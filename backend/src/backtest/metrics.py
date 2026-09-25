"""
Backtest Metric Packs

Four packs, one per lever the fortnight can actually change:

    turnover   forecast the next bucket — error, allocation, residual shape
    true odds  belief vs settlement — scores, calibration, favourite-longshot
    GM         expected vs realised money, risk, optimism
    algo       solver behaviour (moves, time, repairs) sitting on the GM pack

Every pack also cuts by gametime and bet type, and carries the series
the UI plots. Nested group calls pass cuts=False so we do not recurse.

Change Log:
-----------
2026-09-11      Initialize (W06 backtest)
2026-09-11      Labs: richer scores + plot payloads per lever
"""

from typing import Dict, Iterable, List, Optional

import math

import numpy as np
import pandas as pd

from src.core import config

from . import plots as P
from .settle import outcome_y, payout_unit_vec


# ---------------------------------------------------------------------------
# Turnover
# ---------------------------------------------------------------------------

def turnover_pack(frame: pd.DataFrame,
                  pred="t_hat", actual="t_actual", cuts=True) -> dict:
    """Forecast vs the next 5-minute bucket."""
    if frame is None or frame.empty:
        return _empty_turnover()
    pred_v = pd.to_numeric(frame[pred], errors="coerce")
    act_v = pd.to_numeric(frame[actual], errors="coerce")
    mask = pred_v.notna() & act_v.notna()
    if not mask.any():
        return _empty_turnover()
    p = pred_v[mask].to_numpy(dtype=float)
    a = act_v[mask].to_numpy(dtype=float)
    err = p - a
    n = int(len(err))
    abs_err = np.abs(err)
    mae = float(np.mean(abs_err))
    mse = float(np.mean(err ** 2))
    rmse = float(np.sqrt(mse))
    mean_diff = float(np.mean(err))
    pos = a > 0
    mape = float(np.mean(abs_err[pos] / a[pos]) * 100.0) if pos.any() else 0.0
    mdape = float(np.median(abs_err[pos] / a[pos]) * 100.0) if pos.any() else 0.0
    denom = np.abs(p) + np.abs(a)
    smape = float(np.mean(2.0 * abs_err[denom > 0] / denom[denom > 0]) * 100.0) if (denom > 0).any() else 0.0
    total_a = float(a.sum())
    total_p = float(p.sum())
    bias_pct = float((total_p - total_a) / total_a * 100.0) if total_a > 0 else 0.0
    wape = float(np.sum(abs_err) / total_a * 100.0) if total_a > 0 else 0.0
    wmae = float(np.average(abs_err, weights=np.clip(a, 0, None))) if total_a > 0 else mae
    naive = None
    if cuts and "t5m" in frame.columns:
        naive = pd.to_numeric(frame.loc[mask, "t5m"], errors="coerce").fillna(0.0).to_numpy()
    theil = _theil_u(p, a, naive)
    share = _share_errors(p, a)
    hit = _zero_hit(p, a)
    out = {
        "n": n,
        "mae": round(mae, 4),
        "mse": round(mse, 4),
        "rmse": round(rmse, 4),
        "mape_pct": round(mape, 4),
        "mdape_pct": round(mdape, 4),
        "smape_pct": round(smape, 4),
        "wape_pct": round(wape, 4),
        "wmae": round(wmae, 4),
        "mean_diff": round(mean_diff, 4),
        "bias_pct": round(bias_pct, 4),
        "sum_pred": round(total_p, 2),
        "sum_actual": round(total_a, 2),
        "r2": round(_r2(a, p), 4),
        "pearson": round(_corr(p, a), 4),
        "spearman": round(_spearman(p, a), 4),
        "theil_u": round(theil, 4),
        "residual_std": round(float(np.std(err)), 4),
        "residual_skew": round(_skew(err), 4),
        "share_mae": share["share_mae"],
        "cosine": share["cosine"],
        "top10_overlap": share["top10_overlap"],
        "hit_precision": hit["precision"],
        "hit_recall": hit["recall"],
        "deciles": _decile_bias(p, a) if cuts else [],
        "by_phase": _group_turnover(frame.loc[mask], "phase", pred, actual) if cuts else [],
        "by_pool": _group_turnover(frame.loc[mask], "pool_code", pred, actual) if cuts else [],
        "by_family": _group_turnover(frame.loc[mask], "family", pred, actual) if cuts else [],
    }
    if cuts:
        out["match_mae"] = _match_mae(frame.loc[mask], pred, actual)
        out["plots"] = _turnover_plots(frame.loc[mask], p, a, err)
    return out


