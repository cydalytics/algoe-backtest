"""Cached panel days -> the wide fact table the workbench already slices.

``src/backtest/facts.py`` defines a schema where every model writes its own
column onto a shared row:

    t_hat__<model>              next-bucket demand
    p__<source>__<calibrator>   calibrated true probability
    odds_star__<algo>           the odds we would have shown

That is exactly the shape needed to change model without re-running anything,
so the parquet path targets the same schema instead of inventing another. Once
a range is assembled, ``slice_facts`` re-scores any stack on it in place.

Two things happen here that cannot happen inside a cached day:

  * calibrators are walk-forward, so day N is transformed by a fit that only
    saw days 1..N-1. Fitting inside a day would leak its own outcome.
  * ``is_main_line`` is relative to the other lines trading at the same time.

Settlement is read back out of the money rather than re-derived from the
scoreline. ``y_frac`` is the fraction of the potential return that was paid,
so payout per unit stake is ``y_frac * avg_odds``, and that lands on one of
the five outcomes a two-way Asian line can have.

This is the one module of the engine that depends on the rest of the backend,
so the offline bundle never imports it: ``run.py`` reaches its own report
without going through the workbench schema.

Change Log:
-----------
2026-09-15      Initialize (W06 parquet workbench)
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from src.backtest.calibrators import make_calibrator
from src.backtest.dimensions import HALF, PERIOD, odds_band, sup_bin, tg_bin
from src.backtest.settle import outcome_y
from src.pricing.pools import resolve, selection_key, selection_label

from .beliefs import SOURCES, SOURCE_LABELS

# panel column -> model id. The four ids that already exist in the live
# registry keep their names so the MVP baseline highlighting still lines up.
TURNOVER_MODELS: Dict[str, str] = {
    "persistence": "f_persist",
    "trailing_mean": "f_ma3",
    "ema": "f_ema",
    "zero": "f_zero",
    "oracle": "f_oracle",
}

TURNOVER_LABELS = {
    "persistence": "Last 5 minutes (MVP)",
    "trailing_mean": "Mean of last 3 buckets",
    "ema": "EMA of last 3 buckets",
    "zero": "Always zero (sanity)",
    "oracle": "Next-bucket actual (ceiling)",
    "hurdle": "CPU hurdle, walk-forward",
}

# Not a panel column: fitted day by day while the range is assembled.
WALK_FORWARD_MODEL = "hurdle"

BELIEF_LABELS = {
    "hkjc_true": "1 / HKJC true odds (MVP)",
    "poisson": "Poisson on HKJC TG/SUP",
    "demargin": "Public odds, margin removed",
}

# Walk-forward calibrators. 'normalize' is omitted: the panel already scales
# each line onto one, so it would be a no-op that looked like a choice.
DEFAULT_CALIBRATORS = ("raw", "shrink", "isotonic")

ALGO_LABELS = {"hold": "Hold current offer"}

MVP_STACK = {"turnover": "persistence", "true_prob": "hkjc_true",
             "calibrator": "raw", "algo": "hold"}

# The five outcomes an Asian two-way line settles to, as payout per unit stake
# at odds O, paired with the signed result the metric packs expect.
_RESULTS = (1.0, 0.5, 0.0, -0.5, -1.0)

Progress = Callable[[dict], None]


def _payout_candidates(odds: np.ndarray) -> np.ndarray:
    """Payout per unit stake for each of the five outcomes, per row."""
    return np.stack([
        odds,                    # full win
        0.5 * odds + 0.5,        # half win
        np.ones_like(odds),      # push, stake returned
        0.5 * np.ones_like(odds),  # half lose
        np.zeros_like(odds),     # full lose
    ], axis=1)


def settle_from_money(f: pd.DataFrame) -> pd.Series:
    """Recover the signed settlement result from what was actually paid out.

    Preferred over re-settling from the scoreline: it needs no line-sign
    convention, no push rules and no corner board, and it agrees with the
    money by construction. Rows whose payout does not land near any of the
    five outcomes are left unsettled rather than forced onto the nearest one.
    """
    out = pd.Series(np.nan, index=f.index, dtype="float64")
    y = pd.to_numeric(f.get("y_frac"), errors="coerce")
    odds = pd.to_numeric(f.get("avg_odds"), errors="coerce")
    odds = odds.where(odds > 1.0, pd.to_numeric(f.get("odds"), errors="coerce"))
    settled = f.get("settled")
    ok = y.notna() & odds.notna() & (odds > 1.0)
    if settled is not None:
        ok &= settled.fillna(False).astype(bool)
    if not ok.any():
        return out

    o = odds[ok].to_numpy(dtype="float64")
    paid = (y[ok].to_numpy(dtype="float64") * o)
    cand = _payout_candidates(o)
    gap = np.abs(cand - paid[:, None])
    pick = gap.argmin(axis=1)
    best = gap[np.arange(len(pick)), pick]
    # a quarter-line half win sits (O-1)/2 away from the nearest neighbour, so
    # a tolerance of a quarter of that spread cannot confuse two outcomes
    tol = np.maximum(0.02, 0.125 * (o - 1.0))
    res = np.where(best <= tol, np.take(_RESULTS, pick), np.nan)
    out.loc[ok] = res
    return out


def _phase(f: pd.DataFrame) -> pd.Series:
    """Match phase in the vocabulary ``dimensions.HALF``/``PERIOD`` expect."""
    state = f.get("game_state")
    state = (state.astype("string").fillna("") if state is not None
             else pd.Series("", index=f.index, dtype="string"))
    minute = pd.to_numeric(f.get("match_minute"), errors="coerce")
    inplay = f.get("is_inplay")
    inplay = (inplay.astype(bool) if inplay is not None
              else pd.Series(False, index=f.index))

    out = pd.Series("prematch", index=f.index, dtype="object")
    out[inplay] = "first_half"
    out[inplay & (minute > 45)] = "second_half"
    # HKJC's own state is the authority when it is present
    out[state.str.lower().str.contains("second", na=False)] = "second_half"
    out[state.str.lower().str.contains("half.?time|interval", na=False,
                                       regex=True)] = "half_time"
    out[state.str.lower().str.contains("full.?time|finish|end", na=False,
                                       regex=True)] = "full_time"
    return out.astype("string")


def _web_clock_bin(phase: pd.Series, minute: pd.Series) -> pd.Series:
    """The clock cut the existing UI orders by (``dimensions.CLOCK_ORDER``)."""
    out = pd.Series("unk", index=phase.index, dtype="object")
    out[phase == "prematch"] = "pre"
    out[phase == "half_time"] = "ht"
    out[phase == "full_time"] = "ft"
    live = phase.isin(["first_half", "second_half"])
    m = pd.to_numeric(minute, errors="coerce")
    for lo, hi, label in ((0, 15, "00-15"), (15, 30, "15-30"), (30, 45, "30-45"),
                          (45, 60, "45-60"), (60, 75, "60-75"), (75, 90, "75-90")):
        out[live & (m >= lo) & (m < hi)] = label
    out[live & (m >= 90)] = "90+"
    return out.astype("string")


def _main_line(f: pd.DataFrame) -> pd.Series:
    """Whether a row is on the busiest line of its pool at that moment.

    HKJC does not publish which line is the main one, so it is inferred from
    where the money is: within one (match, pool, bucket) the line taking the
    most turnover is the one the market is treating as the reference.
    """
    keys = ["match_id", "pool_name", "bucket"]
    if not all(c in f.columns for c in keys):
        return pd.Series(False, index=f.index)
    money = pd.to_numeric(f["turnover"], errors="coerce").fillna(0.0)
    per_line = money.groupby([f[k] for k in keys] + [f["line_id"]]).transform("sum")
    best = per_line.groupby([f[k] for k in keys]).transform("max")
    return (per_line >= best) & (best > 0)


def to_facts(panel: pd.DataFrame) -> pd.DataFrame:
    """One cached panel day -> wide facts, minus the calibrated beliefs."""
    if panel is None or panel.empty:
        return pd.DataFrame()
    f = panel
    out = pd.DataFrame(index=f.index)

    pool_code = f["pool_name"].astype("string").fillna("")
    defs = {code: resolve(code) for code in pool_code.dropna().unique()}
    out["pool_code"] = pool_code
    out["family"] = pool_code.map(lambda c: getattr(defs.get(c), "family", "Specials"))
    out["domain"] = pool_code.map(lambda c: getattr(defs.get(c), "domain", "goal"))
    out["seg"] = pool_code.map(lambda c: getattr(defs.get(c), "seg", "FT"))
    out["kind"] = pool_code.map(lambda c: getattr(defs.get(c), "kind", "unknown"))

    out["as_of"] = pd.to_datetime(f["bucket"]).astype("datetime64[ns]")
    out["day"] = out["as_of"].dt.floor("D")
    out["match_id"] = pd.to_numeric(f["match_id"], errors="coerce").astype("Int64")
    out["label_key"] = [
        selection_key(m, p, l, c) for m, p, l, c in
        zip(out["match_id"], pool_code, f["line_id"], f["combination_id"])
    ]

    out["home"] = f.get("home_name", pd.Series("", index=f.index)).astype("string")
    out["away"] = f.get("away_name", pd.Series("", index=f.index)).astype("string")
    out["league"] = f.get("league_name", pd.Series("", index=f.index)).astype("string")
    out["league_code"] = f.get("league_code", pd.Series("", index=f.index)).astype("string")
    out["country"] = pd.Series("", index=f.index, dtype="string")
    # the extraction carries no cup/league split, so every match is a league one
    # until that column exists; saying "league" is honest, guessing is not
    out["competition"] = pd.Series("league", index=f.index, dtype="string")

    phase = _phase(f)
    out["phase"] = phase
    out["half"] = phase.map(HALF).fillna("pre").astype("string")
    out["period"] = phase.map(PERIOD).fillna("prematch").astype("string")
    out["minute"] = pd.to_numeric(f.get("match_minute"), errors="coerce")
    out["clock_bin"] = _web_clock_bin(phase, out["minute"])
    # the panel's finer cut, kept as its own dimension: it splits prematch into
    # the hours before kick-off, which the coarse bin above throws away
    out["clock_fine"] = f.get("clock_bin", pd.Series(pd.NA, index=f.index)).astype("string")

    comb = f["combination_string"].astype("string").fillna("")
    out["selection"] = comb
    line_label = f["line_label"].astype("string").fillna("")
    out["sel_label"] = [
        "{} {}".format(selection_label(defs.get(pc), sel, "Home", "Away")
                       if defs.get(pc) else sel, ll).strip()
        for pc, sel, ll in zip(pool_code, comb, line_label)
    ]
    out["line_label"] = line_label
    out["is_main_line"] = _main_line(f).to_numpy()

    out["tg"] = pd.to_numeric(f.get("tg"), errors="coerce")
    out["sup"] = pd.to_numeric(f.get("sup"), errors="coerce")
    out["tg_bin"] = out["tg"].map(tg_bin).astype("string")
    out["sup_bin"] = out["sup"].map(sup_bin).astype("string")
    out["tg_sup"] = out["tg_bin"].astype(str) + "|" + out["sup_bin"].astype(str)

    out["event_kind"] = f.get("event_kind", pd.Series("none", index=f.index)).astype("string")
    out["event_window"] = f.get("event_window", pd.Series("quiet", index=f.index)).astype("string")
    out["minutes_since_goal"] = pd.to_numeric(f.get("minutes_since_goal"), errors="coerce")
    out["minutes_since_corner"] = pd.to_numeric(f.get("minutes_since_corner"), errors="coerce")

    odds = pd.to_numeric(f["odds"], errors="coerce")
    true_odds = pd.to_numeric(f.get("true_odds"), errors="coerce")
    out["sell_odds"] = odds
    out["hkjc_odds"] = odds
    out["hkjc_true_odds"] = true_odds
    out["odds_band"] = true_odds.where(true_odds > 1.0, odds).map(odds_band).astype("string")
    out["odds_bin"] = f.get("odds_bin", pd.Series(pd.NA, index=f.index)).astype("string")
    out["odds_age_min"] = pd.to_numeric(f.get("odds_age_min"), errors="coerce")

    out["t_actual"] = pd.to_numeric(f["turnover"], errors="coerce").fillna(0.0)
    out["t_actual_tickets"] = pd.to_numeric(f.get("tickets"), errors="coerce").fillna(0.0)
    out["t5m"] = pd.to_numeric(f.get("lag1"), errors="coerce").fillna(0.0)
    out["lag_state"] = np.where(out["t5m"] > 0, "lag>0", "lag=0")
    out["tickets5m"] = pd.to_numeric(f.get("tickets_lag1"), errors="coerce").fillna(0.0)
    out["invested"] = pd.to_numeric(f.get("turnover_key"), errors="coerce")
    out["book_sum"] = pd.to_numeric(f.get("book__hkjc_true"), errors="coerce")
    out["sell_book_sum"] = pd.to_numeric(f.get("sell_book_sum"), errors="coerce")
    out["hkjc_margin"] = pd.to_numeric(f.get("hkjc_margin"), errors="coerce")

    out["result"] = settle_from_money(f)
    out["y_frac"] = pd.to_numeric(f.get("y_frac"), errors="coerce")
    out["settled"] = out["result"].notna()

    for model, col in TURNOVER_MODELS.items():
        if col in f.columns:
            out["t_hat__{}".format(model)] = pd.to_numeric(f[col], errors="coerce")
    for source in SOURCES:
        col = "p__{}".format(source)
        if col in f.columns:
            out["p_src__{}".format(source)] = pd.to_numeric(f[col], errors="coerce")
    out["odds_star__hold"] = odds
    return out.reset_index(drop=True)


class _Calibration:
    """Walk-forward calibrator fits, one per (source, calibrator).

    A fit only ever sees days that are already finished, which is what makes
    the calibrated numbers a forecast rather than a description.
    """

    def __init__(self, sources: Sequence[str], names: Sequence[str]):
        self.sources = list(sources)
        self.names = [n for n in names if n != "raw"]
        self._history: Dict[str, List[Tuple[np.ndarray, np.ndarray]]] = {
            s: [] for s in self.sources
        }

    def transform(self, facts: pd.DataFrame) -> None:
        for source in self.sources:
            col = "p_src__{}".format(source)
            if col not in facts.columns:
                continue
            p = facts[col]
            facts["p__{}__raw".format(source)] = p
            hist = self._history[source]
            if hist:
                hp = np.concatenate([h[0] for h in hist])
                hy = np.concatenate([h[1] for h in hist])
            else:
                hp = hy = np.empty(0)
            for name in self.names:
                cal = make_calibrator(name)
                if hp.size:
                    cal.fit(hp, hy)
                facts["p__{}__{}".format(source, name)] = cal.transform(
                    p.to_numpy(dtype="float64"))

    def observe(self, facts: pd.DataFrame) -> None:
        """Fold a finished day's settled rows into the fitting history."""
        y = facts["result"].map(lambda r: outcome_y(r) if pd.notna(r) else None)
        keep = y.notna()
        if not keep.any():
            return
        yv = y[keep].to_numpy(dtype="float64")
        for source in self.sources:
            col = "p_src__{}".format(source)
            if col not in facts.columns:
                continue
            p = facts.loc[keep, col].to_numpy(dtype="float64")
            good = np.isfinite(p)
            if good.any():
                self._history[source].append((p[good], yv[good]))


