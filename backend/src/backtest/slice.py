"""
Workbench Slice

Filter the wide fact table, compose a chosen stack, and recompute the
metric packs + plot gallery on that cut. Compare every stored model on
the same rows. Group-by is a second pass of the same packs.

Expected GM composes the selected turnover and belief at the posted
(or stored-solver) odds. Realised GM at hold is a property of actual
tickets and posted prices — forecasts do not move it.

Change Log:
-----------
2026-09-12      Initialize (W06 workbench)
2026-09-12      A/B compare on shared rows + aligned distributions
"""

import hashlib
import json
from collections import OrderedDict
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from . import metrics as packs
from . import plots as P
from .settle import outcome_y
from .dimensions import CLOCK_ORDER, FILTER_COLS, GROUP_COLS
from .facts import (
    discover_models, odds_star_col, p_col, parse_belief,
    t_actual_star_col, t_hat_col, t_hat_star_col,
)
from .predictors import TRUE_PROB_LABELS, TURNOVER_LABELS
from .algos import ALGO_LABELS
from .calibrators import CALIBRATOR_LABELS

MVP_T = "persistence"
MVP_P = "hkjc_true"
MVP_CAL = "raw"
MVP_ALGO = "hold"

DEFAULT_GALLERY = (
    "clock_bin", "half", "period", "event_window",
    "pool_code", "family", "league", "competition",
    "tg_bin", "sup_bin", "odds_band",
)

# ---------------------------------------------------------------------------
# Answer cache
#
# The fact table is what a run stores; the metric packs are derived from it on
# demand, which is what makes model switching free. Scoring the same stack on
# the same rows always gives the same answer, so hold onto it: a page reload,
# a back-and-forth between two stacks, or two people looking at the same run
# all replay instead of recomputing.
# ---------------------------------------------------------------------------

CACHE_MAX = 96
_cache: "OrderedDict[str, dict]" = OrderedDict()


def _cache_key(run_id: str, kind: str, payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True, default=str)
    digest = hashlib.sha1(body.encode("utf-8")).hexdigest()[:16]
    return "{}|{}|{}".format(run_id, kind, digest)


def _remember(key: str, build):
    hit = _cache.get(key)
    if hit is not None:
        _cache.move_to_end(key)
        return hit
    value = build()
    _cache[key] = value
    while len(_cache) > CACHE_MAX:
        _cache.popitem(last=False)
    return value


def cache_stats() -> dict:
    return {"entries": len(_cache), "limit": CACHE_MAX}


def invalidate(run_id: Optional[str] = None) -> int:
    """Drop cached answers. Call when a run's fact table is rewritten."""
    if run_id is None:
        dropped = len(_cache)
        _cache.clear()
        return dropped
    stale = [k for k in _cache if k.startswith("{}|".format(run_id))]
    for k in stale:
        _cache.pop(k, None)
    return len(stale)


def slice_cached(run_id: str, facts: pd.DataFrame, **kw) -> dict:
    key = _cache_key(run_id, "slice", kw)
    return _remember(key, lambda: slice_facts(facts, **kw))


def compare_cached(run_id: str, facts: pd.DataFrame, **kw) -> dict:
    key = _cache_key(run_id, "compare", kw)
    return _remember(key, lambda: compare_facts(facts, **kw))


