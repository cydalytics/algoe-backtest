"""CPU turnover forecast for the air-gapped box.

Fits on the 5-minute panel the backtest already builds. No GPU, no network,
and the parquet never leaves the machine: train and score happen where the
data already is.

Unit: one selection (pool, line, combination) in one 5-minute bucket.
Target: that selection's turnover in the bucket.

The number to beat is persistence (next 5 min = last 5 min), the same
``f_persist`` the backtest reports as WAPE. The fit blends toward persistence
with a weight picked on days it was not fitted on; a weight of 0 means the
shipped forecast is persistence.

Two parts, both numpy on CPU:

    p   = P(money next bucket)        weighted ridge-logistic
    mu  = log1p(turnover | money)     weighted ridge on the money rows
    yhat = a * p * expm1(mu) + (1 - a) * lag1

Splitting them lets a selection that was quiet last bucket still get a
forecast: the level comes from its line, pool, match and history, not from
its own lag.

    python -m src.backtest.parquet.forecast selftest
    python -m src.backtest.parquet.forecast fit --data-dir "S:\\...\\Raw_Data"

In the USB bundle the module name is ``backtest.forecast``.
"""
from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .config import DEFAULT_POOLS
from .panel import _parse_line

# Pools we one-hot. A pool outside this list keeps every dummy at 0.
POOLS = ("HILO", "HDC", "CHLO", "CHDC", "HAD", "FHAD", "FHILO", "FHDC", "SGA")
# All-up / banker. Pre-match rows stay in; in-play rows are dropped.
BANKER_POOLS = {"SGA"}

# The investment feed has ticket_count and turnover, not a customer id.
# Buyers are tickets. Large-vs-small is average stake (turnover / tickets).
FEATURES = (
    "log1p_lag1", "log1p_lag2", "log1p_lag3", "d1", "d2", "lag_ratio", "ma3",
    "any_lag1", "any_lag3",
    "log1p_tickets", "log_avg_stake",
    "share_line", "share_pool", "share_match", "share_line_d1", "share_match_d1",
    "is_best_line", "prob_gap", "odds_gap", "dist_best_prob", "n_lines", "best_line_moved",
    "line_gap", "abs_sup", "tg_level", "log_odds",
    "inplay_f", "mins_to_ko", "match_minute",
    "since_goal", "since_corner", "since_yellow",
    "post_goal_5", "post_goal_15", "post_corner_5", "prematch",
    "n_live", "n_board", "other_lag_share",
    "log1p_match", "log1p_pool_lag", "log1p_line_lag", "log1p_so_far", "log1p_board",
    "dow", "hour", "weekend",
    "log1p_prior_pool", "log1p_prior_hour", "log1p_prior_league", "log1p_prior_tickets",
    "prior_hit_pool", "hot_vs_pool",
)

# Weight on the model against persistence. 0 = pure persistence.
ALPHAS = (0.0, 0.25, 0.5, 0.75, 1.0)
RIDGE = 1.0
LOGIT_STEPS = 8


def wape(y, yhat) -> float:
    y = np.asarray(y, dtype="float64")
    yhat = np.asarray(yhat, dtype="float64")
    denom = float(np.abs(y).sum())
    if denom <= 0 or not np.isfinite(denom):
        return float("nan")
    return float(np.abs(y - yhat).sum()) / denom


def _num(frame: pd.DataFrame, col: str, default: float = 0.0) -> np.ndarray:
    if col not in frame.columns:
        return np.full(len(frame), default, dtype="float64")
    return pd.to_numeric(frame[col], errors="coerce").fillna(default).to_numpy(dtype="float64")


def _line_gap(frame: pd.DataFrame) -> np.ndarray:
    """Actual line minus the parameter the line should sit near.

    Totals use TG (corners use CTG). Handicaps use SUP (corners use CSUP).
    The identity of the line is not a feature; the gap is.
    """
    if "line_label" not in frame.columns:
        return np.zeros(len(frame), dtype="float64")
    labels = frame["line_label"].astype("string")
    parsed = {lab: _parse_line(lab) for lab in labels.dropna().unique()}
    line = labels.map(parsed).astype("float64").to_numpy()
    name = frame["pool"].astype(str).str.upper()
    corner = name.str.startswith(("C", "FC", "ETC")).to_numpy()
    handicap = (name.str.contains("HDC") | name.str.endswith("DC")).to_numpy()
    tg = np.where(corner, _num(frame, "ctg", np.nan), _num(frame, "tg", np.nan))
    sup = np.where(corner, _num(frame, "csup", np.nan), _num(frame, "sup", np.nan))
    expected = np.where(handicap, sup, tg)
    gap = line - expected
    return np.where(np.isfinite(gap), gap, 0.0)


class History:
    """Means from days already finished. The day being featurised is not in here."""

    def __init__(self):
        self.pool: Dict = {}
        self.hour: Dict = {}
        self.league: Dict = {}
        self.tickets: Dict = {}
        self.hit: Dict = {}

    @staticmethod
    def _mean(store, key) -> float:
        total, n = store.get(key, (0.0, 0))
        return total / n if n else 0.0

    @staticmethod
    def _add(store, key, total, n) -> None:
        prev, pn = store.get(key, (0.0, 0))
        store[key] = (prev + float(total), pn + int(n))

    def update(self, frame: pd.DataFrame) -> None:
        """Fold in one finished day. Means are per offered selection-bucket."""
        if frame is None or frame.empty:
            return
        work = pd.DataFrame({
            "pool": frame["pool"].astype(str).to_numpy(),
            "hour": frame["clock_hour"].to_numpy(),
            "y": frame["y"].to_numpy(dtype="float64"),
            "hit": (frame["y"].to_numpy(dtype="float64") > 0).astype("float64"),
            "tickets": frame["tickets_y"].to_numpy(dtype="float64"),
        })
        for store, key, col in ((self.pool, "pool", "y"), (self.hour, "hour", "y"),
                                (self.tickets, "pool", "tickets"), (self.hit, "pool", "hit")):
            agg = work.groupby(key, observed=True)[col].agg(["sum", "size"])
            for k, total, n in agg.itertuples():
                self._add(store, k, total, n)
        match = frame.groupby(["league", "match_id"], observed=True)["y"].sum()
        for (league, _), money in match.items():
            self._add(self.league, str(league), float(money), 1)


