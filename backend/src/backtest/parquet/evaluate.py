"""Streaming evaluation of the panel.

Every statistic is stored as an additive sum keyed by (subset, model, slice), so
a day of facts can be folded in and thrown away. Nothing needs the whole panel
in memory at once; only the scatter/histogram samples are capped.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

import numpy as np
import pandas as pd

TURNOVER_MODELS = {
    "persist": "f_persist",     # the assumption under test: next 5min = last 5min
    "ma3": "f_ma3",
    "ema": "f_ema",
    "zero": "f_zero",
}
BELIEF_MODELS = {
    "p_true": "p_true",         # the assumption under test: 1 / true odds
    "p_sell": "p_sell_norm",    # public odds, normalised - the thing to beat
}
from .bins import sup_bin, tg_bin
from .panel import _parse_line
from .pricing import POOL_KIND

# Every one of these is a column already on the cached panel, or a label
# computed from those columns. Nothing here invents team ratings or a cup flag.
TRADER_DIMS = (
    "phase", "domain", "kind", "side", "line_role", "book", "matchup",
    "clock_bin", "event_window", "odds_bin", "quote_age", "belief_source",
)
SLICE_DIMS = list(TRADER_DIMS) + ["pool_name", "league_code"]

CAL_BINS = np.linspace(0.0, 1.0, 21)
BOOK_BINS = np.linspace(0.90, 1.20, 31)
EPS = 1e-6


class Accumulator:
    def __init__(self, sample_rows: int = 6000, seed: int = 7):
        self.rng = np.random.default_rng(seed)
        self.sample_rows = sample_rows
        self.turnover: Dict[tuple, np.ndarray] = defaultdict(lambda: np.zeros(6))
        self.slices: Dict[tuple, np.ndarray] = defaultdict(lambda: np.zeros(6))
        self.belief: Dict[tuple, np.ndarray] = defaultdict(lambda: np.zeros(8))
        self.belief_cuts: Dict[tuple, np.ndarray] = defaultdict(lambda: np.zeros(8))
        self.cal: Dict[tuple, np.ndarray] = defaultdict(lambda: np.zeros(5))
        self.gm: Dict[tuple, np.ndarray] = defaultdict(lambda: np.zeros(6))
        self.book_hist = np.zeros(len(BOOK_BINS) - 1)
        self.resid: Dict[str, np.ndarray] = defaultdict(
            lambda: np.zeros(len(RESID_BINS) - 1))
        self.daily: List[dict] = []
        self.bucket_level: Dict[str, np.ndarray] = defaultdict(lambda: np.zeros(4))
        self.scatter: List[pd.DataFrame] = []
        self.cal_scatter: List[pd.DataFrame] = []
        self.coverage = {
            "rows": 0, "days": 0, "matches": set(), "pools": set(),
            "turnover": 0.0, "money_rows": 0, "settled_rows": 0,
            # every bucket of one selection shares a single outcome, so the
            # independent sample size for calibration is the selection count
            "settled_keys": set(),
        }

    # ------------------------------------------------------------------ fold
    def update(self, day: pd.Timestamp, f: pd.DataFrame) -> None:
        if f is None or f.empty:
            return
        self.coverage["rows"] += len(f)
        self.coverage["days"] += 1
        self.coverage["matches"].update(f["match_id"].dropna().unique().tolist())
        self.coverage["pools"].update(f["pool_name"].dropna().unique().tolist())
        self.coverage["turnover"] += float(f["turnover"].sum())
        self.coverage["money_rows"] += int((f["turnover"] > 0).sum())

        f = f.copy()
        _stamp_trader(f)
        f["p_sell_norm"] = np.where(f["sell_book_sum"] > 0,
                                    f["p_sell"] / f["sell_book_sum"], np.nan)
        subsets = {
            "all": pd.Series(True, index=f.index),
            "active": (f["turnover"] > 0) | (f["lag1"] > 0),
            "money": f["turnover"] > 0,
        }
        y = f["turnover"].to_numpy(dtype="float64")

        for sname, mask in subsets.items():
            m = mask.to_numpy()
            if not m.any():
                continue
            yy = y[m]
            for model, col in TURNOVER_MODELS.items():
                ff = f[col].to_numpy(dtype="float64")[m]
                self.turnover[(sname, model)] += _err_stats(yy, ff)
            if sname == "active":
                for dim in SLICE_DIMS:
                    if dim not in f.columns:
                        continue
                    labels = f[dim].astype("string").fillna("unknown").to_numpy()[m]
                    for model, col in TURNOVER_MODELS.items():
                        if model == "zero":
                            continue
                        ff = f[col].to_numpy(dtype="float64")[m]
                        for lab, st in _grouped_err(labels, yy, ff).items():
                            self.slices[(dim, lab, model)] += st

        # residual histogram + scatter sample, on the active subset
        act = subsets["active"].to_numpy()
        if act.any():
            for model, col in TURNOVER_MODELS.items():
                if model == "zero":
                    continue
                e = f[col].to_numpy(dtype="float64")[act] - y[act]
                self.resid[model] += np.histogram(e, bins=RESID_BINS)[0]
            self._sample(f.loc[act, ["turnover", "f_persist", "f_ma3", "f_ema", "pool_name",
                                     "clock_bin", "bucket"]], self.scatter)

        # ---- belief -------------------------------------------------------
        ok = f["settled"].fillna(False).to_numpy() & f["y_frac"].notna().to_numpy()
        if ok.any():
            self.coverage["settled_rows"] += int(ok.sum())
            sk = f.loc[ok, ["pool_id", "line_id", "combination_id"]].astype("int64")
            self.coverage["settled_keys"].update(
                (sk["pool_id"] * 1_000_000 + sk["line_id"] * 1_000
                 + sk["combination_id"]).unique().tolist())
            yf = f["y_frac"].to_numpy(dtype="float64")[ok]
            w = np.maximum(y[ok], 0.0)
            for model, col in BELIEF_MODELS.items():
                p = f[col].to_numpy(dtype="float64")[ok]
                good = np.isfinite(p) & (p > 0) & (p < 1)
                if not good.any():
                    continue
                self.belief[("all", model)] += _belief_stats(p[good], yf[good], w[good])
                labels_frame = f.loc[ok].iloc[np.flatnonzero(good)]
                yy = yf[good]
                ww = w[good]
                pp = p[good]
                for dim in TRADER_DIMS:
                    if dim not in labels_frame.columns:
                        continue
                    labs = labels_frame[dim].astype("string").fillna("unknown").to_numpy()
                    for lab in pd.unique(labs):
                        s = labs == lab
                        if not s.any():
                            continue
                        self.belief_cuts[(model, dim, str(lab))] += _belief_stats(
                            pp[s], yy[s], ww[s])
                idx = np.digitize(p[good], CAL_BINS) - 1
                idx = np.clip(idx, 0, len(CAL_BINS) - 2)
                for b in np.unique(idx):
                    s = idx == b
                    self.cal[(model, int(b))] += np.array([
                        s.sum(), p[good][s].sum(), yf[good][s].sum(),
                        w[good][s].sum(), (w[good][s] * yf[good][s]).sum()])
            self._sample(f.loc[ok, ["p_true", "p_sell_norm", "y_frac", "turnover",
                                    "pool_name", "clock_bin"]], self.cal_scatter)

        bs = f["book_sum"].to_numpy(dtype="float64")
        bs = bs[np.isfinite(bs)]
        if bs.size:
            self.book_hist += np.histogram(bs, bins=BOOK_BINS)[0]

        # ---- GM -----------------------------------------------------------
        gm_ok = f["turnover"] > 0
        if gm_ok.any():
            g = f[gm_ok]
            self.gm[("overall", "all")] += _gm_stats(g)
            for dim in TRADER_DIMS:
                if dim not in g.columns:
                    continue
                for lab, sub in g.groupby(g[dim].astype("string").fillna("unknown"),
                                          observed=True):
                    self.gm[(dim, str(lab))] += _gm_stats(sub)

        # ---- bucket-level totals (what the optimiser really needs) --------
        tot = f.groupby("bucket", observed=True).agg(
            y=("turnover", "sum"), p=("f_persist", "sum"), m=("f_ma3", "sum"),
            e=("f_ema", "sum"))
        for model, col in (("persist", "p"), ("ma3", "m"), ("ema", "e")):
            self.bucket_level[model] += np.array([
                len(tot), float(np.abs(tot[col] - tot["y"]).sum()),
                float(tot["y"].sum()), float(tot[col].sum())])

        # ---- daily series -------------------------------------------------
        self.daily.append({
            "day": str(pd.Timestamp(day).date()),
            "rows": int(len(f)),
            "matches": int(f["match_id"].nunique()),
            "turnover": float(f["turnover"].sum()),
            "forecast_persist": float(f["f_persist"].sum()),
            "forecast_ma3": float(f["f_ma3"].sum()),
            "forecast_ema": float(f["f_ema"].sum()),
            "forecast_zero": 0.0,
            "realized_gm": float(f["realized_gm"].sum()),
            "expected_gm": float(f["expected_gm"].sum()),
            "settled_turnover": float(f.loc[ok, "turnover"].sum()) if ok.any() else 0.0,
        })

    def _sample(self, df: pd.DataFrame, store: List[pd.DataFrame]) -> None:
        have = sum(len(d) for d in store)
        if have >= self.sample_rows or df.empty:
            return
        take = min(len(df), self.sample_rows - have)
        if take < len(df):
            df = df.iloc[self.rng.choice(len(df), take, replace=False)]
        store.append(df.copy())

    # ------------------------------------------------------- per-day files
    _SUMS = ("turnover", "slices", "belief", "belief_cuts", "cal", "gm",
             "resid", "bucket_level")

    def to_state(self) -> dict:
        """Plain, picklable sums for one or more days."""
        state = {name: dict(getattr(self, name)) for name in self._SUMS}
        state["book_hist"] = self.book_hist.copy()
        state["daily"] = list(self.daily)
        state["scatter"] = list(self.scatter)
        state["cal_scatter"] = list(self.cal_scatter)
        state["coverage"] = {k: (set(v) if isinstance(v, set) else v)
                             for k, v in self.coverage.items()}
        return state

    def absorb(self, state: dict) -> None:
        """Add another day's sums. Every statistic is additive by design."""
        for name in self._SUMS:
            mine = getattr(self, name)
            for key, arr in (state.get(name) or {}).items():
                mine[key] += arr
        self.book_hist += state.get("book_hist", 0)
        self.daily.extend(state.get("daily") or [])
        self.scatter.extend(state.get("scatter") or [])
        self.cal_scatter.extend(state.get("cal_scatter") or [])
        for key, value in (state.get("coverage") or {}).items():
            if isinstance(value, set):
                self.coverage[key].update(value)
            else:
                self.coverage[key] += value

    def _thin(self, store: List[pd.DataFrame]) -> List[pd.DataFrame]:
        if not store:
            return store
        df = pd.concat(store, ignore_index=True)
        if len(df) > self.sample_rows:
            df = df.iloc[self.rng.choice(len(df), self.sample_rows, replace=False)]
        return [df]

    # -------------------------------------------------------------- finalize
    def finalize(self) -> dict:
        self.scatter = self._thin(self.scatter)
        self.cal_scatter = self._thin(self.cal_scatter)
        self.daily.sort(key=lambda d: d["day"])
        out: dict = {}
        cov = dict(self.coverage)
        cov["matches"] = len(cov.pop("matches"))
        cov["pools"] = sorted(cov.pop("pools"))
        cov["settled_selections"] = len(cov.pop("settled_keys"))
        out["coverage"] = cov

        out["turnover"] = {
            "models": {f"{s}|{m}": _err_report(v) for (s, m), v in self.turnover.items()},
            "by_slice": [dict(dim=d, value=l, model=m, **_err_report(v))
                         for (d, l, m), v in sorted(self.slices.items())],
            "resid_hist": {m: {"edges": RESID_BINS.tolist(), "counts": v.tolist()}
                           for m, v in self.resid.items()},
            "bucket_level": {m: {"n": int(v[0]),
                                 "mae": _safe(v[1], v[0]),
                                 "wape": _safe(v[1], v[2]),
                                 "bias_pct": _safe(v[3] - v[2], v[2])}
                             for m, v in self.bucket_level.items()},
            "scatter": _pack(self.scatter),
        }
        out["belief"] = {
            "models": {m: _belief_report(v) for (_, m), v in self.belief.items()},
            "calibration": _cal_report(self.cal),
            "book_sum_hist": {"edges": BOOK_BINS.tolist(), "counts": self.book_hist.tolist()},
            "scatter": _pack(self.cal_scatter),
            "by_slice": [dict(model=m, dim=d, value=l, **_belief_report(v))
                         for (m, d, l), v in sorted(self.belief_cuts.items())],
        }
        out["gm"] = {
            "overall": _gm_report(self.gm.get(("overall", "all"), np.zeros(6))),
            "by_slice": [dict(dim=d, value=l, **_gm_report(v))
                         for (d, l), v in sorted(self.gm.items()) if d != "overall"],
        }
        out["daily"] = self.daily
        out["verdict"] = _verdict(out)
        return out