def slice_facts(
    facts: pd.DataFrame,
    turnover: str = MVP_T,
    true_prob: str = MVP_P,
    calibrator: str = MVP_CAL,
    algo: str = MVP_ALGO,
    filters: Optional[Dict[str, Sequence[str]]] = None,
    group_by: str = "clock_bin",
) -> dict:
    """Score one composed stack on a filtered cut of the fact table."""
    models = discover_models(facts)
    filters = _clean_filters(filters)
    masked = _scored(apply_filters(facts, filters), [turnover])
    work = compose(masked, turnover, true_prob, calibrator, algo)
    n = int(len(work))

    t_pack = packs.turnover_pack(work) if n else packs.turnover_pack(None)
    p_pack = packs.true_odds_pack(work) if n else packs.true_odds_pack(None)
    g_pack = packs.gm_pack(work) if n else packs.gm_pack(None)

    by = group_by if group_by in GROUP_COLS else "clock_bin"
    return {
        "n_rows": n,
        "n_full": int(len(facts)),
        "stack": {
            "turnover": turnover,
            "turnover_label": TURNOVER_LABELS.get(turnover, turnover),
            "true_prob": true_prob,
            "true_prob_label": TRUE_PROB_LABELS.get(true_prob, true_prob),
            "calibrator": calibrator,
            "calibrator_label": CALIBRATOR_LABELS.get(calibrator, calibrator),
            "algo": algo,
            "algo_label": ALGO_LABELS.get(algo, algo),
            "baseline": {
                "turnover": turnover == MVP_T,
                "true_prob": true_prob == MVP_P and calibrator == MVP_CAL,
                "algo": algo == MVP_ALGO,
            },
        },
        "filters": {k: list(v) for k, v in filters.items()},
        "group_by": by,
        "models": models,
        "facets": facets(facts, filters),
        "turnover": t_pack,
        "true_odds": p_pack,
        "gm": g_pack,
        "compare": compare_models(work, models, turnover, true_prob, calibrator, algo),
        "breakdown": breakdown(work, by),
        "gallery": gallery(work),
        "note": (
            "E[GM] composes the selected turnover × belief at the posted "
            "(or stored-solver) odds. Solver prices were produced on the "
            "MVP stack — they are a price path, not a re-solve under this belief."
        ),
    }


def compare_facts(
    facts: pd.DataFrame,
    left: Optional[dict] = None,
    right: Optional[dict] = None,
    filters: Optional[Dict[str, Sequence[str]]] = None,
    group_by: str = "clock_bin",
) -> dict:
    """Score two composed stacks on the same filtered rows."""
    left = _stack_spec(left)
    right = _stack_spec(right)
    models = discover_models(facts)
    filters = _clean_filters(filters)
    filtered = apply_filters(facts, filters)
    masked = _scored(filtered, [left["turnover"], right["turnover"]])
    dropped = int(len(filtered) - len(masked)) if filtered is not None and masked is not None else 0
    a = compose(masked, left["turnover"], left["true_prob"], left["calibrator"], left["algo"])
    b = compose(masked, right["turnover"], right["true_prob"], right["calibrator"], right["algo"])
    by = group_by if group_by in GROUP_COLS else "clock_bin"
    left_side = _side(a, left, by)
    right_side = _side(b, right, by)
    note = ("Both sides are scored on the same filtered rows. Histogram bins "
            "and CDFs share an axis so the shapes are comparable.")
    if dropped:
        note += (" {:,} rows with no forecast on one side (walk-forward warm-up, "
                 "or rows that side does not score) are left out of both.").format(dropped)
    return {
        "n_rows": int(len(masked)) if masked is not None else 0,
        "n_full": int(len(facts)) if facts is not None else 0,
        "n_unscored": dropped,
        "filters": {k: list(v) for k, v in filters.items()},
        "group_by": by,
        "models": models,
        "facets": facets(facts, filters),
        "left": left_side,
        "right": right_side,
        "delta": _metric_delta(left_side, right_side),
        "dists": _dists(a, b),
        "daily": _daily_test(a, b),
        "level": {"a": _level_bins(a), "b": _level_bins(b)},
        "cuts": _cut_delta(left_side["breakdown"], right_side["breakdown"]),
        "note": note,
    }


def _scored(frame: pd.DataFrame, turnover_models: Sequence[str]) -> pd.DataFrame:
    """Rows every named turnover model actually forecast. A model with no
    column is ignored here and scored as zero later, as before."""
    if frame is None or frame.empty:
        return frame
    keep = pd.Series(True, index=frame.index)
    for name in dict.fromkeys(turnover_models):
        col = t_hat_col(name)
        if col in frame.columns:
            keep &= pd.to_numeric(frame[col], errors="coerce").notna()
    return frame if bool(keep.all()) else frame[keep]