# Columns kept as dictionary-encoded categories. Over a long window these are
# the difference between a frame that fits in memory and one that does not.
_CATEGORICAL = (
    "pool_code", "family", "domain", "seg", "kind", "phase", "half", "period",
    "clock_bin", "clock_fine", "selection", "sel_label", "line_label",
    "league", "league_code", "country", "competition",
    "event_kind", "event_window", "tg_bin", "sup_bin", "tg_sup",
    "odds_band", "odds_bin", "lag_state",
)


def compact(facts: pd.DataFrame) -> pd.DataFrame:
    """Shrink dtypes without changing any number the metrics read.

    Floats go to float32, which holds about seven significant digits: ample
    for turnover in HK$ and for probabilities, and a quarter of the memory.
    """
    for col in _CATEGORICAL:
        if col in facts.columns:
            facts[col] = facts[col].astype("string").fillna("unknown").astype("category")
    for col in facts.columns:
        if col in _CATEGORICAL or col in ("as_of", "day", "label_key"):
            continue
        if pd.api.types.is_float_dtype(facts[col]):
            facts[col] = facts[col].astype("float32")
    return facts


def models_present(facts: pd.DataFrame) -> dict:
    turnover = [m for m in list(TURNOVER_MODELS) + [WALK_FORWARD_MODEL]
                if "t_hat__{}".format(m) in facts.columns]
    beliefs = sorted({c[len("p__"):] for c in facts.columns if c.startswith("p__")})
    return {
        "turnover": turnover,
        "beliefs": ["{}/{}".format(*b.split("__", 1)) for b in beliefs if "__" in b],
        "algos": ["hold"],
    }