def _share(num: pd.Series, den: pd.Series) -> np.ndarray:
    den_v = den.to_numpy(dtype="float64")
    out = np.zeros(len(num), dtype="float64")
    ok = den_v > 0
    out[ok] = num.to_numpy(dtype="float64")[ok] / den_v[ok]
    return out


def _attach_books(s: pd.DataFrame) -> pd.DataFrame:
    """Shares, best line, and who else is on the board.

    Runs on every offered row, before anything is filtered on what the bucket
    then took, so no count or rank here depends on the label.
    """
    odds = pd.to_numeric(s["odds"], errors="coerce") if "odds" in s.columns else pd.Series(np.nan, index=s.index)
    p = pd.to_numeric(s["p_true"], errors="coerce") if "p_true" in s.columns else pd.Series(np.nan, index=s.index)
    p = p.where(p.notna() & (p > 0) & (p < 1), 1.0 / odds.where(odds > 1))
    s["p_side"] = p.fillna(0.5)
    s["odds"] = odds.fillna(2.0)

    line_key = ["match_id", "pool", "bucket", "line_key"]
    pool_key = ["match_id", "pool", "bucket"]
    match_key = ["match_id", "bucket"]

    g = s.groupby(line_key, observed=True)
    s["prob_gap"] = g["p_side"].transform("max") - g["p_side"].transform("min")
    s["odds_gap"] = g["odds"].transform("max") - g["odds"].transform("min")
    lines = (s.groupby(line_key, observed=True)
             .agg(prob_gap=("prob_gap", "first"), odds_gap=("odds_gap", "first"))
             .reset_index())
    lines["rank_key"] = lines["prob_gap"] + lines["odds_gap"] * 1e-4
    best_idx = lines.groupby(pool_key, observed=True)["rank_key"].idxmin()
    best = lines.loc[best_idx, pool_key + ["line_key"]].rename(columns={"line_key": "best_line"})
    best = best.sort_values("bucket")
    prev = best.groupby(["match_id", "pool"], observed=True)["best_line"].shift(1)
    best["best_line_moved"] = (prev.notna() & (best["best_line"] != prev)).astype("float64")
    s = s.merge(best, on=pool_key, how="left")
    s["is_best_line"] = (s["line_key"] == s["best_line"]).astype("float64")
    s["dist_best_prob"] = s["prob_gap"] - s.groupby(pool_key, observed=True)["prob_gap"].transform("min")
    s["n_lines"] = s.groupby(pool_key, observed=True)["line_key"].transform("nunique").astype("float64")
    s["best_line_moved"] = s["best_line_moved"].fillna(0.0)

    line_lag = s.groupby(line_key, observed=True)["lag1"].transform("sum")
    pool_lag = s.groupby(pool_key, observed=True)["lag1"].transform("sum")
    match_lag = s.groupby(match_key, observed=True)["lag1"].transform("sum")
    board_lag = s.groupby("bucket", observed=True)["lag1"].transform("sum")
    s["share_line"] = _share(s["lag1"], line_lag)
    s["share_pool"] = _share(s["lag1"], pool_lag)
    s["share_match"] = _share(s["lag1"], match_lag)
    prev_line = _share(s["lag2"], s.groupby(line_key, observed=True)["lag2"].transform("sum"))
    prev_match = _share(s["lag2"], s.groupby(match_key, observed=True)["lag2"].transform("sum"))
    s["share_line_d1"] = s["share_line"] - prev_line
    s["share_match_d1"] = s["share_match"] - prev_match
    s["log1p_line_lag"] = np.log1p(line_lag.to_numpy(dtype="float64"))
    s["log1p_pool_lag"] = np.log1p(pool_lag.to_numpy(dtype="float64"))
    s["log1p_match"] = np.log1p(match_lag.to_numpy(dtype="float64"))
    s["log1p_board"] = np.log1p(board_lag.to_numpy(dtype="float64"))
    s["other_lag_share"] = 1.0 - _share(match_lag, board_lag)
    s["n_board"] = s.groupby("bucket", observed=True)["match_id"].transform("nunique").astype("float64")
    live = s.loc[s["inplay"]].groupby("bucket")["match_id"].nunique()
    s["n_live"] = s["bucket"].map(live).fillna(0.0).astype("float64")

    # money the match has taken up to the end of the previous bucket
    per_bucket = s.groupby(match_key, observed=True)["lag1"].sum()
    so_far = per_bucket.groupby(level=0).cumsum()
    s["log1p_so_far"] = np.log1p(
        pd.MultiIndex.from_frame(s[match_key]).map(so_far).to_numpy(dtype="float64"))
    return s