def _daily_test(a: pd.DataFrame, b: pd.DataFrame) -> dict:
    """Day-level paired test of B against A on WAPE.

    Rows inside a day share matches and money, so they are not independent;
    days are the unit. d_t = WAPE_B,t - WAPE_A,t. The standard error is
    Newey-West with one lag, which is the Diebold-Mariano statistic on the
    daily loss differential.
    """
    if a is None or a.empty or b is None or b.empty or "day" not in a.columns:
        return {"days": [], "n_days": 0}
    y = _num(a, "t_actual")
    frame = pd.DataFrame({
        "day": pd.to_datetime(a["day"]).dt.strftime("%Y-%m-%d").to_numpy(),
        "y": y,
        "ea": np.abs(_num(a, "t_hat") - y),
        "eb": np.abs(_num(b, "t_hat") - y),
        "pa": _num(a, "t_hat"),
        "pb": _num(b, "t_hat"),
    })
    g = frame.groupby("day", sort=True).agg(y=("y", "sum"), ea=("ea", "sum"), eb=("eb", "sum"),
                                            pa=("pa", "sum"), pb=("pb", "sum"), n=("y", "size"))
    g = g[g["y"] > 0]
    if g.empty:
        return {"days": [], "n_days": 0}
    wa = g["ea"] / g["y"] * 100.0
    wb = g["eb"] / g["y"] * 100.0
    d = (wb - wa).to_numpy(dtype=float)
    n = len(d)
    mean = float(d.mean())
    stat = None
    se = None
    if n >= 3:
        c = d - mean
        g0 = float(c @ c) / n
        g1 = float(c[1:] @ c[:-1]) / n
        var = max(g0 + g1, g0 * 0.25) / n
        se = float(np.sqrt(var))
        stat = mean / se if se > 0 else None
    total_y = float(g["y"].sum())
    return {
        "n_days": n,
        "wape_a": round(float(g["ea"].sum()) / total_y * 100.0, 4),
        "wape_b": round(float(g["eb"].sum()) / total_y * 100.0, 4),
        "skill": round(1.0 - float(g["eb"].sum()) / float(g["ea"].sum()), 4)
        if float(g["ea"].sum()) > 0 else None,
        "mean_diff": round(mean, 4),
        "se": None if se is None else round(se, 4),
        "dm_stat": None if stat is None else round(stat, 3),
        "ci95": None if se is None else [round(mean - 1.96 * se, 4), round(mean + 1.96 * se, 4)],
        "b_better_days": int((d < 0).sum()),
        "a_better_days": int((d > 0).sum()),
        "days": [
            {"day": day, "wape_a": round(float(x), 3), "wape_b": round(float(z), 3),
             "diff": round(float(z - x), 3), "turnover": round(float(t), 2),
             "bias_a": round(float((pa - t) / t * 100.0), 3),
             "bias_b": round(float((pb - t) / t * 100.0), 3), "n": int(k)}
            for day, x, z, t, pa, pb, k in zip(g.index, wa, wb, g["y"], g["pa"], g["pb"], g["n"])
        ],
    }


def _level_bins(work: pd.DataFrame, bins: int = 12) -> List[dict]:
    """Mean forecast against mean actual, in quantile bins of the forecast.

    A forecast that is right on average at every level lies on the diagonal.
    Zero forecasts get their own bin; the rest are split by quantile so each
    bin holds a similar number of rows.
    """
    if work is None or work.empty:
        return []
    p = _num(work, "t_hat")
    y = _num(work, "t_actual")
    ok = np.isfinite(p) & np.isfinite(y)
    p, y = p[ok], y[ok]
    out = []
    zero = p <= 0
    if zero.any():
        out.append({"pred": 0.0, "actual": round(float(y[zero].mean()), 2),
                    "n": int(zero.sum()), "money": round(float(y[zero].sum()), 2)})
    pos_p, pos_y = p[~zero], y[~zero]
    if len(pos_p):
        edges = np.unique(np.quantile(pos_p, np.linspace(0, 1, bins + 1)))
        idx = np.clip(np.searchsorted(edges, pos_p, side="right") - 1, 0, max(len(edges) - 2, 0))
        for k in np.unique(idx):
            s = idx == k
            out.append({"pred": round(float(pos_p[s].mean()), 2),
                        "actual": round(float(pos_y[s].mean()), 2),
                        "n": int(s.sum()), "money": round(float(pos_y[s].sum()), 2)})
    return out