def _empty_turnover():
    return {
        "n": 0, "mae": 0.0, "mse": 0.0, "rmse": 0.0, "mape_pct": 0.0,
        "mdape_pct": 0.0, "smape_pct": 0.0, "wape_pct": 0.0, "wmae": 0.0,
        "mean_diff": 0.0, "bias_pct": 0.0, "sum_pred": 0.0, "sum_actual": 0.0,
        "r2": 0.0, "pearson": 0.0, "spearman": 0.0, "theil_u": 0.0,
        "residual_std": 0.0, "residual_skew": 0.0,
        "share_mae": 0.0, "cosine": 0.0, "top10_overlap": 0.0,
        "hit_precision": 0.0, "hit_recall": 0.0, "match_mae": 0.0,
        "deciles": [], "by_phase": [], "by_pool": [], "by_family": [],
        "plots": {},
    }


def _turnover_plots(frame, p, a, err) -> dict:
    ticks = []
    if "as_of" in frame.columns:
        work = frame.assign(_p=p, _a=a, _e=np.abs(err))
        for key, g in work.groupby("as_of", sort=True):
            ticks.append({
                "key": str(key)[11:16] if len(str(key)) >= 16 else str(key),
                "mae": round(float(g["_e"].mean()), 2),
                "bias": round(float((g["_p"] - g["_a"]).mean()), 2),
                "n": int(len(g)),
            })
    clock = []
    if "minute" in frame.columns:
        minute = pd.to_numeric(frame["minute"], errors="coerce")
        clock = P.bins_xy(minute.to_numpy(), err, bins=10)
    return {
        "scatter": P.sample_xy(p, a, seed=3),
        "residual_vs_pred": P.bins_xy(p, err, bins=10),
        "residual_hist": P.hist(err, bins=20),
        "mae_by_tick": ticks,
        "mae_by_phase": [{"key": r["key"], "value": r["mae"], "n": r["n"]}
                         for r in _group_turnover(frame, "phase", "t_hat", "t_actual")],
        "mae_by_pool": [{"key": r["key"], "value": r["mae"], "n": r["n"]}
                        for r in _group_turnover(frame, "pool_code", "t_hat", "t_actual")[:12]],
        "deciles": [
            {"key": "D{}".format(d["decile"] + 1), "pred": d["mean_pred"],
             "actual": d["mean_actual"], "bias": d["bias"], "n": d["n"]}
            for d in _decile_bias(p, a)
        ],
        "residual_vs_clock": clock,
    }


def _decile_bias(pred, actual, bins=10) -> List[dict]:
    if len(pred) < bins:
        return []
    try:
        labels = pd.qcut(pred, bins, labels=False, duplicates="drop")
    except ValueError:
        return []
    out = []
    for i in sorted(set(int(x) for x in labels if pd.notna(x))):
        take = labels == i
        pv, av = pred[take], actual[take]
        out.append({
            "decile": int(i),
            "n": int(take.sum()),
            "mean_pred": round(float(pv.mean()), 2),
            "mean_actual": round(float(av.mean()), 2),
            "bias": round(float(pv.mean() - av.mean()), 2),
        })
    return out


def _group_turnover(frame, col, pred, actual) -> List[dict]:
    if col not in frame.columns:
        return []
    rows = []
    for key, group in frame.groupby(col, dropna=False):
        pack = turnover_pack(group, pred, actual, cuts=False)
        rows.append({
            "key": str(key),
            "n": pack["n"],
            "mae": pack["mae"],
            "mse": pack["mse"],
            "mape_pct": pack["mape_pct"],
            "bias_pct": pack["bias_pct"],
            "mean_diff": pack["mean_diff"],
            "share_mae": pack["share_mae"],
        })
    return sorted(rows, key=lambda r: -r["n"])


def _match_mae(frame, pred, actual) -> float:
    if "match_id" not in frame.columns:
        return 0.0
    g = frame.groupby("match_id")[[pred, actual]].sum()
    return round(float((g[pred] - g[actual]).abs().mean()), 4)