def prepare(frame: pd.DataFrame, history: Optional[History] = None) -> pd.DataFrame:
    """Every offered selection-bucket, with features known before the bucket.

    Nothing here filters on this bucket's turnover. In-play SGA is removed.
    ``active`` marks the rows the backtest scores (money this bucket or the
    last) so the two reports line up; it is a scoring cut, never a feature.
    """
    if frame is None or frame.empty or "turnover" not in frame.columns:
        return pd.DataFrame()
    f = frame
    pool = (f["pool_name"].astype("string").fillna("").str.upper()
            if "pool_name" in f.columns else pd.Series("", index=f.index))
    inplay = (f["is_inplay"].fillna(False).astype(bool)
              if "is_inplay" in f.columns else pd.Series(False, index=f.index))
    offer = ~(pool.isin(BANKER_POOLS) & inplay)
    if not bool(offer.any()):
        return pd.DataFrame()
    s = f.loc[offer].copy()
    s["src_index"] = s.index.to_numpy()
    s["y"] = np.clip(_num(s, "turnover"), 0, None)
    s["tickets_y"] = np.clip(_num(s, "tickets"), 0, None)
    for k in (1, 2, 3):
        s[f"lag{k}"] = np.clip(_num(s, f"lag{k}"), 0, None)
    s["pool"] = pool.loc[offer].to_numpy()
    s["inplay"] = inplay.loc[offer].to_numpy()
    if "match_id" not in s.columns:
        s["match_id"] = -1
    s["league"] = (s["league_code"].astype("string").fillna("unknown")
                   if "league_code" in s.columns else "unknown")
    line_src = s["line_id"] if "line_id" in s.columns else s.get("line_label", pd.Series("0", index=s.index))
    s["line_key"] = line_src.astype("string").fillna("0")
    s["bucket"] = pd.to_datetime(s["bucket"])
    s = _attach_books(s)

    bucket = s["bucket"]
    s["clock_hour"] = bucket.dt.hour.astype(int)
    s["dow"] = bucket.dt.dayofweek.astype("float64") / 6.0
    s["hour"] = s["clock_hour"].astype("float64") / 23.0
    s["weekend"] = (bucket.dt.dayofweek >= 5).astype("float64")

    log1 = np.log1p(s["lag1"])
    log2 = np.log1p(s["lag2"])
    log3 = np.log1p(s["lag3"])
    tickets = np.clip(_num(s, "tickets_lag1"), 0, None)
    s["log1p_lag1"] = log1
    s["log1p_lag2"] = log2
    s["log1p_lag3"] = log3
    s["d1"] = log1 - log2
    s["d2"] = log2 - log3
    s["lag_ratio"] = np.clip(s["lag1"] / np.maximum(s["lag2"], 1.0), 0, 20)
    s["ma3"] = (log1 + log2 + log3) / 3.0
    s["any_lag1"] = (s["lag1"] > 0).astype("float64")
    s["any_lag3"] = ((s["lag1"] + s["lag2"] + s["lag3"]) > 0).astype("float64")
    s["log1p_tickets"] = np.log1p(tickets)
    s["log_avg_stake"] = np.log1p(s["lag1"] / np.maximum(tickets, 1.0))
    s["log_odds"] = np.log(np.clip(s["odds"].to_numpy(dtype="float64"), 1.01, None))
    s["inplay_f"] = s["inplay"].astype("float64")
    s["mins_to_ko"] = np.clip(_num(s, "mins_to_ko"), -30, 360) / 360.0
    s["match_minute"] = np.clip(_num(s, "match_minute"), 0, 120) / 90.0
    s["since_goal"] = np.clip(_num(s, "minutes_since_goal", 999.0), 0, 90) / 90.0
    s["since_corner"] = np.clip(_num(s, "minutes_since_corner", 999.0), 0, 90) / 90.0
    s["since_yellow"] = np.clip(_num(s, "minutes_since_yellow", 999.0), 0, 90) / 90.0
    window = (s["event_window"].astype("string").fillna("quiet")
              if "event_window" in s.columns else pd.Series("quiet", index=s.index, dtype="string"))
    s["event_window"] = window
    s["post_goal_5"] = (window == "post_goal_5").astype("float64")
    s["post_goal_15"] = (window == "post_goal_15").astype("float64")
    s["post_corner_5"] = (window == "post_corner_5").astype("float64")
    s["prematch"] = (window == "prematch").astype("float64")
    s["line_gap"] = np.clip(_line_gap(s), -8, 8)
    corner = s["pool"].astype(str).str.startswith(("C", "FC", "ETC")).to_numpy()
    s["abs_sup"] = np.abs(np.where(corner, _num(s, "csup"), _num(s, "sup")))
    s["tg_level"] = np.where(corner, _num(s, "ctg"), _num(s, "tg"))

    hist = history or History()
    pool_s = s["pool"].astype(str)
    league_s = s["league"].astype(str)
    hour_s = s["clock_hour"]

    def lookup(store, keys):
        table = {k: hist._mean(store, k) for k in keys.unique()}
        return keys.map(table).to_numpy(dtype="float64")

    s["log1p_prior_pool"] = np.log1p(lookup(hist.pool, pool_s))
    s["log1p_prior_hour"] = np.log1p(lookup(hist.hour, hour_s))
    s["log1p_prior_league"] = np.log1p(lookup(hist.league, league_s))
    s["log1p_prior_tickets"] = np.log1p(lookup(hist.tickets, pool_s))
    s["prior_hit_pool"] = lookup(hist.hit, pool_s)
    s["hot_vs_pool"] = s["log1p_lag1"] - s["log1p_prior_pool"]

    s["active"] = (s["y"] > 0) | (s["lag1"] > 0)
    keep = list(dict.fromkeys(
        ["src_index", "y", "lag1", "tickets_y", "active", "bucket", "pool", "inplay",
         "event_window", "match_id", "league", "clock_hour"] + list(FEATURES)))
    return s[keep].reset_index(drop=True)


def design(frame: pd.DataFrame) -> Tuple[np.ndarray, List[str]]:
    """Numeric design matrix. Categories are fixed so train and score align."""
    cols, names = [], []
    for name in FEATURES:
        names.append(name)
        cols.append(_num(frame, name))
    pool = frame["pool"].astype(str) if "pool" in frame.columns else pd.Series("", index=frame.index)
    for name in POOLS:
        names.append("pool_" + name)
        cols.append((pool == name).to_numpy(dtype="float64"))
    x = np.column_stack(cols)
    return np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0), names


def weighted_sample(frame: pd.DataFrame, cap: int, rng: np.random.Generator) -> pd.DataFrame:
    """Keep money and post-goal rows more often; carry 1/p so fits stay unbiased."""
    if frame.empty:
        return frame
    n = len(frame)
    if n <= cap:
        out = frame.copy()
        out["w"] = 1.0
        return out
    score = np.ones(n, dtype="float64")
    score[frame["y"].to_numpy() > 0] = 4.0
    score[frame["event_window"].astype(str).str.startswith("post_goal").to_numpy()] *= 3.0
    prob = np.minimum(score * cap / score.sum(), 1.0)
    take = rng.random(n) < prob
    out = frame.loc[take].copy()
    out["w"] = 1.0 / prob[take]
    return out


