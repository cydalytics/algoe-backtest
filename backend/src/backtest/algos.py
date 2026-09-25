"""
Optimizer Algorithms

The live desk has one solver: SLSQP, five starts, current trust region,
Asian-pool market cap. That is a *choice*, not the definition of the
problem. The backtest scores the same tick under several policies so
we can see whether the recommendation is coming from the search, the
constraints, or just from standing still.

    hold          post the current offer (the real baseline)
    slsqp         current solver, one start
    slsqp_multi   current solver, five starts
    coordinate    1-D line search on each free dimension
    grid          coarse TG × SUP grid per block
    tight         SLSQP inside 40% of the usual trust region
    loose         SLSQP with a doubled trust region
    no_cap        SLSQP with the market ceiling removed

Change Log:
-----------
2026-09-11      Initialize (W06 backtest labs)
"""

from contextlib import contextmanager
from typing import Dict, List

import numpy as np
import pandas as pd

from src.core import config
from src.core.logging import get_logger
from src.optimizer import optimizer as opt
from src.pipeline import DeskTick

log = get_logger(__name__)

ALGOS = (
    "hold", "slsqp", "slsqp_multi", "coordinate", "grid",
    "tight", "loose", "no_cap",
)

ALGO_LABELS = {
    "hold": "Hold current offer",
    "slsqp": "SLSQP (1 start)",
    "slsqp_multi": "SLSQP (5 starts)",
    "coordinate": "Coordinate descent",
    "grid": "Coarse TG × SUP grid",
    "tight": "SLSQP, tight trust region",
    "loose": "SLSQP, loose trust region",
    "no_cap": "SLSQP, no market cap",
}


def apply_algo(frame, pricer, name: str) -> dict:
    """Write sell_odds_star and return solver diagnostics for the tick."""
    if name not in ALGOS:
        raise ValueError("unknown algo '{}'".format(name))
    if name == "hold":
        frame.selections["sell_odds_star"] = frame.selections.get("sell_odds")
        return {"algo": name, "n_solved": 0, "n_failed": 0, "seconds": 0.0,
                "mean_abs_theta": 0.0, "n_repaired": 0, "exp_uplift": 0.0}

    tick = DeskTick(run_id="bt", as_of=frame.as_of, source=frame.source, frame=frame)
    try:
        if name == "coordinate":
            results = _search_tick(tick, kind="coordinate")
        elif name == "grid":
            results = _search_tick(tick, kind="grid")
        else:
            with _algo_config(name):
                results = opt.optimize_tick(tick, budget_seconds=90)
    except Exception:                                     # noqa: BLE001
        log.exception("algo %s failed on tick %s", name, frame.as_of)
        frame.selections["sell_odds_star"] = frame.selections.get("sell_odds")
        return {"algo": name, "n_solved": 0, "n_failed": 1, "seconds": 0.0,
                "mean_abs_theta": 0.0, "n_repaired": 0, "exp_uplift": 0.0}

    if not results:
        frame.selections["sell_odds_star"] = frame.selections.get("sell_odds")
        return {"algo": name, "n_solved": 0, "n_failed": 0, "seconds": 0.0,
                "mean_abs_theta": 0.0, "n_repaired": 0, "exp_uplift": 0.0}

    pricer.attach(
        frame,
        theta_by_match={m: r.theta_star for m, r in results.items()},
        suffix="_star",
    )
    return _diag(name, results)


def apply_policy(frame, policy: str) -> None:
    """Possibly revert a match to the current offer after a solve."""
    sel = frame.selections
    if policy in (None, "", "always") or "sell_odds_star" not in sel.columns:
        sel["policy_keep"] = True
        return
    t = pd_num(sel.get("t_hat"))
    p = pd_num(sel.get("true_prob"))
    now = pd_num(sel.get("sell_odds"))
    star = pd_num(sel.get("sell_odds_star"))
    e_now = t * (1.0 - p * now)
    e_star = t * (1.0 - p * star)
    mid = sel["match_id"]
    lift = e_star.groupby(mid).transform("sum") - e_now.groupby(mid).transform("sum")
    t_sum = t.groupby(mid).transform("sum")
    if policy == "threshold":
        keep = lift >= float(config.BACKTEST_LIFT_THRESHOLD)
    elif policy == "bps":
        bps = 1e4 * lift / t_sum.where(t_sum > 0)
        keep = bps.fillna(0.0) >= float(config.BACKTEST_LIFT_BPS)
    else:
        keep = lift >= 0
    sel.loc[~keep.to_numpy(), "sell_odds_star"] = now.loc[~keep].to_numpy()
    sel["policy_keep"] = keep


def pd_num(series):
    if series is None:
        return pd.Series(dtype=float)
    return pd.to_numeric(series, errors="coerce").fillna(0.0)


@contextmanager
def _algo_config(name):
    saved = {
        "OPTIMIZER_STARTS": config.OPTIMIZER_STARTS,
        "OPTIMIZER_WORKERS": config.OPTIMIZER_WORKERS,
        "THETA_MAX_MOVE": dict(config.THETA_MAX_MOVE),
        "MARKET_CAP_POOLS": list(config.MARKET_CAP_POOLS),
    }
    config.OPTIMIZER_WORKERS = 1
    if name == "slsqp":
        config.OPTIMIZER_STARTS = 1
    elif name == "slsqp_multi":
        config.OPTIMIZER_STARTS = 5
    elif name == "tight":
        config.OPTIMIZER_STARTS = 1
        config.THETA_MAX_MOVE = {k: v * 0.4 for k, v in config.THETA_MAX_MOVE.items()}
    elif name == "loose":
        config.OPTIMIZER_STARTS = 1
        config.THETA_MAX_MOVE = {k: v * 2.0 for k, v in config.THETA_MAX_MOVE.items()}
    elif name == "no_cap":
        config.OPTIMIZER_STARTS = 1
        config.MARKET_CAP_POOLS = []
    try:
        yield
    finally:
        for key, value in saved.items():
            setattr(config, key, value)