def _share_errors(p, a) -> dict:
    ps = p / p.sum() if p.sum() > 0 else np.zeros_like(p)
    a_s = a / a.sum() if a.sum() > 0 else np.zeros_like(a)
    share_mae = float(np.mean(np.abs(ps - a_s)))
    denom = float(np.linalg.norm(p) * np.linalg.norm(a))
    cosine = float(np.dot(p, a) / denom) if denom > 0 else 0.0
    if len(a) < 10 or a.sum() <= 0:
        overlap = 0.0
    else:
        k = max(1, int(round(0.1 * len(a))))
        top_a = set(np.argsort(a)[-k:])
        top_p = set(np.argsort(p)[-k:])
        overlap = len(top_a & top_p) / float(k)
    return {
        "share_mae": round(share_mae, 6),
        "cosine": round(cosine, 4),
        "top10_overlap": round(overlap, 4),
    }


def _zero_hit(p, a, floor=None):
    floor = config.TURNOVER_FLOOR if floor is None else floor
    pred_on = p > floor
    act_on = a > floor
    tp = float((pred_on & act_on).sum())
    fp = float((pred_on & ~act_on).sum())
    fn = float((~pred_on & act_on).sum())
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    return {"precision": round(prec, 4), "recall": round(rec, 4)}


def _theil_u(p, a, naive):
    mse = float(np.mean((p - a) ** 2))
    if naive is None or len(naive) != len(a):
        return 0.0
    base = float(np.mean((naive - a) ** 2))
    if base <= 1e-12:
        return 0.0
    return float(np.sqrt(mse / base))


# ---------------------------------------------------------------------------
# True odds
# ---------------------------------------------------------------------------

def true_odds_pack(frame: pd.DataFrame,
                   prob="true_prob", result="result", cuts=True) -> dict:
    """Belief vs the settled outcome."""
    if frame is None or frame.empty:
        return _empty_true()
    p = pd.to_numeric(frame[prob], errors="coerce")
    r = pd.to_numeric(frame[result], errors="coerce")
    y = r.map(outcome_y)
    mask = p.notna() & y.notna() & (p > 0) & (p < 1)
    if not mask.any():
        return _empty_true()
    pv = p[mask].to_numpy(dtype=float)
    yv = y[mask].to_numpy(dtype=float)
    pv = np.clip(pv, 1e-6, 1.0 - 1e-6)
    logloss = float(-np.mean(yv * np.log(pv) + (1.0 - yv) * np.log(1.0 - pv)))
    brier = float(np.mean((pv - yv) ** 2))
    spherical = float(np.mean((pv * yv + (1 - pv) * (1 - yv)) / np.sqrt(pv ** 2 + (1 - pv) ** 2)))
    bias = float(np.mean(pv - yv))
    decomp = _brier_decomp(pv, yv)
    ece, mce = _ece_mce(pv, yv)
    ace = _ace(pv, yv)
    slope, intercept = _cox(pv, yv)
    book = _book_coherence(frame.loc[mask])
    out = {
        "n": int(mask.sum()),
        "logloss": round(logloss, 6),
        "brier": round(brier, 6),
        "accuracy": round(_hard_accuracy(pv, yv), 4),
        "spherical": round(spherical, 6),
        "bias": round(bias, 6),
        "mean_prob": round(float(pv.mean()), 6),
        "mean_y": round(float(yv.mean()), 6),
        "ece": round(ece, 6),
        "mce": round(mce, 6),
        "ace": round(ace, 6),
        "sharpness": round(float(np.var(pv)), 6),
        "auc": round(_auc(pv, yv), 4),
        "cal_slope": round(slope, 4),
        "cal_intercept": round(intercept, 4),
        "brier_reliability": decomp["reliability"],
        "brier_resolution": decomp["resolution"],
        "brier_uncertainty": decomp["uncertainty"],
        "book_abs": book["abs"],
        "book_bad_pct": book["bad_pct"],
        "calibration": _calibration(pv, yv) if cuts else [],
        "by_phase": _group_true(frame.loc[mask], "phase", prob, result) if cuts else [],
        "by_pool": _group_true(frame.loc[mask], "pool_code", prob, result) if cuts else [],
        "by_family": _group_true(frame.loc[mask], "family", prob, result) if cuts else [],
    }
    if cuts:
        out["plots"] = _true_plots(pv, yv, frame.loc[mask], out)
    return out