def _stamp_trader(f: pd.DataFrame) -> None:
    """Cuts that already exist on a cached panel day.

    Phase comes from HKJC game_state and the match clock. Domain and kind come
    from the pool code. Side comes from the combination. Line role is how far
    that line sits from the TG or SUP already on the row. Matchup is the
    TG bin crossed with the SUP direction. Quote age is how long the posted
    odds have been hanging. Belief source is true odds, Poisson, or demargin.
    """
    n = len(f)
    minute = pd.to_numeric(f["match_minute"], errors="coerce") if "match_minute" in f.columns else pd.Series(np.nan, index=f.index)
    state = f["game_state"].astype("string") if "game_state" in f.columns else pd.Series("", index=f.index)
    inplay = f["is_inplay"].fillna(False).astype(bool) if "is_inplay" in f.columns else pd.Series(False, index=f.index)
    phase = pd.Series("prematch", index=f.index, dtype="object")
    phase[inplay] = "1H"
    phase[inplay & (minute > 45)] = "2H"
    low = state.str.lower()
    phase[low.str.contains("second", na=False)] = "2H"
    phase[low.str.contains("half.?time|interval", na=False, regex=True)] = "HT"
    f["phase"] = phase.astype("string")

    pool = f["pool_name"].astype("string") if "pool_name" in f.columns else pd.Series("unknown", index=f.index)
    kind_of = {"ou": "totals", "ah": "handicap"}
    domain, kind = [], []
    for code in pool.tolist():
        d, k = POOL_KIND.get(str(code), ("other", "other"))
        domain.append(d)
        kind.append(kind_of.get(k, "other"))
    f["domain"] = pd.Series(domain, index=f.index, dtype="string")
    f["kind"] = pd.Series(kind, index=f.index, dtype="string")

    comb = f["combination_string"].astype("string") if "combination_string" in f.columns else pd.Series("", index=f.index)
    hilo = f["kind"].eq("totals")
    over = comb.str.upper().str.startswith("H")
    f["side"] = np.where(hilo, np.where(over, "over", "under"), np.where(over, "home", "away"))
    f["side"] = pd.Series(f["side"], index=f.index).astype("string")

    corner = f["domain"].astype(str).str.startswith("corner")
    tg = pd.to_numeric(f["tg"], errors="coerce") if "tg" in f.columns else pd.Series(np.nan, index=f.index)
    sup = pd.to_numeric(f["sup"], errors="coerce") if "sup" in f.columns else pd.Series(np.nan, index=f.index)
    if "ctg" in f.columns:
        tg = tg.where(~corner, pd.to_numeric(f["ctg"], errors="coerce"))
    if "csup" in f.columns:
        sup = sup.where(~corner, pd.to_numeric(f["csup"], errors="coerce"))
    f["tg_bin"] = tg.map(tg_bin).astype("string")
    f["sup_side"] = sup.map(sup_bin).astype("string")
    f["matchup"] = (f["tg_bin"].astype(str) + " · " + f["sup_side"].astype(str)).astype("string")
    f["book"] = (f["phase"].astype(str) + " · " + f["kind"].astype(str)).astype("string")

    if "line_label" in f.columns:
        line = f["line_label"].map(_parse_line)
        ref = tg.where(hilo, sup)
        dist = (line - ref).abs()
        role = pd.Series("unknown", index=f.index, dtype="object")
        role[dist.notna() & (dist <= 0.75)] = "near theta"
        role[dist.notna() & (dist > 0.75)] = "wing"
        f["line_role"] = role.astype("string")
    else:
        f["line_role"] = "unknown"

    if "odds_age_min" in f.columns:
        age = pd.to_numeric(f["odds_age_min"], errors="coerce")
        quote = pd.Series("unknown", index=f.index, dtype="object")
        quote[age.notna() & (age <= 2)] = "just moved"
        quote[age.notna() & (age > 2) & (age <= 10)] = "recent"
        quote[age.notna() & (age > 10)] = "stale"
        f["quote_age"] = quote.astype("string")
    else:
        f["quote_age"] = "unknown"
    f["belief_source"] = (f["p_source"].astype("string") if "p_source" in f.columns
                          else pd.Series("unknown", index=f.index, dtype="string"))
    if "event_window" not in f.columns:
        f["event_window"] = "unknown"
    if "odds_bin" not in f.columns:
        f["odds_bin"] = "unknown"
    if "clock_bin" not in f.columns:
        f["clock_bin"] = "unknown"
    del n