def apply_filters(frame: pd.DataFrame, filters: Dict[str, Sequence[str]]) -> pd.DataFrame:
    if frame is None or frame.empty or not filters:
        return frame
    work = frame
    for col, values in filters.items():
        if col not in work.columns or not values:
            continue
        allowed = {str(v) for v in values}
        work = work[work[col].map(lambda x: str(x) in allowed)]
    return work


def compose(frame: pd.DataFrame, turnover, true_prob, calibrator, algo) -> pd.DataFrame:
    """Map wide columns onto the names the metric packs already read."""
    if frame is None or frame.empty:
        return pd.DataFrame()
    work = frame.copy()
    tcol = t_hat_col(turnover)
    pcol = p_col(true_prob, calibrator)
    ocol = odds_star_col(algo)
    if tcol in work.columns:
        work["t_hat"] = pd.to_numeric(work[tcol], errors="coerce")
    elif "t_hat" not in work.columns:
        work["t_hat"] = 0.0
    if pcol in work.columns:
        work["true_prob"] = pd.to_numeric(work[pcol], errors="coerce")
    elif "true_prob" not in work.columns:
        work["true_prob"] = pd.to_numeric(work.get("hkjc_true_odds"), errors="coerce").rdiv(1.0)
    if algo == "hold" or ocol not in work.columns:
        work["sell_odds_star"] = work["sell_odds"] if "sell_odds" in work.columns else work.get("hkjc_odds")
        work["t_hat_star"] = work["t_hat"]
        work["t_actual_star"] = work["t_actual"] if "t_actual" in work.columns else 0.0
    else:
        work["sell_odds_star"] = pd.to_numeric(work[ocol], errors="coerce")
        ts = t_hat_star_col(algo)
        ta = t_actual_star_col(algo)
        work["t_hat_star"] = pd.to_numeric(work[ts], errors="coerce") if ts in work.columns else work["t_hat"]
        work["t_actual_star"] = (
            pd.to_numeric(work[ta], errors="coerce") if ta in work.columns
            else work["t_actual"] if "t_actual" in work.columns else 0.0
        )
    return work


def facets(facts: pd.DataFrame, filters: Dict[str, Sequence[str]]) -> dict:
    """Unique values per cut, cascading (other filters applied)."""
    out = {}
    if facts is None or facts.empty:
        return out
    for col in FILTER_COLS:
        if col not in facts.columns:
            continue
        others = {k: v for k, v in filters.items() if k != col}
        cut = apply_filters(facts, others)
        counts = cut[col].astype(str).value_counts(dropna=True)
        values = _ordered(col, list(counts.index))
        out[col] = [{"value": v, "n": int(counts.get(v, 0))} for v in values]
    return out