def _empty_true():
    return {
        "n": 0, "logloss": 0.0, "brier": 0.0, "accuracy": 0.0, "spherical": 0.0, "bias": 0.0,
        "mean_prob": 0.0, "mean_y": 0.0, "ece": 0.0, "mce": 0.0, "ace": 0.0,
        "sharpness": 0.0, "auc": 0.5, "cal_slope": 0.0, "cal_intercept": 0.0,
        "brier_reliability": 0.0, "brier_resolution": 0.0, "brier_uncertainty": 0.0,
        "book_abs": 0.0, "book_bad_pct": 0.0,
        "calibration": [], "by_phase": [], "by_pool": [], "by_family": [],
        "plots": {},
    }


def _true_plots(pv, yv, frame, pack) -> dict:
    return {
        "reliability": pack.get("calibration") or _calibration(pv, yv),
        "reliability_equal_mass": _calibration_mass(pv, yv),
        "favourite_longshot": _favourite_longshot(pv, yv),
        "residual_hist": P.hist(pv - yv, bins=20),
        "brier_decomp": [
            {"key": "Uncertainty", "value": pack["brier_uncertainty"]},
            {"key": "Reliability", "value": pack["brier_reliability"]},
            {"key": "Resolution", "value": pack["brier_resolution"]},
        ],
        "ece_by_pool": [
            {"key": r["key"], "value": r.get("ece", 0.0), "n": r["n"]}
            for r in pack.get("by_pool", [])[:12]
        ],
        "logloss_by_phase": [
            {"key": r["key"], "value": r["logloss"], "n": r["n"]}
            for r in pack.get("by_phase", [])
        ],
        "book_sum_hist": P.hist(
            pd.to_numeric(frame["book_sum"], errors="coerce").dropna(),
            bins=16,
        ) if "book_sum" in frame.columns else [],
    }


def _calibration(prob, y, bins=None) -> List[dict]:
    bins = bins or config.BACKTEST_CAL_BINS
    edges = np.linspace(0.0, 1.0, bins + 1)
    out = []
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        take = (prob >= lo) & (prob <= hi) if i == bins - 1 else (prob >= lo) & (prob < hi)
        if not take.any():
            out.append({"lo": round(float(lo), 3), "hi": round(float(hi), 3),
                        "n": 0, "p_hat": None, "p_obs": None})
            continue
        out.append({
            "lo": round(float(lo), 3), "hi": round(float(hi), 3),
            "n": int(take.sum()),
            "p_hat": round(float(prob[take].mean()), 4),
            "p_obs": round(float(y[take].mean()), 4),
        })
    return out


def _quantile_bins(values, y, bins, extra=None):
    """One groupby pass over equal-mass bins of `values`. None when unbinnable.

    `extra` names a second series to average per bin (e.g. probability when the
    bins themselves are cut on odds).
    """
    values = np.asarray(values, dtype=float)
    try:
        labels = pd.qcut(values, bins, labels=False, duplicates="drop")
    except ValueError:
        return None
    k = np.asarray(labels, dtype=float)
    keep = ~np.isnan(k)
    if not keep.any():
        return None

    idx = k[keep].astype(np.intp)
    v = values[keep]
    nb = int(idx.max()) + 1
    n = np.bincount(idx, minlength=nb).astype(float)
    live = n > 0

    out = {
        "n": n,
        "v_hat": _bin_mean(idx, v, nb, n),
        "p_obs": _bin_mean(idx, np.asarray(y, dtype=float)[keep], nb, n),
    }
    if extra is not None:
        out["x_hat"] = _bin_mean(idx, np.asarray(extra, dtype=float)[keep], nb, n)

    # qcut labels rise with value, so sorting the values by itself lays the bins
    # out as contiguous runs and the run edges give lo/hi without a per-bin scan.
    v_sorted = np.sort(v)
    counts = n[live].astype(np.intp)
    starts = np.concatenate(([0], np.cumsum(counts)[:-1]))
    lo = np.full(nb, np.nan)
    hi = np.full(nb, np.nan)
    lo[live] = v_sorted[starts]
    hi[live] = v_sorted[starts + counts - 1]
    out["lo"] = lo
    out["hi"] = hi

    frame = pd.DataFrame(out, index=pd.Index(np.arange(nb), name="k"))
    return frame[live]


def _bin_mean(idx, vals, nb, n):
    total = np.bincount(idx, weights=vals, minlength=nb)
    return np.divide(total, n, out=np.zeros(nb), where=n > 0)