def score_frames(days_and_frames, sample_rows: int = 6000) -> dict:
    """Fold a sequence of ``(day, panel)`` pairs into the accuracy report.

    Same numbers ``3-run.bat`` writes for turnover and belief. Does not touch
    the TG/SUP solver.
    """
    acc = Accumulator(sample_rows=sample_rows)
    for day, frame in days_and_frames:
        acc.update(day, frame)
    return acc.finalize()


RESID_BINS = np.concatenate([
    -np.geomspace(1e6, 1.0, 25), [0.0], np.geomspace(1.0, 1e6, 25)])


# ------------------------------------------------------------------ statistics
def _err_stats(y: np.ndarray, f: np.ndarray) -> np.ndarray:
    e = f - y
    return np.array([len(y), np.abs(e).sum(), e.sum(), (e ** 2).sum(),
                     y.sum(), f.sum()])


def _grouped_err(labels, y, f) -> Dict[str, np.ndarray]:
    out = {}
    for lab in pd.unique(labels):
        m = labels == lab
        out[str(lab)] = _err_stats(y[m], f[m])
    return out


def _err_report(v: np.ndarray) -> dict:
    n, sae, se, sse, sy, sf = v
    return {
        "n": int(n),
        "mae": _safe(sae, n),
        "rmse": float(np.sqrt(sse / n)) if n else None,
        "wape": _safe(sae, sy),
        "bias": _safe(se, n),
        "bias_pct": _safe(sf - sy, sy),
        "sum_actual": float(sy),
        "sum_forecast": float(sf),
    }