def compare_models(work, models, turnover, true_prob, calibrator, algo) -> dict:
    """Every stored model, scored on the same filtered rows."""
    t_rows, p_rows, a_rows = [], [], []
    for name in models.get("turnover") or []:
        col = t_hat_col(name)
        if col not in work.columns:
            continue
        frame = work.copy()
        frame["t_hat"] = pd.to_numeric(frame[col], errors="coerce")
        pack = packs.turnover_pack(frame, cuts=False)
        t_rows.append({
            "id": name,
            "label": TURNOVER_LABELS.get(name, name),
            "baseline": name == MVP_T,
            "selected": name == turnover,
            "n": pack["n"],
            "mae": pack["mae"],
            "mse": pack["mse"],
            "rmse": pack["rmse"],
            "wape_pct": pack.get("wape_pct", 0),
            "r2": pack.get("r2", 0),
            "theil_u": pack.get("theil_u", 0),
        })
    for token in models.get("beliefs") or []:
        source, cal = parse_belief(token)
        col = p_col(source, cal)
        if col not in work.columns:
            continue
        frame = work.copy()
        frame["true_prob"] = pd.to_numeric(frame[col], errors="coerce")
        pack = packs.true_odds_pack(frame, cuts=False)
        p_rows.append({
            "id": token,
            "label": "{} / {}".format(
                TRUE_PROB_LABELS.get(source, source),
                CALIBRATOR_LABELS.get(cal, cal),
            ),
            "baseline": source == MVP_P and cal == MVP_CAL,
            "selected": source == true_prob and cal == calibrator,
            "n": pack["n"],
            "logloss": pack["logloss"],
            "brier": pack["brier"],
            "accuracy": pack.get("accuracy", 0),
            "ece": pack.get("ece", 0),
            "auc": pack.get("auc", 0.5),
            "bias": pack["bias"],
        })
    for name in models.get("algos") or ["hold"]:
        frame = compose(work, turnover, true_prob, calibrator, name)
        pack = packs.gm_pack(frame, cuts=False)
        a_rows.append({
            "id": name,
            "label": ALGO_LABELS.get(name, name),
            "baseline": name == MVP_ALGO,
            "selected": name == algo,
            "n": pack["n"],
            "exp_gm_star": pack["exp_gm_star"],
            "exp_lift": pack["exp_lift"],
            "realized_gm": pack["realized_gm"],
            "realized_lift": pack["realized_lift"],
            "optimism": pack.get("optimism", 0),
        })
    t_rows.sort(key=lambda r: r["mae"])
    p_rows.sort(key=lambda r: r["logloss"])
    a_rows.sort(key=lambda r: -r["realized_gm"])
    return {"turnover": t_rows, "belief": p_rows, "algo": a_rows}


def breakdown(work: pd.DataFrame, col: str) -> List[dict]:
    if work is None or work.empty or col not in work.columns:
        return []
    rows = []
    for key, group in work.groupby(col, dropna=False):
        t = packs.turnover_pack(group, cuts=False)
        p = packs.true_odds_pack(group, cuts=False)
        g = packs.gm_pack(group, cuts=False)
        rows.append({
            "key": str(key),
            "n": int(len(group)),
            "mae": t["mae"],
            "mse": t["mse"],
            "rmse": t["rmse"],
            "wape_pct": t.get("wape_pct", 0),
            "bias_pct": t.get("bias_pct", 0),
            "sum_actual": t.get("sum_actual", 0),
            "logloss": p["logloss"],
            "brier": p["brier"],
            "accuracy": p.get("accuracy", 0),
            "ece": p.get("ece", 0),
            "exp_gm_star": g["exp_gm_star"],
            "realized_gm": g["realized_gm"],
            "realized_lift": g["realized_lift"],
        })
    return _sort_breakdown(col, rows)


def gallery(work: pd.DataFrame) -> dict:
    """Dense extra plots that the pack plots do not already carry."""
    if work is None or work.empty:
        return {}
    out = {
        "mae_by": {},
        "logloss_by": {},
        "accuracy_by": {},
        "gm_by": {},
        "exp_gm_by": {},
    }
    for col in DEFAULT_GALLERY:
        if col not in work.columns:
            continue
        t_rows, p_rows, g_rows, e_rows, a_rows = [], [], [], [], []
        for key, group in work.groupby(col, dropna=False):
            t = packs.turnover_pack(group, cuts=False)
            p = packs.true_odds_pack(group, cuts=False)
            g = packs.gm_pack(group, cuts=False)
            label = str(key)
            t_rows.append({"key": label, "value": t["mae"], "n": t["n"]})
            p_rows.append({"key": label, "value": p["logloss"], "n": p["n"]})
            a_rows.append({"key": label, "value": p.get("accuracy", 0), "n": p["n"]})
            g_rows.append({"key": label, "value": g["realized_gm"], "n": g["n"]})
            e_rows.append({"key": label, "value": g["exp_gm_star"], "n": g["n"]})
        out["mae_by"][col] = _sort_bars(col, t_rows)
        out["logloss_by"][col] = _sort_bars(col, p_rows)
        out["accuracy_by"][col] = _sort_bars(col, a_rows)
        out["gm_by"][col] = _sort_bars(col, g_rows)
        out["exp_gm_by"][col] = _sort_bars(col, e_rows)

    out["tg_sup_mae"] = _tg_sup_heat(work, "mae")
    out["tg_sup_logloss"] = _tg_sup_heat(work, "logloss")
    out["tg_sup_gm"] = _tg_sup_heat(work, "gm")
    out["clock_mae"] = out["mae_by"].get("clock_bin", [])
    out["clock_logloss"] = out["logloss_by"].get("clock_bin", [])
    out["clock_gm"] = out["gm_by"].get("clock_bin", [])
    return out