def _calibration_mass(prob, y, bins=10) -> List[dict]:
    if len(prob) < bins:
        return []
    agg = _quantile_bins(prob, y, bins)
    if agg is None:
        return []
    return [
        {
            "lo": round(float(r.lo), 3),
            "hi": round(float(r.hi), 3),
            "n": int(r.n),
            "p_hat": round(float(r.v_hat), 4),
            "p_obs": round(float(r.p_obs), 4),
        }
        for r in agg.itertuples()
    ]


def _favourite_longshot(prob, y, bins=8) -> List[dict]:
    """Bias against 1/p — the classic favourite-longshot curve."""
    odds = 1.0 / np.clip(prob, 1e-6, 1.0)
    if len(odds) < bins:
        return []
    # bins are cut on odds, but p_hat is the mean probability inside the bin
    agg = _quantile_bins(odds, y, bins, extra=prob)
    if agg is None:
        return []
    out = []
    for r in agg.itertuples():
        ph, po = float(r.x_hat), float(r.p_obs)
        out.append({
            "key": "D{}".format(int(r.Index) + 1),
            "odds": round(float(r.v_hat), 2),
            "p_hat": round(ph, 4),
            "p_obs": round(po, 4),
            "bias": round(ph - po, 4),
            "n": int(r.n),
        })
    return out


def _ece_mce(prob, y, bins=None):
    rows = _calibration(prob, y, bins)
    live = [r for r in rows if r["n"] > 0 and r["p_hat"] is not None]
    n = sum(r["n"] for r in live) or 1
    gaps = [abs(r["p_hat"] - r["p_obs"]) for r in live]
    ece = sum(r["n"] * g for r, g in zip(live, gaps)) / n
    mce = max(gaps) if gaps else 0.0
    return float(ece), float(mce)


def _ace(prob, y, bins=10):
    rows = _calibration_mass(prob, y, bins)
    live = [r for r in rows if r["n"] > 0]
    if not live:
        return 0.0
    n = sum(r["n"] for r in live)
    return float(sum(r["n"] * abs(r["p_hat"] - r["p_obs"]) for r in live) / n)


def _brier_decomp(p, y, bins=10):
    """Murphy: Brier = reliability − resolution + uncertainty."""
    ybar = float(np.mean(y))
    uncertainty = ybar * (1.0 - ybar)
    agg = _quantile_bins(p, y, bins)
    if agg is None:
        return {"reliability": 0.0, "resolution": 0.0, "uncertainty": round(uncertainty, 6)}
    nk = agg["n"].to_numpy(dtype=float)
    pk = agg["v_hat"].to_numpy(dtype=float)
    ok = agg["p_obs"].to_numpy(dtype=float)
    rel = float(np.sum(nk * (pk - ok) ** 2))
    res = float(np.sum(nk * (ok - ybar) ** 2))
    n = float(len(p))
    return {
        "reliability": round(rel / n, 6),
        "resolution": round(res / n, 6),
        "uncertainty": round(uncertainty, 6),
    }


def _cox(p, y):
    """Linear calibration: y ≈ a + b p. Perfect is a=0, b=1."""
    if np.var(p) < 1e-12:
        return 0.0, float(np.mean(y))
    b = float(np.cov(p, y, ddof=0)[0, 1] / np.var(p))
    a = float(np.mean(y) - b * np.mean(p))
    return b, a


def _hard_accuracy(p, y):
    """Share of rows where (p>0.5) matches (y>0.5). Half-wins dropped."""
    hard = np.where(y > 0.5 + 1e-9, 1.0, np.where(y < 0.5 - 1e-9, 0.0, np.nan))
    mask = np.isfinite(hard)
    if not mask.any():
        return 0.0
    return float(np.mean((p[mask] > 0.5) == (hard[mask] == 1.0)))


def _auc(p, y):
    """ROC-AUC on hard labels; half-wins dropped."""
    hard = np.where(y > 0.5 + 1e-9, 1.0, np.where(y < 0.5 - 1e-9, 0.0, np.nan))
    mask = np.isfinite(hard)
    p, hard = p[mask], hard[mask]
    pos = p[hard == 1]
    neg = p[hard == 0]
    if len(pos) == 0 or len(neg) == 0:
        return 0.5
    # Mann–Whitney
    ranks = pd.Series(p).rank(method="average").to_numpy()
    sum_pos = float(ranks[hard == 1].sum())
    auc = (sum_pos - len(pos) * (len(pos) + 1) / 2.0) / (len(pos) * len(neg))
    return float(np.clip(auc, 0.0, 1.0))