def _belief_stats(p, y, w) -> np.ndarray:
    p = np.clip(p, EPS, 1 - EPS)
    ll = -(y * np.log(p) + (1 - y) * np.log(1 - p))
    br = (p - y) ** 2
    return np.array([len(p), ll.sum(), br.sum(), (p - y).sum(),
                     w.sum(), (w * ll).sum(), (w * br).sum(), (w * (p - y)).sum()])


def _belief_report(v: np.ndarray) -> dict:
    n, sll, sbr, sd, sw, swll, swbr, swd = v
    return {
        "n": int(n),
        "log_loss": _safe(sll, n),
        "brier": _safe(sbr, n),
        "mean_signed_err": _safe(sd, n),
        "money_log_loss": _safe(swll, sw),
        "money_brier": _safe(swbr, sw),
        "money_signed_err": _safe(swd, sw),
        "weight": float(sw),
    }


def _cal_report(cal: Dict[tuple, np.ndarray]) -> dict:
    out: Dict[str, list] = defaultdict(list)
    for (model, b), v in sorted(cal.items()):
        n, sp, sy, sw, swy = v
        if n <= 0:
            continue
        out[model].append({
            "bin": int(b),
            "lo": float(CAL_BINS[b]), "hi": float(CAL_BINS[b + 1]),
            "n": int(n),
            "p_mean": _safe(sp, n),
            "y_mean": _safe(sy, n),
            "weight": float(sw),
            "y_mean_money": _safe(swy, sw),
        })
    res = {}
    for model, rows in out.items():
        n_tot = sum(r["n"] for r in rows)
        w_tot = sum(r["weight"] for r in rows)
        ece = sum(r["n"] * abs(r["p_mean"] - r["y_mean"]) for r in rows) / n_tot if n_tot else None
        mece = (sum(r["weight"] * abs(r["p_mean"] - r["y_mean_money"]) for r in rows) / w_tot
                if w_tot else None)
        res[model] = {"bins": rows, "ece": ece, "money_ece": mece}
    return res