def uniform_sample(frame: pd.DataFrame, cap: int, rng: np.random.Generator) -> pd.DataFrame:
    if len(frame) <= cap:
        return frame
    take = rng.choice(len(frame), size=cap, replace=False)
    return frame.iloc[np.sort(take)]


def _standardize(x: np.ndarray, w: np.ndarray):
    ws = w / w.sum()
    mean = ws @ x
    std = np.sqrt(ws @ (x - mean) ** 2)
    std[std < 1e-8] = 1.0
    return mean, std


def _ridge(z: np.ndarray, t: np.ndarray, w: np.ndarray, lam: float) -> np.ndarray:
    """Weighted ridge with an unpenalised intercept. Returns [b0, b...]."""
    a = np.column_stack([np.ones(len(z)), z])
    aw = a * w[:, None]
    pen = lam * np.eye(a.shape[1])
    pen[0, 0] = 0.0
    return np.linalg.solve(a.T @ aw + pen, aw.T @ t)


def _logistic(z: np.ndarray, t: np.ndarray, w: np.ndarray, lam: float) -> np.ndarray:
    """Weighted ridge-logistic by Newton steps."""
    a = np.column_stack([np.ones(len(z)), z])
    pen = lam * np.eye(a.shape[1])
    pen[0, 0] = 0.0
    rate = float(np.clip((w * t).sum() / w.sum(), 1e-4, 1 - 1e-4))
    beta = np.zeros(a.shape[1])
    beta[0] = np.log(rate / (1 - rate))
    for _ in range(LOGIT_STEPS):
        p = 1.0 / (1.0 + np.exp(-np.clip(a @ beta, -30, 30)))
        grad = a.T @ (w * (p - t)) + pen @ beta
        hess = (a * (w * p * (1 - p))[:, None]).T @ a + pen
        beta = beta - np.linalg.solve(hess + 1e-9 * np.eye(len(beta)), grad)
    return beta