def _book_coherence(frame) -> dict:
    if "book_sum" not in frame.columns:
        return {"abs": 0.0, "bad_pct": 0.0}
    keys = [k for k in ("match_id", "pool_id", "line_id") if k in frame.columns]
    if not keys:
        s = pd.to_numeric(frame["book_sum"], errors="coerce").dropna()
    else:
        s = frame.groupby(keys)["book_sum"].first()
        s = pd.to_numeric(s, errors="coerce").dropna()
    if s.empty:
        return {"abs": 0.0, "bad_pct": 0.0}
    gap = (s - 1.0).abs()
    bad = float((gap > config.BOOK_SUM_TOLERANCE).mean() * 100.0)
    return {"abs": round(float(gap.mean()), 4), "bad_pct": round(bad, 2)}


def _group_true(frame, col, prob, result) -> List[dict]:
    if col not in frame.columns:
        return []
    rows = []
    for key, group in frame.groupby(col, dropna=False):
        pack = true_odds_pack(group, prob, result, cuts=False)
        rows.append({
            "key": str(key),
            "n": pack["n"],
            "logloss": pack["logloss"],
            "brier": pack["brier"],
            "accuracy": pack.get("accuracy", 0),
            "bias": pack["bias"],
            "ece": pack["ece"],
        })
    return sorted(rows, key=lambda r: -r["n"])


# ---------------------------------------------------------------------------
# GM
# ---------------------------------------------------------------------------

def gm_pack(frame: pd.DataFrame, cuts=True) -> dict:
    """Expected and realized margin at the posted offer vs the action we took."""
    if frame is None or frame.empty:
        return _empty_gm()

    t_hat = _series(frame, "t_hat")
    t_act = _series(frame, "t_actual")
    t_hat_star = _series(frame, "t_hat_star", t_hat)
    t_act_star = _series(frame, "t_actual_star", t_act)
    p = _series(frame, "true_prob")
    odds_now = _series(frame, "sell_odds")
    odds_star = _series(frame, "sell_odds_star", odds_now)
    result = pd.to_numeric(frame.get("result"), errors="coerce")

    e_now = t_hat * (1.0 - p * odds_now)
    e_star = t_hat_star * (1.0 - p * odds_star)

    pay_now = pd.Series(payout_unit_vec(odds_now, result), index=frame.index)
    pay_star = pd.Series(payout_unit_vec(odds_star, result), index=frame.index)
    settled = result.notna()
    r_now = (t_act - t_act * pay_now).where(settled)
    r_star = (t_act_star - t_act_star * pay_star).where(settled)

    day_counts = _better_worse(frame, r_now, r_star, "day")
    tick_counts = _better_worse(frame, r_now, r_star, "as_of")
    tick_lifts = _group_lifts(frame, r_now, r_star, "as_of")

    exp_now = float(e_now.sum())
    exp_star = float(e_star.sum())
    real_now = float(r_now.fillna(0.0).sum())
    real_star = float(r_star.fillna(0.0).sum())
    turnover = float(t_act_star.where(settled, 0.0).sum())
    lifts = np.asarray([row["lift"] for row in tick_lifts], dtype=float) if tick_lifts else np.array([0.0])
    out = {
        "n": int(len(frame)),
        "n_settled": int(settled.sum()),
        "exp_gm_now": round(exp_now, 2),
        "exp_gm_star": round(exp_star, 2),
        "exp_lift": round(exp_star - exp_now, 2),
        "realized_gm_now": round(real_now, 2),
        "realized_gm_star": round(real_star, 2),
        "realized_gm": round(real_star, 2),
        "realized_lift": round(real_star - real_now, 2),
        "realized_lift_bps": round(
            1e4 * (real_star - real_now) / turnover, 1) if turnover > 0 else 0.0,
        "optimism": round((exp_star - exp_now) - (real_star - real_now), 2),
        "hit_rate": _hit_rate(tick_counts),
        "tick_vol": round(float(np.std(lifts)), 2),
        "cvar_5": round(_cvar(lifts, 0.05), 2),
        "worst_tick": round(float(np.min(lifts)), 2),
        "days_better": day_counts["better"],
        "days_worse": day_counts["worse"],
        "days_tie": day_counts["tie"],
        "ticks_better": tick_counts["better"],
        "ticks_worse": tick_counts["worse"],
        "ticks_tie": tick_counts["tie"],
        "by_phase": _group_gm(frame, "phase") if cuts else [],
        "by_pool": _group_gm(frame, "pool_code") if cuts else [],
        "by_family": _group_gm(frame, "family") if cuts else [],
        "by_day": _group_gm(frame, "day") if cuts else [],
    }
    if cuts:
        out["plots"] = _gm_plots(frame, e_now, e_star, r_now, r_star, tick_lifts, out)
    return out