def _search_tick(tick, kind) -> Dict[int, opt.MatchResult]:
    """Coordinate or grid search using the same legs the SLSQP path uses."""
    out = {}
    for match_id in opt.solve_order(tick):
        ctx = tick.contexts.get(int(match_id))
        if ctx is None:
            continue
        legs, skipped = opt.legs_from_tick(tick, match_id)
        engine = opt.MatchOptimizer(
            match_id, legs, ctx.state, ctx.theta.get(config.THETA_LAYER, {})
        )
        if kind == "coordinate":
            result = _coordinate(engine)
        else:
            result = _grid(engine)
        result.skipped = skipped
        out[int(match_id)] = result
    return out


def _coordinate(engine: opt.MatchOptimizer) -> opt.MatchResult:
    """Sweep each free dimension, holding the others at the best so far."""
    started = __import__("time").time()
    theta = dict(engine.theta_base)
    for name, dims in opt.BLOCKS.items():
        legs = [leg for leg in engine.legs if leg.block == name]
        if not legs:
            continue
        free = [d for d in dims if d not in engine.frozen]
        for dim in free:
            lo, hi = engine._bounds((dim,))[0]
            xs = np.linspace(lo, hi, 9)
            best_x, best_loss = theta[dim], engine.expected_loss(theta, legs)
            for x in xs:
                trial = dict(theta)
                trial[dim] = float(x)
                loss = engine.expected_loss(trial, legs)
                if loss < best_loss:
                    best_x, best_loss = float(x), loss
            theta[dim] = best_x
    return _result_from(engine, theta, started)


def _grid(engine: opt.MatchOptimizer) -> opt.MatchResult:
    """5×5 on TG and SUP of each block; first-half dims stay at the base."""
    started = __import__("time").time()
    theta = dict(engine.theta_base)
    for name, dims in opt.BLOCKS.items():
        legs = [leg for leg in engine.legs if leg.block == name]
        if not legs:
            continue
        tg, sup = dims[0], dims[1]
        free = [d for d in (tg, sup) if d not in engine.frozen]
        if not free:
            continue
        axes = {}
        for dim in free:
            lo, hi = engine._bounds((dim,))[0]
            axes[dim] = np.linspace(lo, hi, 5)
        best = dict(theta)
        best_loss = engine.expected_loss(theta, legs)
        tg_vals = axes.get(tg, [theta[tg]])
        sup_vals = axes.get(sup, [theta[sup]])
        for a in tg_vals:
            for b in sup_vals:
                trial = dict(theta)
                if tg in axes:
                    trial[tg] = float(a)
                if sup in axes:
                    trial[sup] = float(b)
                loss = engine.expected_loss(trial, legs)
                if loss < best_loss:
                    best, best_loss = trial, loss
        theta.update({d: best[d] for d in dims})
    return _result_from(engine, theta, started)


def _result_from(engine, theta_star, started) -> opt.MatchResult:
    result = opt.MatchResult(
        match_id=engine.match_id,
        theta_now=dict(engine.theta_now),
        theta_star=dict(theta_star),
        turnover=sum(leg.turnover for leg in engine.legs),
        payout_now=engine.payout(engine.theta_now),
        payout_star=engine.payout(theta_star),
        legs=len(engine.legs),
        repaired=dict(engine.repaired),
    )
    result.seconds = round(__import__("time").time() - started, 3)
    return result


def _diag(name, results: Dict[int, opt.MatchResult]) -> dict:
    seconds = [r.seconds for r in results.values()]
    moves = []
    repaired = 0
    uplift = 0.0
    failed = 0
    for r in results.values():
        repaired += len(r.repaired or {})
        uplift += float(r.uplift)
        for dim, star in r.theta_star.items():
            now = r.theta_now.get(dim, star)
            moves.append(abs(float(star) - float(now)))
        for block in (r.blocks or {}).values():
            if not getattr(block, "success", True):
                failed += 1
    return {
        "algo": name,
        "n_solved": len(results),
        "n_failed": failed,
        "seconds": round(float(np.sum(seconds)), 3) if seconds else 0.0,
        "mean_abs_theta": round(float(np.mean(moves)), 4) if moves else 0.0,
        "n_repaired": repaired,
        "exp_uplift": round(uplift, 2),
    }


def merge_diags(rows: List[dict]) -> dict:
    if not rows:
        return {"algo": "", "n_solved": 0, "n_failed": 0, "seconds": 0.0,
                "mean_abs_theta": 0.0, "n_repaired": 0, "exp_uplift": 0.0}
    n = len(rows)
    return {
        "algo": rows[0].get("algo", ""),
        "n_solved": int(sum(r.get("n_solved", 0) for r in rows)),
        "n_failed": int(sum(r.get("n_failed", 0) for r in rows)),
        "seconds": round(sum(r.get("seconds", 0.0) for r in rows), 3),
        "mean_abs_theta": round(
            float(np.mean([r.get("mean_abs_theta", 0.0) for r in rows])), 4),
        "n_repaired": int(sum(r.get("n_repaired", 0) for r in rows)),
        "exp_uplift": round(sum(r.get("exp_uplift", 0.0) for r in rows), 2),
        "n_ticks": n,
    }