class CpuForecast:
    """Hit probability times size, blended toward persistence by alpha."""

    def __init__(self):
        self.names: List[str] = []
        self.mean = None
        self.std = None
        self.hit = None
        self.size = None
        self.size_cap = 20.0
        self.alpha = 0.0
        self.kind = "persist"

    def fit(self, frame: pd.DataFrame) -> dict:
        if frame.empty:
            raise ValueError("no rows to fit")
        x, names = design(frame)
        w = frame["w"].to_numpy(dtype="float64") if "w" in frame.columns else np.ones(len(frame))
        return self.fit_arrays(x, frame["y"].to_numpy(dtype="float64"), w, names)

    def fit_arrays(self, x: np.ndarray, y: np.ndarray, w: np.ndarray, names: List[str]) -> dict:
        x = np.asarray(x, dtype="float64")
        y = np.asarray(y, dtype="float64")
        w = np.asarray(w, dtype="float64")
        self.names = list(names)
        self.mean, self.std = _standardize(x, w)
        z = (x - self.mean) / self.std
        money = y > 0
        if money.sum() < 10 or (~money).sum() < 10:
            raise ValueError("need both money and quiet rows to fit")
        self.hit = _logistic(z, money.astype("float64"), w, RIDGE)
        target = np.log1p(y[money])
        self.size = _ridge(z[money], target, w[money], RIDGE)
        self.size_cap = float(np.max(target)) + 1.0
        self.kind = "hurdle"
        return {"rows": int(len(y)), "money_rows": int(money.sum()), "kind": self.kind}

    def _parts(self, x: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        a = np.column_stack([np.ones(len(x)), (np.asarray(x, dtype="float64") - self.mean) / self.std])
        p = 1.0 / (1.0 + np.exp(-np.clip(a @ self.hit, -30, 30)))
        mu = np.clip(a @ self.size, 0.0, self.size_cap)
        return p, mu

    def _x(self, frame: pd.DataFrame) -> np.ndarray:
        x, names = design(frame)
        if names != self.names:
            raise ValueError("feature columns drifted between fit and score")
        return x

    def model_x(self, x: np.ndarray) -> np.ndarray:
        p, mu = self._parts(x)
        return p * np.expm1(mu)

    def model_only(self, frame: pd.DataFrame) -> np.ndarray:
        return self.model_x(self._x(frame))

    def predict(self, frame: pd.DataFrame, alpha: Optional[float] = None) -> np.ndarray:
        if frame.empty:
            return np.zeros(0, dtype="float64")
        lag = np.clip(frame["lag1"].to_numpy(dtype="float64"), 0, None)
        if self.kind == "persist" or self.hit is None:
            return lag
        a = self.alpha if alpha is None else float(alpha)
        if a <= 0:
            return lag
        return a * self.model_only(frame) + (1.0 - a) * lag

    def pick_alpha(self, inner: pd.DataFrame) -> dict:
        """Choose alpha on days the coefficients never saw, on unweighted rows."""
        if inner.empty or self.hit is None:
            self.alpha = 0.0
            return {"alpha": 0.0, "inner_rows": 0, "scores": {}}
        return self.pick_alpha_arrays(self._x(inner), inner["y"].to_numpy(dtype="float64"),
                                      inner["lag1"].to_numpy(dtype="float64"))

    def pick_alpha_arrays(self, x: np.ndarray, y: np.ndarray, lag: np.ndarray) -> dict:
        y = np.asarray(y, dtype="float64")
        lag = np.clip(np.asarray(lag, dtype="float64"), 0, None)
        if len(y) == 0 or self.hit is None:
            self.alpha = 0.0
            return {"alpha": 0.0, "inner_rows": 0, "scores": {}}
        m = self.model_x(x)
        scores = {}
        best_a, best = 0.0, wape(y, lag)
        for a in ALPHAS:
            s = wape(y, a * m + (1.0 - a) * lag)
            scores[str(a)] = s
            if np.isfinite(s) and s < best - 1e-6:
                best, best_a = s, a
        self.alpha = float(best_a)
        return {"alpha": self.alpha, "inner_rows": int(len(y)), "scores": scores}

    def to_dict(self) -> dict:
        def arr(v):
            return None if v is None else np.asarray(v).tolist()
        return {"names": self.names, "mean": arr(self.mean), "std": arr(self.std),
                "hit": arr(self.hit), "size": arr(self.size), "size_cap": self.size_cap,
                "alpha": self.alpha, "kind": self.kind}

    @classmethod
    def from_dict(cls, payload: dict) -> "CpuForecast":
        m = cls()
        m.names = list(payload.get("names") or [])
        for key in ("mean", "std", "hit", "size"):
            v = payload.get(key)
            setattr(m, key, None if v is None else np.asarray(v, dtype="float64"))
        m.size_cap = float(payload.get("size_cap") or 20.0)
        m.alpha = float(payload.get("alpha") or 0.0)
        m.kind = payload.get("kind") or "persist"
        return m


class WalkForward:
    """Day-by-day forecast for the workbench. Day N only ever sees days before N.

    Feed days oldest first: ``predict`` then ``observe``. Until ``warmup_days``
    have been observed there is no fit and the forecast is NaN, so a compare
    never scores a model on days it could not have been run on. After that
    the fit is refreshed every ``refit_every`` days on an expanding window;
    the last ``inner_days`` of that window only pick alpha.
    """

    def __init__(self, warmup_days: int = 14, inner_days: int = 7, refit_every: int = 7,
                 per_day: int = 6000, inner_per_day: int = 8000, seed: int = 7):
        self.warmup_days = int(warmup_days)
        self.inner_days = int(inner_days)
        self.refit_every = max(int(refit_every), 1)
        self.per_day = int(per_day)
        self.inner_per_day = int(inner_per_day)
        self.rng = np.random.default_rng(seed)
        self.hist = History()
        self.model: Optional[CpuForecast] = None
        self.names: List[str] = []
        self._days: List[dict] = []
        self._since_fit = 0
        self.log: List[dict] = []

    def predict(self, panel: pd.DataFrame) -> Tuple[np.ndarray, pd.DataFrame]:
        """Forecast aligned to ``panel`` rows. NaN where there is no fit yet
        or where the row is not offered (in-play SGA)."""
        n = 0 if panel is None else len(panel)
        out = np.full(n, np.nan, dtype="float64")
        slim = prepare(panel if panel is not None else pd.DataFrame(), self.hist)
        if slim.empty or self.model is None:
            return out, slim
        pos = pd.Index(panel.index).get_indexer(slim["src_index"].to_numpy())
        ok = pos >= 0
        out[pos[ok]] = self.model.predict(slim)[ok]
        return out, slim

    def observe(self, slim: pd.DataFrame, day=None) -> None:
        if slim is None or slim.empty:
            return
        self.hist.update(slim)
        fit = weighted_sample(slim, self.per_day, self.rng)
        inner = uniform_sample(slim, self.inner_per_day, self.rng)
        xf, names = design(fit)
        xi, _ = design(inner)
        self.names = names
        self._days.append({
            "day": None if day is None else "{:%Y-%m-%d}".format(pd.Timestamp(day)),
            "xf": xf.astype("float32"), "yf": fit["y"].to_numpy(dtype="float32"),
            "wf": fit["w"].to_numpy(dtype="float32"),
            "xi": xi.astype("float32"), "yi": inner["y"].to_numpy(dtype="float32"),
            "li": inner["lag1"].to_numpy(dtype="float32"),
        })
        self._since_fit += 1
        if len(self._days) >= self.warmup_days and (
                self.model is None or self._since_fit >= self.refit_every):
            self._refit()

    def _refit(self) -> None:
        inner_n = min(self.inner_days, max(len(self._days) - 1, 0))
        fit_days = self._days[:len(self._days) - inner_n]
        inner_days = self._days[len(self._days) - inner_n:]
        x = np.concatenate([d["xf"] for d in fit_days]).astype("float64")
        y = np.concatenate([d["yf"] for d in fit_days]).astype("float64")
        w = np.concatenate([d["wf"] for d in fit_days]).astype("float64")
        model = CpuForecast()
        try:
            info = model.fit_arrays(x, y, w, self.names)
        except (ValueError, np.linalg.LinAlgError) as exc:
            self.log.append({"after": self._days[-1]["day"], "error": str(exc)})
            return
        if inner_days:
            alpha = model.pick_alpha_arrays(
                np.concatenate([d["xi"] for d in inner_days]).astype("float64"),
                np.concatenate([d["yi"] for d in inner_days]),
                np.concatenate([d["li"] for d in inner_days]))
        else:
            model.alpha = 0.0
            alpha = {"alpha": 0.0, "inner_rows": 0, "scores": {}}
        self.model = model
        self._since_fit = 0
        self.log.append({
            "after": self._days[-1]["day"],
            "fit_days": len(fit_days), "inner_days": len(inner_days),
            "rows": info["rows"], "money_rows": info["money_rows"],
            "alpha": alpha["alpha"], "inner_scores": alpha["scores"],
        })

    def summary(self) -> dict:
        return {
            "warmup_days": self.warmup_days, "inner_days": self.inner_days,
            "refit_every": self.refit_every, "refits": self.log,
        }


def score_frame(model: CpuForecast, frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {"rows": 0, "turnover": 0.0, "sae_persist": 0.0, "sae_model": 0.0,
                "wape_persist": None, "wape_model": None}
    y = frame["y"].to_numpy(dtype="float64")
    persist = np.clip(frame["lag1"].to_numpy(dtype="float64"), 0, None)
    pred = model.predict(frame)
    return {
        "rows": int(len(frame)),
        "turnover": float(y.sum()),
        "sae_persist": float(np.abs(y - persist).sum()),
        "sae_model": float(np.abs(y - pred).sum()),
        "wape_persist": wape(y, persist),
        "wape_model": wape(y, pred),
    }


def _slice_rows(slim: pd.DataFrame) -> List[dict]:
    rows = []
    if slim.empty:
        return rows
    slim = slim.copy()
    slim["phase"] = np.where(slim["inplay"], "inplay", "prematch")
    slim["lag_state"] = np.where(slim["lag1"] > 0, "lag>0", "lag=0")
    for dim in ("phase", "pool", "event_window", "lag_state"):
        agg = slim.assign(ep=(slim["y"] - slim["p"]).abs(), em=(slim["y"] - slim["m"]).abs()) \
            .groupby(dim, observed=True).agg(rows=("y", "size"), turnover=("y", "sum"),
                                             ep=("ep", "sum"), em=("em", "sum"))
        for value, r in agg.iterrows():
            if r["turnover"] <= 0:
                continue
            rows.append({"dim": dim, "value": str(value), "rows": int(r["rows"]),
                         "turnover": float(r["turnover"]),
                         "wape_persist": float(r["ep"] / r["turnover"]),
                         "wape_model": float(r["em"] / r["turnover"])})
    return rows


def _html(report: dict) -> str:
    h = report["holdout"]
    beat = h.get("beats_persist")
    headline = "beats persistence" if beat else "does not beat persistence"
    body = []
    for row in report.get("slices") or []:
        body.append(
            "<tr><td>{dim}</td><td>{value}</td><td>{rows:,}</td>"
            "<td>{turnover:,.0f}</td><td>{wape_persist:.1%}</td><td>{wape_model:.1%}</td></tr>".format(**row))

    def pct(v):
        return "{:.1%}".format(v) if v is not None else "n/a"

    return """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Turnover forecast</title>
<style>
body {{ font: 15px/1.45 Segoe UI, sans-serif; margin: 2rem; color: #1a1a1a; }}
table {{ border-collapse: collapse; }}
td, th {{ border-bottom: 1px solid #ddd; padding: 4px 10px; text-align: right; }}
td:nth-child(-n+2), th:nth-child(-n+2) {{ text-align: left; }}
.good {{ color: #0a7a32; }} .bad {{ color: #a32020; }}
</style></head><body>
<h1>Turnover forecast — {headline}</h1>
<p>Hold-out {start} → {end}, every offered selection-bucket. In-play SGA is excluded.
Fitted on this machine on CPU; nothing was exported.</p>
<p>Persistence WAPE <b>{pw}</b> · model WAPE <b class="{cls}">{mw}</b>
· alpha {alpha} · {rows:,} rows · turnover {money:,.0f}</p>
<p>Backtest cut (money this bucket or the last): persistence {apw} · model {amw}</p>
<table>
<tr><th>cut</th><th>value</th><th>rows</th><th>turnover</th><th>persist</th><th>model</th></tr>
{rows_html}
</table>
</body></html>
""".format(
        headline=headline, start=report["holdout_start"], end=report["holdout_end"],
        pw=pct(h.get("wape_persist")), mw=pct(h.get("wape_model")),
        apw=pct(h.get("active_wape_persist")), amw=pct(h.get("active_wape_model")),
        cls="good" if beat else "bad", alpha=report["alpha"]["alpha"],
        rows=h.get("rows") or 0, money=h.get("turnover") or 0,
        rows_html="\n".join(body) or "<tr><td colspan='6'>no slices</td></tr>")


def run_fit(cfg, out_dir: Path, holdout_days: int, inner_days: int,
            per_day: int, inner_per_day: int) -> dict:
    from .cache import PanelCache, day_list, resolve_cache_root

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = PanelCache(cfg, resolve_cache_root(cfg))
    days = day_list(cfg.start_date, cfg.end_date)
    if len(days) < holdout_days + inner_days + 1:
        raise SystemExit("need at least {} days in the window; got {}".format(
            holdout_days + inner_days + 1, len(days)))
    missing = [d for d in days if not cache.has_day(d)]
    print("[cache] {}".format(cache.root), flush=True)
    print("[cache] {} cached, {} to build".format(len(days) - len(missing), len(missing)), flush=True)
    if missing:
        cache.build(missing, progress=lambda info: print(
            "[{}] {}".format(info.get("stage"), info.get("day") or info.get("message", "")),
            flush=True))

    hold_start = days[-holdout_days]
    inner_start = days[-holdout_days - inner_days]
    rng = np.random.default_rng(7)
    hist = History()
    fit_parts, inner_parts = [], []
    for day in days:
        if day >= hold_start:
            break
        raw = cache.load_day(day)
        slim = prepare(raw if raw is not None else pd.DataFrame(), hist)
        if slim.empty:
            continue
        hist.update(slim)
        if day < inner_start:
            fit_parts.append(weighted_sample(slim, per_day, rng))
            tag = "fit"
        else:
            inner_parts.append(uniform_sample(slim, inner_per_day, rng))
            tag = "inner"
        print("[{}] {} offered={:,} money={:,}".format(
            tag, day.date(), len(slim), int((slim["y"] > 0).sum())), flush=True)
    if not fit_parts:
        raise SystemExit("training window has no rows")
    model = CpuForecast()
    fit_info = model.fit(pd.concat(fit_parts, ignore_index=True))
    alpha_info = model.pick_alpha(
        pd.concat(inner_parts, ignore_index=True) if inner_parts else pd.DataFrame())
    print("[fit] {} rows={:,} money={:,} alpha={}".format(
        fit_info["kind"], fit_info["rows"], fit_info["money_rows"], alpha_info["alpha"]), flush=True)

    daily, slim_parts = [], []
    tot = {"sp": 0.0, "sm": 0.0, "y": 0.0, "n": 0, "asp": 0.0, "asm": 0.0, "ay": 0.0}
    for day in days:
        if day < hold_start:
            continue
        raw = cache.load_day(day)
        slim = prepare(raw if raw is not None else pd.DataFrame(), hist)
        if slim.empty:
            daily.append({"day": "{:%Y-%m-%d}".format(day), "rows": 0})
            continue
        hist.update(slim)
        sc = score_frame(model, slim)
        act = slim[slim["active"]]
        asc = score_frame(model, act)
        sc["day"] = "{:%Y-%m-%d}".format(day)
        sc["active_wape_persist"] = asc["wape_persist"]
        sc["active_wape_model"] = asc["wape_model"]
        daily.append(sc)
        tot["sp"] += sc["sae_persist"]
        tot["sm"] += sc["sae_model"]
        tot["y"] += sc["turnover"]
        tot["n"] += sc["rows"]
        tot["asp"] += asc["sae_persist"]
        tot["asm"] += asc["sae_model"]
        tot["ay"] += asc["turnover"]
        slim_parts.append(pd.DataFrame({
            "y": slim["y"].to_numpy(), "p": slim["lag1"].to_numpy(),
            "m": model.predict(slim), "inplay": slim["inplay"].to_numpy(),
            "pool": slim["pool"].to_numpy(), "event_window": slim["event_window"].astype(str).to_numpy(),
            "lag1": slim["lag1"].to_numpy()}))
        print("[hold] {} persist={:.1%} model={:.1%}".format(
            day.date(), sc["wape_persist"] or float("nan"), sc["wape_model"] or float("nan")), flush=True)

    hold = {
        "rows": int(tot["n"]), "turnover": tot["y"],
        "wape_persist": tot["sp"] / tot["y"] if tot["y"] else None,
        "wape_model": tot["sm"] / tot["y"] if tot["y"] else None,
        "active_wape_persist": tot["asp"] / tot["ay"] if tot["ay"] else None,
        "active_wape_model": tot["asm"] / tot["ay"] if tot["ay"] else None,
    }
    ok = hold["wape_persist"] is not None and hold["wape_model"] is not None
    hold["beats_persist"] = bool(ok and hold["wape_model"] < hold["wape_persist"] - 1e-4)
    hold["lift"] = (hold["wape_persist"] - hold["wape_model"]) if ok else None

    slices = _slice_rows(pd.concat(slim_parts, ignore_index=True) if slim_parts else pd.DataFrame())
    report = {
        "fit": fit_info, "alpha": alpha_info, "model": model.to_dict(), "holdout": hold,
        "fit_end": "{:%Y-%m-%d}".format(inner_start - pd.Timedelta(days=1)),
        "inner_start": "{:%Y-%m-%d}".format(inner_start),
        "holdout_start": "{:%Y-%m-%d}".format(hold_start),
        "holdout_end": "{:%Y-%m-%d}".format(days[-1]),
        "slices": slices, "daily": daily, "cache": str(cache.root),
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (out_dir / "report.html").write_text(_html(report), encoding="utf-8")
    pd.DataFrame(daily).to_csv(out_dir / "daily.csv", index=False)
    pd.DataFrame(slices).to_csv(out_dir / "by_slice.csv", index=False)
    with (out_dir / "model.pkl").open("wb") as fh:
        pickle.dump(model.to_dict(), fh, protocol=pickle.HIGHEST_PROTOCOL)
    print("[out] {}".format(out_dir / "report.html"), flush=True)
    verdict = "beats" if hold["beats_persist"] else "does not beat"
    print("[result] model WAPE {} {} persistence {}".format(
        "n/a" if hold["wape_model"] is None else "{:.1%}".format(hold["wape_model"]), verdict,
        "n/a" if hold["wape_persist"] is None else "{:.1%}".format(hold["wape_persist"])), flush=True)
    return report


# ------------------------------------------------------------------ selftest
def _synthetic(days: int = 12, seed: int = 3) -> List[pd.DataFrame]:
    """Panel-shaped days where persistence is weak on purpose.

    Money arrives intermittently, so a selection is often quiet in one bucket
    and busy in the next. Its size follows its line (main line vs side line)
    and jumps after a goal. Persistence sees neither.
    """
    rng = np.random.default_rng(seed)
    frames = []
    day0 = pd.Timestamp("2026-07-01")
    books = {
        "HILO": [(1, "2.5", 0.51, 900.0), (2, "3.5", 0.72, 150.0)],
        "HDC": [(1, "-0.5", 0.53, 700.0)],
        "CHLO": [(1, "9.5", 0.52, 300.0)],
        "SGA": [(1, "H", 0.40, 120.0)],
    }
    for d in range(days):
        rows = []
        day = day0 + pd.Timedelta(days=d)
        for m in range(5):
            ko = day + pd.Timedelta(hours=19, minutes=15 * m)
            goal_at = ko + pd.Timedelta(minutes=25)
            for step in range(18):
                bucket = ko - pd.Timedelta(minutes=30) + pd.Timedelta(minutes=5 * step)
                inplay = bucket >= ko
                since_goal = (bucket - goal_at).total_seconds() / 60.0
                post = inplay and 0 <= since_goal <= 5
                for pool, lines in books.items():
                    for line_id, label, p_home, level in lines:
                        for side in (1, 2):
                            jump = 2.4 if post and pool != "SGA" else 1.0
                            lag = level * rng.uniform(0.7, 1.3) if rng.random() < 0.6 else 0.0
                            hit = rng.random() < 0.6
                            y = level * jump * rng.uniform(0.7, 1.3) if hit else 0.0
                            p_side = p_home if side == 1 else 1.0 - p_home
                            rows.append({
                                "bucket": bucket, "match_id": 1000 + m, "league_code": "EPL",
                                "pool_name": pool, "line_label": label, "line_id": line_id,
                                "combination_id": side, "p_true": p_side, "odds": 1.0 / p_side,
                                "turnover": y, "tickets": max(y / 50.0, 0.0),
                                "lag1": lag, "lag2": level * rng.uniform(0.5, 1.5), "lag3": 0.0,
                                "tickets_lag1": lag / 50.0,
                                "is_inplay": inplay,
                                "mins_to_ko": (ko - bucket).total_seconds() / 60.0,
                                "match_minute": max((bucket - ko).total_seconds() / 60.0, 0.0),
                                "minutes_since_goal": since_goal if since_goal >= 0 else np.nan,
                                "event_window": ("post_goal_5" if post else
                                                 ("prematch" if not inplay else "quiet")),
                                "tg": 2.6, "sup": 0.2, "ctg": 9.5, "csup": 0.1,
                            })
        frames.append(pd.DataFrame(rows))
    return frames


def _fail(msg: str) -> None:
    raise SystemExit("[selftest] FAIL: " + msg)


def selftest() -> None:
    frames = _synthetic()

    # 1. features must not move when only this bucket's turnover changes
    base = prepare(frames[0])
    bent = frames[0].copy()
    quiet = bent["lag1"] == 0
    bent.loc[quiet, "turnover"] = bent.loc[quiet, "turnover"] * 7 + 5000.0
    bent.loc[quiet, "tickets"] = 999.0
    moved = prepare(bent)
    if len(base) != len(moved):
        _fail("row set depends on the label")
    xa, _ = design(base)
    xb, _ = design(moved)
    if not np.allclose(xa, xb):
        _fail("features depend on the label")

    # 2. in-play SGA is gone; pre-match SGA stays
    if ((base["pool"] == "SGA") & base["inplay"]).any():
        _fail("in-play SGA was kept")
    if not ((base["pool"] == "SGA") & ~base["inplay"]).any():
        _fail("pre-match SGA was dropped")

    # 3. best line is the most balanced line of HILO
    hilo = base[base["pool"] == "HILO"]
    if set(hilo["is_best_line"].unique()) != {0.0, 1.0}:
        _fail("best line was not marked")

    hist = History()
    rng = np.random.default_rng(1)
    fit_parts, inner_parts, hold_parts = [], [], []
    for i, frame in enumerate(frames):
        slim = prepare(frame, hist)
        hist.update(slim)
        if i < 8:
            fit_parts.append(weighted_sample(slim, 1500, rng))
        elif i < 10:
            inner_parts.append(slim)
        else:
            hold_parts.append(slim)
    if float(hold_parts[0]["log1p_prior_pool"].max()) <= 0:
        _fail("history prior did not accumulate")

    model = CpuForecast()
    model.fit(pd.concat(fit_parts, ignore_index=True))
    info = model.pick_alpha(pd.concat(inner_parts, ignore_index=True))
    hold = pd.concat(hold_parts, ignore_index=True)
    sc = score_frame(model, hold)
    if not (sc["wape_model"] < sc["wape_persist"]):
        _fail("model {:.3f} did not beat persist {:.3f}".format(sc["wape_model"], sc["wape_persist"]))
    if info["alpha"] <= 0:
        _fail("alpha collapsed to persistence")

    # 4. a selection that was quiet last bucket still gets a real forecast
    cold = hold[(hold["lag1"] == 0) & (hold["y"] > 0)]
    got = float(model.predict(cold).sum()) / float(cold["y"].sum())
    if got < 0.25:
        _fail("zero-lag rows get {:.0%} of their money".format(got))

    # 5. reload is exact
    again = CpuForecast.from_dict(model.to_dict())
    if float(np.max(np.abs(model.predict(hold) - again.predict(hold)))) > 1e-6:
        _fail("model reload drifted")

    # 6. walk-forward: NaN during warm-up; a day's own labels never move its forecast
    def walk(days: List[pd.DataFrame]) -> List[np.ndarray]:
        wf = WalkForward(warmup_days=4, inner_days=2, refit_every=3, per_day=1500, inner_per_day=1500)
        preds = []
        for d, frame in enumerate(days):
            pred, slim = wf.predict(frame)
            preds.append(pred)
            wf.observe(slim, pd.Timestamp("2026-07-01") + pd.Timedelta(days=d))
        return preds
    plain = walk(frames)
    if not all(np.isnan(p).all() for p in plain[:4]):
        _fail("walk-forward forecast exists before warm-up")
    if not np.isfinite(plain[-1]).any():
        _fail("walk-forward never produced a forecast")
    bent_days = [f.copy() for f in frames]
    bent_days[-1]["turnover"] = bent_days[-1]["turnover"] * 5 + 1000.0
    if not np.allclose(walk(bent_days)[-1], plain[-1], equal_nan=True):
        _fail("walk-forward forecast used its own day's labels")

    print("[selftest] PASS  persist={:.1%} model={:.1%} alpha={} zero-lag cover={:.0%}".format(
        sc["wape_persist"], sc["wape_model"], info["alpha"], got), flush=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["selftest", "fit"])
    p.add_argument("--data-dir", default=r"S:\Users\Yeung\20260520 Algo E\Raw_Data")
    p.add_argument("--out-dir", default="turnover_out")
    p.add_argument("--cache-dir", default="")
    p.add_argument("--start", default="2026-07-01")
    p.add_argument("--end", default="2026-09-15")
    p.add_argument("--pools", default=",".join(DEFAULT_POOLS),
                   help="same list as the backtest, so its panel cache is reused")
    p.add_argument("--holdout-days", type=int, default=14)
    p.add_argument("--inner-days", type=int, default=7,
                   help="days before the hold-out used only to pick alpha")
    p.add_argument("--per-day", type=int, default=20000, help="weighted fit rows per day")
    p.add_argument("--inner-per-day", type=int, default=30000, help="uniform rows per inner day")
    p.add_argument("--prematch-window-min", type=int, default=360)
    return p


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "selftest":
        selftest()
        return
    from .config import Config
    pools = [s.strip() for s in args.pools.split(",") if s.strip()]
    cfg = Config(
        data_dir=Path(args.data_dir), out_dir=Path(args.out_dir),
        cache_dir=Path(args.cache_dir) if args.cache_dir else None,
        start_date=args.start, end_date=args.end, pools=pools,
        prematch_window_min=args.prematch_window_min, save_facts=False, optimize=False,
    )
    run_fit(cfg, Path(args.out_dir), args.holdout_days, args.inner_days,
            args.per_day, args.inner_per_day)


if __name__ == "__main__":
    main()