def _empty_gm():
    return {
        "n": 0, "n_settled": 0,
        "exp_gm_now": 0.0, "exp_gm_star": 0.0, "exp_lift": 0.0,
        "realized_gm_now": 0.0, "realized_gm_star": 0.0, "realized_gm": 0.0,
        "realized_lift": 0.0, "realized_lift_bps": 0.0,
        "optimism": 0.0, "hit_rate": 0.0, "tick_vol": 0.0,
        "cvar_5": 0.0, "worst_tick": 0.0,
        "days_better": 0, "days_worse": 0, "days_tie": 0,
        "ticks_better": 0, "ticks_worse": 0, "ticks_tie": 0,
        "by_phase": [], "by_pool": [], "by_family": [], "by_day": [],
        "plots": {},
    }


def _gm_plots(frame, e_now, e_star, r_now, r_star, tick_lifts, pack) -> dict:
    exp_vs = []
    if "as_of" in frame.columns:
        work = pd.DataFrame(
            {
                "e": (e_star - e_now).to_numpy(),
                "r": (r_star.fillna(0.0) - r_now.fillna(0.0)).to_numpy(),
            },
            index=frame.index,
        )
        agg = work.groupby(frame["as_of"], sort=True, observed=True).sum()
        exp_vs = [
            {"key": _tick_key(key), "x": round(float(e), 2), "y": round(float(r), 2)}
            for key, e, r in zip(agg.index, agg["e"], agg["r"])
        ]
    plotted = _thin(tick_lifts)
    return {
        "lift_by_tick": [
            {"key": r["key"], "value": r["lift"], "exp": r["exp"]} for r in plotted
        ],
        "exp_vs_realized": exp_vs,
        "better_worse": [
            {"key": "Days +", "value": pack["days_better"]},
            {"key": "Days −", "value": pack["days_worse"]},
            {"key": "Ticks +", "value": pack["ticks_better"]},
            {"key": "Ticks −", "value": pack["ticks_worse"]},
        ],
        "lift_by_pool": [
            {"key": r["key"], "value": r["realized_lift"], "n": r["n"]}
            for r in pack.get("by_pool", [])[:12]
        ],
        "lift_by_phase": [
            {"key": r["key"], "value": r["realized_lift"], "n": r["n"]}
            for r in pack.get("by_phase", [])
        ],
        "optimism_by_tick": [
            {"key": r["key"], "value": round(r["exp"] - r["lift"], 2)} for r in plotted
        ],
    }


# A real 5-minute panel has thousands of buckets. The stats above stay at full
# bucket resolution; the plotted series folds consecutive buckets so the chart
# and the JSON payload stay readable.
PLOT_POINTS = 240


def _thin(rows: List[dict]) -> List[dict]:
    if len(rows) <= PLOT_POINTS:
        return rows
    step = math.ceil(len(rows) / PLOT_POINTS)
    out = []
    for i in range(0, len(rows), step):
        chunk = rows[i:i + step]
        out.append({
            "key": chunk[0]["key"],
            "lift": round(sum(r["lift"] for r in chunk), 2),
            "exp": round(sum(r["exp"] for r in chunk), 2),
        })
    return out


def _lift_frame(frame, r_now, r_star, col) -> pd.DataFrame:
    """Per-group expected and realized lift, summed with one groupby pass."""
    t_hat = _series(frame, "t_hat")
    t_hat_star = _series(frame, "t_hat_star", t_hat)
    p = _series(frame, "true_prob")
    odds_now = _series(frame, "sell_odds")
    odds_star = _series(frame, "sell_odds_star", odds_now)
    work = pd.DataFrame(
        {
            "lift": r_star.fillna(0.0).to_numpy() - r_now.fillna(0.0).to_numpy(),
            "exp": (t_hat_star * (1.0 - p * odds_star)).to_numpy()
            - (t_hat * (1.0 - p * odds_now)).to_numpy(),
        },
        index=frame.index,
    )
    return work.groupby(frame[col], sort=True, dropna=False, observed=True).sum()