def _tg_sup_heat(work, kind: str) -> dict:
    if "tg_bin" not in work.columns or "sup_bin" not in work.columns:
        return {"rows": [], "cols": [], "cells": []}
    tgs = ["low", "mid", "high", "very_high"]
    sups = ["away", "balanced", "home"]
    tgs = [t for t in tgs if t in set(work["tg_bin"].astype(str))]
    sups = [s for s in sups if s in set(work["sup_bin"].astype(str))]
    values = []
    for tg in tgs:
        row = []
        for su in sups:
            g = work[(work["tg_bin"].astype(str) == tg) & (work["sup_bin"].astype(str) == su)]
            if g.empty:
                row.append(None)
                continue
            if kind == "mae":
                row.append(packs.turnover_pack(g, cuts=False)["mae"])
            elif kind == "logloss":
                row.append(packs.true_odds_pack(g, cuts=False)["logloss"])
            else:
                row.append(packs.gm_pack(g, cuts=False)["realized_gm"])
        values.append(row)
    return P.heatmap(tgs, sups, values)


def _clean_filters(filters) -> Dict[str, List[str]]:
    out = {}
    if not filters:
        return out
    for col, values in filters.items():
        if col not in FILTER_COLS:
            continue
        items = [str(v) for v in (values if isinstance(values, (list, tuple)) else [values]) if v not in (None, "", "all")]
        if items:
            out[col] = items
    return out


def _ordered(col: str, values: List[str]) -> List[str]:
    if col == "clock_bin":
        return [v for v in CLOCK_ORDER if v in values] + [v for v in values if v not in CLOCK_ORDER]
    if col == "half":
        order = ["pre", "1H", "HT", "2H", "FT"]
        return [v for v in order if v in values] + [v for v in values if v not in order]
    if col == "period":
        order = ["prematch", "inplay", "full_time"]
        return [v for v in order if v in values] + [v for v in values if v not in order]
    if col == "tg_bin":
        order = ["low", "mid", "high", "very_high"]
        return [v for v in order if v in values] + [v for v in values if v not in order]
    if col == "sup_bin":
        order = ["away", "balanced", "home"]
        return [v for v in order if v in values] + [v for v in values if v not in order]
    return sorted(values)


def _sort_bars(col: str, rows: List[dict]) -> List[dict]:
    order = {k: i for i, k in enumerate(_ordered(col, [r["key"] for r in rows]))}
    return sorted(rows, key=lambda r: (order.get(r["key"], 999), -r.get("n", 0)))


def _sort_breakdown(col: str, rows: List[dict]) -> List[dict]:
    order = {k: i for i, k in enumerate(_ordered(col, [r["key"] for r in rows]))}
    if col in ("clock_bin", "half", "period", "tg_bin", "sup_bin"):
        return sorted(rows, key=lambda r: order.get(r["key"], 999))
    return sorted(rows, key=lambda r: -r["n"])