def _gm_stats(g: pd.DataFrame) -> np.ndarray:
    return np.array([
        len(g),
        float(g["turnover"].sum()),
        float(g["dividend"].sum()),
        float(g["realized_gm"].sum()),
        float(g["expected_gm"].sum()),
        float(g.loc[g["settled"].fillna(False), "turnover"].sum()),
    ])


def _gm_report(v: np.ndarray) -> dict:
    n, to, div, rgm, egm, settled_to = v
    return {
        "n": int(n),
        "turnover": float(to),
        "dividend": float(div),
        "realized_gm": float(rgm),
        "expected_gm": float(egm),
        "realized_margin": _safe(rgm, to),
        "expected_margin": _safe(egm, to),
        "margin_gap": (_safe(rgm, to) - _safe(egm, to)
                       if to else None),
        "settled_turnover": float(settled_to),
    }


def _safe(a, b):
    a, b = float(a), float(b)
    return a / b if b not in (0.0,) else None


def _pack(frames: List[pd.DataFrame]) -> list:
    if not frames:
        return []
    df = pd.concat(frames, ignore_index=True)
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            df[c] = df[c].astype(str)
    return df.where(pd.notna(df), None).to_dict(orient="records")


def _verdict(out: dict) -> List[dict]:
    """Plain-language read of whether the two assumptions hold.

    Belief and margin tolerances scale with the number of settled selections,
    not the number of rows: one selection contributes one independent outcome
    however many buckets it was priced in. Without that, a short window would
    always look mis-calibrated purely from sampling noise.
    """
    v = []
    n_sel = int(out["coverage"].get("settled_selections") or 0)
    sigma = 0.5 / np.sqrt(n_sel) if n_sel else None
    ece_tol = max(0.03, 2 * sigma) if sigma else 0.03
    gm_tol = max(0.01, 2.0 / np.sqrt(n_sel)) if n_sel else 0.01
    t = out["turnover"]["models"].get("active|persist")
    tz = out["turnover"]["models"].get("active|zero")
    te = out["turnover"]["models"].get("active|ema")
    if t:
        beats_zero = (tz and tz["mae"] and t["mae"] and t["mae"] < tz["mae"])
        v.append({
            "topic": "next 5min = last 5min",
            "verdict": "usable" if beats_zero else "no better than forecasting zero",
            "detail": (f"WAPE {_pct(t['wape'])} of turnover, MAE {t['mae']:,.0f} per "
                       f"selection-bucket over {t['n']:,} active buckets; "
                       f"bias {_pct(t['bias_pct'])}. "
                       + (f"EMA(3) WAPE {_pct(te['wape'])}." if te else "")
                       + (f" Forecast-zero WAPE {_pct(tz['wape'])}." if tz else "")),
        })
    bl = out["turnover"]["bucket_level"].get("persist")
    if bl:
        v.append({
            "topic": "market-wide money per bucket",
            "verdict": "good" if (bl["wape"] or 1) < 0.25 else "weak",
            "detail": (f"summed across all live selections, persistence is off by "
                       f"{_pct(bl['wape'])} of the money in a 5-min bucket "
                       f"(bias {_pct(bl['bias_pct'])})."),
        })
    b = out["belief"]["models"].get("p_true")
    s = out["belief"]["models"].get("p_sell")
    cal = out["belief"]["calibration"].get("p_true", {})
    if b:
        gap = (None if not s or s["money_log_loss"] is None or b["money_log_loss"] is None
               else s["money_log_loss"] - b["money_log_loss"])
        cmp_word = ("no comparison" if gap is None else
                    "indistinguishable" if abs(gap) < 1e-4 else
                    "better" if gap > 0 else "worse")
        v.append({
            "topic": "true prob = 1 / true odds",
            "verdict": "calibrated" if (cal.get("money_ece") or 1) < ece_tol
                       else "mis-calibrated",
            "detail": (f"money-weighted log-loss {b['money_log_loss']:.4f}, "
                       f"ECE {_pct(cal.get('money_ece'))}, signed error "
                       f"{_pct(b['money_signed_err'])} on {b['n']:,} settled rows "
                       f"covering {n_sel:,} independent selections, so the noise floor "
                       f"on ECE is about {_pct(ece_tol)}."
                       + (f" Against public odds ({s['money_log_loss']:.4f}) it is "
                          f"{cmp_word}." if s else "")),
        })
    _cut_warnings(v, out)
    g = out["gm"]["overall"]
    if g and g["turnover"]:
        v.append({
            "topic": "gross margin",
            "verdict": "consistent" if (g["margin_gap"] is not None
                                        and abs(g["margin_gap"]) < gm_tol) else "diverges",
            "detail": (f"realised {_pct(g['realized_margin'])} vs expected "
                       f"{_pct(g['expected_margin'])} on turnover "
                       f"{g['turnover']:,.0f}; gap {_pct(g['margin_gap'])} against a "
                       f"noise floor of {_pct(gm_tol)}."),
        })
    return v