def _better_worse(frame, r_now, r_star, col) -> Dict[str, int]:
    if col not in frame.columns:
        return {"better": 0, "worse": 0, "tie": 0}
    lift = _lift_frame(frame, r_now, r_star, col)["lift"].to_numpy()
    better = int((lift > 1e-6).sum())
    worse = int((lift < -1e-6).sum())
    return {"better": better, "worse": worse, "tie": int(lift.size - better - worse)}


def _tick_key(key) -> str:
    label = str(key)
    return label[11:16] if len(label) >= 16 and "T" in label else label


def _group_lifts(frame, r_now, r_star, col) -> List[dict]:
    if col not in frame.columns:
        return []
    agg = _lift_frame(frame, r_now, r_star, col)
    return [
        {"key": _tick_key(key), "lift": round(float(lift), 2), "exp": round(float(exp), 2)}
        for key, lift, exp in zip(agg.index, agg["lift"], agg["exp"])
    ]


def _group_gm(frame, col) -> List[dict]:
    if col not in frame.columns:
        return []
    rows = []
    for key, group in frame.groupby(col, dropna=False, observed=True):
        pack = gm_pack(group, cuts=False)
        rows.append({
            "key": str(key),
            "n": pack["n"],
            "exp_lift": pack["exp_lift"],
            "realized_lift": pack["realized_lift"],
            "realized_gm_now": pack["realized_gm_now"],
            "realized_gm_star": pack["realized_gm_star"],
            "realized_gm": pack["realized_gm"],
        })
    return sorted(rows, key=lambda r: -abs(r["realized_lift"]))


def _hit_rate(counts) -> float:
    decided = counts["better"] + counts["worse"]
    if decided <= 0:
        return 0.0
    return round(counts["better"] / decided, 4)


def _cvar(values, q):
    if len(values) == 0:
        return 0.0
    cut = np.quantile(values, q)
    tail = values[values <= cut]
    return float(tail.mean()) if len(tail) else float(cut)


# ---------------------------------------------------------------------------
# Rankings
# ---------------------------------------------------------------------------

def leaderboard(combos: Iterable[dict]) -> dict:
    """Rank a flat list of combo reports (legacy cartesian sweep)."""
    rows = list(combos)
    turn = sorted(rows, key=lambda c: (c["turnover"]["mae"], c["id"]))
    odds = sorted(rows, key=lambda c: (c["true_odds"]["logloss"], c["id"]))
    gm = sorted(rows, key=lambda c: (-c["gm"].get("realized_gm", c["gm"]["realized_lift"]), c["id"]))
    return {
        "turnover": [_lead(c, "mae", c["turnover"]["mae"]) for c in turn],
        "true_odds": [_lead(c, "logloss", c["true_odds"]["logloss"]) for c in odds],
        "gm": [_lead(c, "realized_gm", c["gm"].get("realized_gm", 0.0)) for c in gm],
    }


def _lead(combo, metric, value):
    return {
        "id": combo["id"],
        "turnover": combo.get("turnover_model"),
        "true_prob": combo.get("true_prob_source"),
        "metric": metric,
        "value": value,
        "n": combo.get("turnover", {}).get("n", 0),
    }


def _series(frame, name, fallback=None):
    if name in frame.columns:
        s = pd.to_numeric(frame[name], errors="coerce")
        if fallback is None:
            return s.fillna(0.0)
        return s.fillna(fallback)
    if fallback is not None:
        return fallback
    return pd.Series(0.0, index=frame.index)


# ---------------------------------------------------------------------------
# Small stats
# ---------------------------------------------------------------------------

def _corr(x, y):
    if len(x) < 3 or np.std(x) < 1e-12 or np.std(y) < 1e-12:
        return 0.0
    return float(np.corrcoef(x, y)[0, 1])


def _spearman(x, y):
    if len(x) < 3:
        return 0.0
    rx = pd.Series(x).rank().to_numpy()
    ry = pd.Series(y).rank().to_numpy()
    return _corr(rx, ry)


def _r2(actual, pred):
    ss_res = float(np.sum((actual - pred) ** 2))
    ss_tot = float(np.sum((actual - actual.mean()) ** 2))
    if ss_tot < 1e-12:
        return 0.0
    return 1.0 - ss_res / ss_tot


def _skew(x):
    x = np.asarray(x, dtype=float)
    if len(x) < 4 or np.std(x) < 1e-12:
        return 0.0
    m = x.mean()
    s = x.std()
    return float(np.mean(((x - m) / s) ** 3))