def _stack_spec(spec) -> dict:
    spec = spec or {}
    return {
        "turnover": spec.get("turnover") or MVP_T,
        "true_prob": spec.get("true_prob") or MVP_P,
        "calibrator": spec.get("calibrator") or MVP_CAL,
        "algo": spec.get("algo") or MVP_ALGO,
    }


def _side(work: pd.DataFrame, spec: dict, group_by: str) -> dict:
    t = spec["turnover"]
    p = spec["true_prob"]
    cal = spec["calibrator"]
    algo = spec["algo"]
    n = 0 if work is None else int(len(work))
    return {
        "stack": {
            "turnover": t,
            "turnover_label": TURNOVER_LABELS.get(t, t),
            "true_prob": p,
            "true_prob_label": TRUE_PROB_LABELS.get(p, p),
            "calibrator": cal,
            "calibrator_label": CALIBRATOR_LABELS.get(cal, cal),
            "algo": algo,
            "algo_label": ALGO_LABELS.get(algo, algo),
            "id": "{}/{}/{}/{}".format(t, p, cal, algo),
            "baseline": {
                "turnover": t == MVP_T,
                "true_prob": p == MVP_P and cal == MVP_CAL,
                "algo": algo == MVP_ALGO,
            },
        },
        "turnover": packs.turnover_pack(work) if n else packs.turnover_pack(None),
        "true_odds": packs.true_odds_pack(work) if n else packs.true_odds_pack(None),
        "gm": packs.gm_pack(work) if n else packs.gm_pack(None),
        "breakdown": breakdown(work, group_by) if n else [],
    }


def _metric_delta(left, right) -> List[dict]:
    specs = (
        ("mae", "MAE", "turnover", True),
        ("mse", "MSE", "turnover", True),
        ("rmse", "RMSE", "turnover", True),
        ("wape_pct", "WAPE", "turnover", True),
        ("bias_pct", "|Bias| %", "turnover", True),
        ("r2", "R²", "turnover", False),
        ("theil_u", "Theil U", "turnover", True),
        ("share_mae", "Share MAE", "turnover", True),
        ("logloss", "Log-loss", "belief", True),
        ("brier", "Brier", "belief", True),
        ("accuracy", "Accuracy", "belief", False),
        ("ece", "ECE", "belief", True),
        ("auc", "AUC", "belief", False),
        ("bias", "Bias", "belief", True),
        ("exp_gm_star", "E[GM]", "gm", False),
        ("realized_gm", "R[GM]", "gm", False),
        ("realized_lift", "R[lift]", "gm", False),
        ("exp_lift", "E[lift]", "gm", False),
        ("hit_rate", "Hit rate", "gm", False),
        ("optimism", "Optimism", "gm", True),
    )
    packs_by = {
        "turnover": (left["turnover"], right["turnover"]),
        "belief": (left["true_odds"], right["true_odds"]),
        "gm": (left["gm"], right["gm"]),
    }
    rows = []
    for key, label, family, lower_better in specs:
        a_pack, b_pack = packs_by[family]
        av = a_pack.get(key)
        bv = b_pack.get(key)
        if av is None or bv is None:
            continue
        if key in ("bias", "bias_pct"):
            av, bv = abs(float(av)), abs(float(bv))
        delta = float(bv) - float(av)
        if abs(delta) < 1e-12:
            winner = "tie"
        elif lower_better:
            winner = "right" if delta < 0 else "left"
        else:
            winner = "right" if delta > 0 else "left"
        rows.append({
            "key": key,
            "label": label,
            "family": family,
            "a": round(float(av), 6),
            "b": round(float(bv), 6),
            "delta": round(delta, 6),
            "lower_better": lower_better,
            "winner": winner,
        })
    return rows