def _cut_warnings(v: List[dict], out: dict) -> None:
    """Say which book the headline is hiding, if one cut is clearly worse."""
    headline = (out["turnover"]["models"].get("active|persist") or {}).get("wape")
    rows = [r for r in out["turnover"]["by_slice"]
            if r["model"] == "persist" and r["dim"] in ("book", "phase", "kind", "line_role", "matchup", "event_window", "quote_age")
            and (r.get("sum_actual") or 0) > 0 and r.get("wape") is not None]
    if headline and rows:
        worst = max(rows, key=lambda r: r["wape"])
        if worst["wape"] > headline * 1.35 and worst["wape"] - headline > 0.05:
            v.append({
                "topic": "turnover is not one number",
                "verdict": "split",
                "detail": (f"headline persist WAPE is {_pct(headline)}, but "
                           f"{worst['dim']}={worst['value']} is {_pct(worst['wape'])} "
                           f"on {worst['sum_actual']:,.0f} of turnover. "
                           f"Book the cut, not the average."),
            })
    cuts = [c for c in out["belief"].get("by_slice") or [] if c.get("model") == "p_true"]
    base = (out["belief"]["models"].get("p_true") or {}).get("money_signed_err")
    if base is not None and cuts:
        ranked = [c for c in cuts if (c.get("weight") or 0) > 0 and c.get("money_signed_err") is not None]
        if ranked:
            worst = max(ranked, key=lambda c: abs(c["money_signed_err"]))
            if abs(worst["money_signed_err"]) > max(0.03, abs(base) * 1.5):
                side = "short" if worst["money_signed_err"] < 0 else "long"
                v.append({
                    "topic": "belief is not one number",
                    "verdict": "split",
                    "detail": (f"1/true-odds is {side} by {_pct(worst['money_signed_err'])} "
                               f"on {worst['dim']}={worst['value']} "
                               f"(headline signed error {_pct(base)}). "
                               f"That is the slice to refuse or to reprice."),
                })


def _pct(x) -> str:
    return "n/a" if x is None else f"{100 * float(x):.2f}%"