def catalog(calibrators: Sequence[str]) -> dict:
    from src.backtest.calibrators import CALIBRATOR_LABELS
    return {
        "turnover": dict(TURNOVER_LABELS),
        "true_prob": dict(BELIEF_LABELS),
        "calibrators": {c: CALIBRATOR_LABELS.get(c, c) for c in calibrators},
        "algos": dict(ALGO_LABELS),
        "policies": {},
        "elasticities": [],
        "sources": dict(SOURCE_LABELS),
    }


def assemble(cache, start, end, calibrators: Sequence[str] = DEFAULT_CALIBRATORS,
             max_rows: int = 0, progress: Optional[Progress] = None):
    """Cached days in a window -> one wide fact table, oldest day first.

    Days are visited in order so the calibrators only ever look backwards.
    ``max_rows`` caps the result by dropping whole days from the *front*, which
    keeps the surviving window contiguous and its calibration valid; the meta
    block reports whenever that happened so the UI can say so.
    """
    from .cache import day_list
    from .forecast import WalkForward

    cal = _Calibration(SOURCES, calibrators)
    walk = WalkForward()
    pieces: List[pd.DataFrame] = []
    kept: List[str] = []
    skipped: List[str] = []
    rows = 0
    days = day_list(start, end)
    for i, day in enumerate(days, start=1):
        panel = cache.load_day(day)
        key = "{:%Y-%m-%d}".format(day)
        if panel is None or panel.empty:
            skipped.append(key)
            if progress is not None:
                progress({"stage": "assemble", "day": key, "rows": 0,
                          "done": i, "total": len(days)})
            continue
        forecast, prepared = walk.predict(panel)
        facts = to_facts(panel)
        facts["t_hat__{}".format(WALK_FORWARD_MODEL)] = forecast
        walk.observe(prepared, day)
        cal.transform(facts)
        cal.observe(facts)
        facts = facts.drop(columns=[c for c in facts.columns
                                    if c.startswith("p_src__")])
        pieces.append(facts)
        kept.append(key)
        rows += len(facts)
        if progress is not None:
            progress({"stage": "assemble", "day": key, "rows": len(facts),
                      "done": i, "total": len(days)})

    if not pieces:
        return pd.DataFrame(), {"days": [], "rows": 0, "trimmed_days": [],
                                "calibrators": list(calibrators),
                                "walk_forward": walk.summary()}

    trimmed: List[str] = []
    if max_rows and rows > max_rows:
        while pieces and rows > max_rows:
            rows -= len(pieces[0])
            pieces.pop(0)
            trimmed.append(kept.pop(0))

    facts = pd.concat(pieces, ignore_index=True)
    facts = compact(facts)
    meta = {
        "days": kept,
        "rows": int(len(facts)),
        "empty_days": skipped,
        "trimmed_days": trimmed,
        "calibrators": list(calibrators),
        "models": models_present(facts),
        "memory_mb": round(facts.memory_usage(deep=True).sum() / 1e6, 1),
        "walk_forward": walk.summary(),
    }
    return facts, meta