def _dists(left: pd.DataFrame, right: pd.DataFrame) -> dict:
    if left is None or left.empty or right is None or right.empty:
        return {}
    t_act = _num(left, "t_actual")
    t_a = _num(left, "t_hat")
    t_b = _num(right, "t_hat")
    p_a = _num(left, "true_prob")
    p_b = _num(right, "true_prob")
    y = pd.to_numeric(left.get("result"), errors="coerce").map(outcome_y).to_numpy(dtype=float)
    err_a = t_a - t_act
    err_b = t_b - t_act
    abs_a = np.abs(err_a)
    abs_b = np.abs(err_b)
    py_a = p_a - y
    py_b = p_b - y
    pair = abs_b - abs_a
    finite_pair = pair[np.isfinite(pair)]
    n = max(int(np.isfinite(finite_pair).sum()), 1)
    win_right = float(np.mean(finite_pair < -1e-9)) if len(finite_pair) else 0.0
    win_left = float(np.mean(finite_pair > 1e-9)) if len(finite_pair) else 0.0
    return {
        "t_hat": P.overlay_hist(t_a, t_b),
        "residual": P.overlay_hist(err_a, err_b),
        "abs_residual": P.overlay_hist(abs_a, abs_b),
        "true_prob": P.overlay_hist(p_a, p_b, bins=20),
        "p_minus_y": P.overlay_hist(py_a, py_b),
        "t_hat_cdf": {"a": P.ecdf(t_a), "b": P.ecdf(t_b)},
        "residual_cdf": {"a": P.ecdf(err_a), "b": P.ecdf(err_b)},
        "abs_residual_cdf": {"a": P.ecdf(abs_a), "b": P.ecdf(abs_b)},
        "true_prob_cdf": {"a": P.ecdf(p_a), "b": P.ecdf(p_b)},
        "qq_abs_residual": P.qq(abs_a, abs_b),
        "qq_true_prob": P.qq(p_a, p_b),
        "paired_abs_err": P.hist(finite_pair, bins=24),
        "row_wins": {
            "n": int(len(finite_pair)),
            "left": round(win_left, 4),
            "right": round(win_right, 4),
            "tie": round(max(0.0, 1.0 - win_left - win_right), 4),
            "mean_abs_delta": round(float(np.mean(finite_pair)) if len(finite_pair) else 0.0, 4),
        },
        "sample": n,
    }


def _num(frame, col):
    if frame is None or col not in frame.columns:
        return np.array([], dtype=float)
    return pd.to_numeric(frame[col], errors="coerce").to_numpy(dtype=float)


def _cut_delta(left_rows, right_rows) -> List[dict]:
    by_l = {r["key"]: r for r in left_rows or []}
    by_r = {r["key"]: r for r in right_rows or []}
    keys = list(dict.fromkeys(list(by_l) + list(by_r)))
    out = []
    for key in keys:
        a = by_l.get(key) or {}
        b = by_r.get(key) or {}
        out.append({
            "key": key,
            "n": int(a.get("n") or b.get("n") or 0),
            "turnover": a.get("sum_actual") or b.get("sum_actual"),
            "wape_a": a.get("wape_pct"),
            "wape_b": b.get("wape_pct"),
            "d_wape": _sub(b.get("wape_pct"), a.get("wape_pct")),
            "bias_a": a.get("bias_pct"),
            "bias_b": b.get("bias_pct"),
            "mae_a": a.get("mae"),
            "mae_b": b.get("mae"),
            "d_mae": _sub(b.get("mae"), a.get("mae")),
            "logloss_a": a.get("logloss"),
            "logloss_b": b.get("logloss"),
            "d_logloss": _sub(b.get("logloss"), a.get("logloss")),
            "accuracy_a": a.get("accuracy"),
            "accuracy_b": b.get("accuracy"),
            "d_accuracy": _sub(b.get("accuracy"), a.get("accuracy")),
            "exp_gm_a": a.get("exp_gm_star"),
            "exp_gm_b": b.get("exp_gm_star"),
            "d_exp_gm": _sub(b.get("exp_gm_star"), a.get("exp_gm_star")),
            "realized_gm_a": a.get("realized_gm"),
            "realized_gm_b": b.get("realized_gm"),
            "d_realized_gm": _sub(b.get("realized_gm"), a.get("realized_gm")),
        })
    return out


def _sub(b, a):
    if b is None or a is None:
        return None
    return round(float(b) - float(a), 6)
