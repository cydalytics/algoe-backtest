"""
Backtest Runner

One meeting, labelled once, then four labs. Each lab moves one lever
and holds the others at a stated baseline, so a better turnover number
is not confused with a better belief, and a better belief is not
confused with a solver that just lengthened every price.

    turnover   sweep demand models, belief frozen, no reprice
    true_prob  sweep sources × calibrators, demand frozen, no reprice
    optimizer  sweep algos on the MVP stack
    policy     sweep update rule × elasticity on one solved stack

Attribution then adds the winners one at a time so the meeting can see
which lever actually moved realised GM. A small T × P heatmap at the
current offer shows whether a better forecast even matters if we never
touch the price.

The old cartesian ``optimize=star|all|none`` flag still works: it
selects which algos the optimizer lab runs.

Change Log:
-----------
2026-09-11      Initialize (W06 backtest)
2026-09-11      Labs: independent levers + attribution
2026-09-12      Wide fact book for the workbench slice
"""

import time
from datetime import datetime
from typing import List, Optional, Sequence

import numpy as np
import pandas as pd

from src.core import config
from src.core.logging import get_logger
from src.features.preprocessor import Preprocessor
from src.models.pricer import Pricer

from . import metrics as packs
from .algos import ALGO_LABELS, apply_algo, apply_policy, merge_diags
from .calibrators import CALIBRATOR_LABELS, make_calibrator
from .demand import attach_counterfactual
from .facts import FactBook
from .labels import attach_labels, copy_frame
from .plots import heatmap
from .predictors import (
    POLICY_LABELS, TRUE_PROB_LABELS, TURNOVER_LABELS, catalog,
    default_algos, default_calibrators, default_elasticities,
    default_policies, default_true_prob, default_turnover,
    make_true_prob, make_turnover, walk_forward_profile,
)
from .settle import outcome_y
from . import slice as slice_cache
from .store import get_backtest_store
from .world import build_world

log = get_logger(__name__)

MVP_T = "persistence"
MVP_P = "hkjc_true"
MVP_CAL = "raw"
MVP_ALGO = "hold"


def run_backtest(
    seed=None,
    n_matches=None,
    n_ticks=None,
    start=None,
    turnover=None,
    true_prob=None,
    calibrators=None,
    algos=None,
    policies=None,
    elasticities=None,
    optimize="star",
    persist=True,
    progress=None,
) -> dict:
    """Run the lab sweep and return a JSON-serialisable report."""
    started = time.time()
    seed = config.SIM_SEED if seed is None else int(seed)
    n_matches = config.BACKTEST_MATCHES if n_matches is None else int(n_matches)
    n_ticks = config.BACKTEST_TICKS if n_ticks is None else int(n_ticks)
    turnover = list(turnover or default_turnover())
    true_prob = list(true_prob or default_true_prob())
    calibrators = list(calibrators or default_calibrators())
    policies = list(policies or default_policies())
    elasticities = [float(x) for x in (elasticities or default_elasticities())]
    algos = _resolve_algos(algos, optimize)

    def note(msg, frac=None):
        log.info(msg)
        if progress:
            progress(msg, frac)

    note("building meeting world ({} matches, {} ticks)".format(
        n_matches, n_ticks), 0.02)
    world = build_world(seed=seed, n_matches=n_matches, n_ticks=n_ticks, start=start)
    labeled = _label_meeting(world, note)
    if not labeled:
        raise RuntimeError("backtest produced no labelled ticks")
    pricer = Pricer()

    facts = FactBook()
    facts.ingest_base(labeled)

    note("turnover lab", 0.30)
    t_lab = _turnover_lab(labeled, turnover, pricer, facts)
    note("true-prob lab", 0.45)
    p_lab = _prob_lab(labeled, true_prob, calibrators, pricer, facts)
    note("optimizer lab", 0.60)
    a_lab = _algo_lab(labeled, algos, pricer, facts)
    note("policy / demand lab", 0.78)
    pol_lab = _policy_lab(labeled, policies, elasticities, algos, pricer)
    note("attribution", 0.90)
    attr = _attribution(labeled, t_lab, p_lab, pricer, algos, elasticities)
    cross = _cross_heatmap(t_lab, p_lab)

    best_t = _best_id(t_lab["candidates"], lambda c: c["turnover"]["mae"], prefer_oracle=False)
    best_p = _best_id(p_lab["candidates"], lambda c: c["true_odds"]["logloss"])
    best_a = _best_id(a_lab["candidates"], lambda c: -c["gm"]["realized_gm"])
    hold = next((c for c in a_lab["candidates"] if c["algo"] == "hold"), None)
    slsqp = next((c for c in a_lab["candidates"] if c["algo"] == "slsqp"), hold)
    mvp_t = next((c for c in t_lab["candidates"] if c["turnover_model"] == MVP_T),
                 t_lab["candidates"][0])
    mvp_p = next((c for c in p_lab["candidates"]
                  if c["true_prob_source"] == MVP_P and c["calibrator"] == MVP_CAL),
                 p_lab["candidates"][0])

    flat = (
        t_lab["candidates"] + p_lab["candidates"]
        + a_lab["candidates"] + pol_lab["candidates"] + attr
    )
    headline = {
        "best_turnover": best_t,
        "best_true_odds": best_p,
        "best_algo": best_a,
        "best_gm": best_a,
        "mvp": "{}/{}/{}".format(MVP_T, MVP_P, MVP_ALGO),
        "mvp_turnover_mae": mvp_t["turnover"]["mae"],
        "mvp_logloss": mvp_p["true_odds"]["logloss"],
        "mvp_ece": mvp_p["true_odds"].get("ece", 0.0),
        "mvp_realized_gm": (hold or slsqp or mvp_t)["gm"]["realized_gm"] if (hold or slsqp) else 0.0,
        "mvp_realized_lift": (slsqp or hold)["gm"]["realized_lift"] if (slsqp or hold) else 0.0,
        "days_better": (slsqp or hold)["gm"]["days_better"] if (slsqp or hold) else 0,
        "days_worse": (slsqp or hold)["gm"]["days_worse"] if (slsqp or hold) else 0,
        "ticks_better": (slsqp or hold)["gm"]["ticks_better"] if (slsqp or hold) else 0,
        "ticks_worse": (slsqp or hold)["gm"]["ticks_worse"] if (slsqp or hold) else 0,
        "attribution_delta": (
            attr[-1]["gm"]["realized_gm"] - attr[0]["gm"]["realized_gm"]
            if len(attr) >= 2 else 0.0
        ),
    }

    report = {
        "run_id": "bt-{}-{}".format(datetime.now().strftime("%Y%m%d-%H%M%S"), seed),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "labs",
        "config": {
            "seed": seed,
            "n_matches": n_matches,
            "n_ticks": n_ticks,
            "start": world.start.isoformat(),
            "turnover": turnover,
            "true_prob": true_prob,
            "calibrators": calibrators,
            "algos": algos,
            "policies": policies,
            "elasticities": elasticities,
            "optimize": optimize,
            "solved_combos": [c["id"] for c in a_lab["candidates"] if c.get("optimized")],
            "bucket_minutes": config.BUCKET_MINUTES,
        },
        "n_ticks": len(labeled),
        "n_matches": n_matches,
        "n_rows": max((c.get("n_rows", 0) for c in flat), default=0),
        "seconds": round(time.time() - started, 2),
        "labs": {
            "turnover": t_lab,
            "true_prob": p_lab,
            "optimizer": a_lab,
            "policy": pol_lab,
        },
        "attribution": attr,
        "cross": cross,
        "combos": [
            {k: c[k] for k in ("id", "turnover_model", "true_prob_source",
                               "calibrator", "algo", "policy", "step")
             if k in c}
            for c in flat
        ],
        "leaderboard": {
            "turnover": [
                {"id": c["id"], "metric": "mae", "value": c["turnover"]["mae"],
                 "n": c["turnover"]["n"]}
                for c in sorted(t_lab["candidates"], key=lambda c: c["turnover"]["mae"])
            ],
            "true_odds": [
                {"id": c["id"], "metric": "logloss", "value": c["true_odds"]["logloss"],
                 "n": c["true_odds"]["n"]}
                for c in sorted(p_lab["candidates"], key=lambda c: c["true_odds"]["logloss"])
            ],
            "gm": [
                {"id": c["id"], "metric": "realized_gm", "value": c["gm"]["realized_gm"],
                 "n": c["gm"]["n"]}
                for c in sorted(a_lab["candidates"],
                                key=lambda c: -c["gm"]["realized_gm"])
            ],
        },
        "headline": headline,
        "catalog": catalog(),
        "workbench": {
            "has_facts": True,
            "baselines": {
                "turnover": MVP_T,
                "true_prob": MVP_P,
                "calibrator": MVP_CAL,
                "algo": MVP_ALGO,
            },
            **facts.catalog(),
        },
    }
    store = get_backtest_store()
    wide = facts.build()
    report["n_fact_rows"] = int(len(wide))
    store.save_facts(report["run_id"], wide, persist=persist)
    slice_cache.invalidate(report["run_id"])
    if persist:
        store.save(report)
    note("backtest {} done in {:.1f}s".format(report["run_id"], report["seconds"]), 1.0)
    return report


def _resolve_algos(algos, optimize) -> List[str]:
    if algos:
        return list(algos)
    if optimize is True:
        optimize = "star"
    if optimize is False:
        optimize = "none"
    if optimize == "none":
        return ["hold"]
    if optimize == "all":
        return list(default_algos())
    # star: the honest pair — standing still vs the live solver
    return ["hold", "slsqp"]


def _label_meeting(world, note) -> list:
    pre = Preprocessor()
    labeled = []
    for i, as_of in enumerate(world.timeline):
        note("labelling tick {} / {} ({})".format(
            i + 1, len(world.timeline), as_of.strftime("%H:%M")),
            0.05 + 0.20 * (i + 1) / max(len(world.timeline), 1))
        snap = world.snapshot(as_of)
        try:
            frame = pre.build(snap)
        except Exception as exc:                          # noqa: BLE001
            log.warning("tick %s dropped: %s", as_of, exc)
            continue
        attach_labels(frame, world)
        labeled.append(frame)
    return labeled


def _score_stack(
    labeled,
    pricer,
    turnover_name,
    true_prob_name,
    calibrator_name="raw",
    algo="hold",
    policy="always",
    elasticity=0.0,
    need_opt=None,
) -> pd.DataFrame:
    """Walk the labelled ticks under one stack and return the scored rows."""
    t_model = make_turnover(turnover_name)
    p_model = make_true_prob(true_prob_name)
    cal = make_calibrator(calibrator_name)
    do_opt = (algo != "hold") if need_opt is None else bool(need_opt)
    pieces = []
    history = []
    hist_p, hist_y = [], []
    diags = []
    saved_e = config.DEMAND_ELASTICITY
    config.DEMAND_ELASTICITY = float(elasticity or 0.0)
    try:
        for frame in labeled:
            work = copy_frame(frame)
            work.turnover_profile = walk_forward_profile(history)
            p_model.attach(work)
            raw_p = pd.to_numeric(work.selections["true_prob"], errors="coerce")
            if hist_p:
                cal.fit(np.asarray(hist_p, dtype=float), np.asarray(hist_y, dtype=float))
            cal.apply(work.selections)
            t_model.attach(work)
            pricer.attach(work)
            if do_opt:
                diags.append(apply_algo(work, pricer, algo))
            else:
                work.selections["sell_odds_star"] = work.selections.get("sell_odds")
                diags.append({"algo": algo, "n_solved": 0, "n_failed": 0,
                              "seconds": 0.0, "mean_abs_theta": 0.0,
                              "n_repaired": 0, "exp_uplift": 0.0})
            apply_policy(work, policy)
            attach_counterfactual(work.selections, elasticity)
            y = pd.to_numeric(work.selections.get("result"), errors="coerce").map(outcome_y)
            take = raw_p.notna() & y.notna() & (raw_p > 0) & (raw_p < 1)
            hist_p.extend(raw_p[take].tolist())
            hist_y.extend(y[take].tolist())
            pieces.append(work.selections)
            history.append(work)
    finally:
        config.DEMAND_ELASTICITY = saved_e
    scored = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()
    scored.attrs["diag"] = merge_diags(diags)
    return scored


def _candidate(stack_id, scored, **meta) -> dict:
    row = {
        "id": stack_id,
        "turnover_model": meta.get("turnover_model"),
        "true_prob_source": meta.get("true_prob_source"),
        "calibrator": meta.get("calibrator", "raw"),
        "algo": meta.get("algo", "hold"),
        "policy": meta.get("policy", "always"),
        "elasticity": float(meta.get("elasticity", 0.0)),
        "optimized": bool(meta.get("optimized", False)),
        "turnover_label": TURNOVER_LABELS.get(meta.get("turnover_model"), meta.get("turnover_model")),
        "true_prob_label": TRUE_PROB_LABELS.get(meta.get("true_prob_source"), meta.get("true_prob_source")),
        "calibrator_label": CALIBRATOR_LABELS.get(meta.get("calibrator", "raw"), meta.get("calibrator")),
        "algo_label": ALGO_LABELS.get(meta.get("algo", "hold"), meta.get("algo")),
        "policy_label": POLICY_LABELS.get(meta.get("policy", "always"), meta.get("policy")),
        "n_rows": int(len(scored)),
        "turnover": packs.turnover_pack(scored),
        "true_odds": packs.true_odds_pack(scored),
        "gm": packs.gm_pack(scored),
        "diag": scored.attrs.get("diag") or {},
    }
    return row


def _turnover_lab(labeled, names, pricer, facts=None) -> dict:
    rows = []
    for name in names:
        scored = _score_stack(labeled, pricer, name, MVP_P, MVP_CAL, MVP_ALGO)
        if facts is not None:
            facts.add_turnover(scored, name)
        rows.append(_candidate(name, scored, turnover_model=name,
                               true_prob_source=MVP_P, calibrator=MVP_CAL, algo=MVP_ALGO))
    leader = _best_id(rows, lambda c: c["turnover"]["mae"], prefer_oracle=False)
    return {"axis": "turnover", "baseline": MVP_T, "leader": leader, "candidates": rows}


def _prob_lab(labeled, sources, calibrators, pricer, facts=None) -> dict:
    rows = []
    for source in sources:
        for cal in calibrators:
            scored = _score_stack(labeled, pricer, MVP_T, source, cal, MVP_ALGO)
            if facts is not None:
                facts.add_belief(scored, source, cal)
            rows.append(_candidate("{}/{}".format(source, cal), scored,
                                   turnover_model=MVP_T, true_prob_source=source,
                                   calibrator=cal, algo=MVP_ALGO))
    leader = _best_id(rows, lambda c: c["true_odds"]["logloss"])
    return {"axis": "true_prob", "baseline": "{}/{}".format(MVP_P, MVP_CAL),
            "leader": leader, "candidates": rows}


def _algo_lab(labeled, algos, pricer, facts=None) -> dict:
    rows = []
    for algo in algos:
        scored = _score_stack(
            labeled, pricer, MVP_T, MVP_P, MVP_CAL, algo,
            need_opt=(algo != "hold"),
        )
        if facts is not None:
            facts.add_algo(scored, algo)
        rows.append(_candidate(algo, scored, turnover_model=MVP_T,
                               true_prob_source=MVP_P, calibrator=MVP_CAL,
                               algo=algo, optimized=(algo != "hold")))
    leader = _best_id(rows, lambda c: -c["gm"]["realized_gm"])
    return {"axis": "optimizer", "baseline": MVP_ALGO, "leader": leader, "candidates": rows}


def _policy_lab(labeled, policies, elasticities, algos, pricer) -> dict:
    """Re-solve only when elasticity changes; policies apply after the fact."""
    solve_algo = "slsqp" if "slsqp" in algos else (algos[-1] if algos else "hold")
    rows = []
    # Always include the inelastic hold so the lab has a baseline.
    scored_hold = _score_stack(labeled, pricer, MVP_T, MVP_P, MVP_CAL, "hold",
                               "always", 0.0, need_opt=False)
    rows.append(_candidate("hold/always/e0", scored_hold, turnover_model=MVP_T,
                           true_prob_source=MVP_P, calibrator=MVP_CAL,
                           algo="hold", policy="always", elasticity=0.0))
    for e in elasticities:
        if solve_algo == "hold":
            scored = _score_stack(labeled, pricer, MVP_T, MVP_P, MVP_CAL, "hold",
                                  "always", e, need_opt=False)
            for policy in policies:
                rows.append(_candidate("hold/{}/e{}".format(policy, e), scored,
                                       turnover_model=MVP_T, true_prob_source=MVP_P,
                                       calibrator=MVP_CAL, algo="hold",
                                       policy=policy, elasticity=e))
            continue
        # One solve per elasticity; replay policies on a copy of the odds.
        scored_base = _score_stack(
            labeled, pricer, MVP_T, MVP_P, MVP_CAL, solve_algo,
            "always", e, need_opt=True,
        )
        for policy in policies:
            if policy == "always":
                scored = scored_base
            else:
                scored = scored_base.copy()
                _reapply_policy(scored, policy)
                attach_counterfactual(scored, e)
            rows.append(_candidate("{}/{}/e{}".format(solve_algo, policy, e), scored,
                                   turnover_model=MVP_T, true_prob_source=MVP_P,
                                   calibrator=MVP_CAL, algo=solve_algo,
                                   policy=policy, elasticity=e,
                                   optimized=True))
            if policy != "always":
                # packs were computed for always; rebuild for this policy
                rows[-1]["turnover"] = packs.turnover_pack(scored)
                rows[-1]["true_odds"] = packs.true_odds_pack(scored)
                rows[-1]["gm"] = packs.gm_pack(scored)
    # Dedup ids
    seen = set()
    uniq = []
    for row in rows:
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        uniq.append(row)
    leader = _best_id(uniq, lambda c: -c["gm"]["realized_gm"])
    return {"axis": "policy", "baseline": "hold/always/e0",
            "leader": leader, "candidates": uniq}


def _reapply_policy(sel: pd.DataFrame, policy: str) -> None:
    """Policy filter on an already-scored frame (no TickFrame wrapper)."""
    class _F:
        selections = sel
    apply_policy(_F, policy)


def _attribution(labeled, t_lab, p_lab, pricer, algos, elasticities) -> list:
    """MVP hold → +best T → +best P/cal → (+solver / elasticity / oracle)."""
    best_t = _best_name(t_lab["candidates"], lambda c: c["turnover"]["mae"],
                        key="turnover_model", skip=("oracle", "zero"))
    best_p_row = min(
        (c for c in p_lab["candidates"] if c["calibrator"] != "raw" or c["true_prob_source"] == MVP_P),
        key=lambda c: c["true_odds"]["logloss"],
        default=p_lab["candidates"][0],
    )
    best_p = best_p_row["true_prob_source"]
    best_cal = best_p_row["calibrator"]
    steps = [
        ("MVP hold", MVP_T, MVP_P, MVP_CAL, "hold", "always", 0.0),
        ("+ best turnover ({})".format(best_t), best_t, MVP_P, MVP_CAL, "hold", "always", 0.0),
        ("+ best belief ({}/{})".format(best_p, best_cal), best_t, best_p, best_cal, "hold", "always", 0.0),
    ]
    solve_algo = "slsqp" if "slsqp" in algos else next((a for a in algos if a != "hold"), None)
    if solve_algo:
        steps.append(("+ {}".format(solve_algo), best_t, best_p, best_cal, solve_algo, "always", 0.0))
        if any(float(e) > 0 for e in elasticities):
            steps.append(("+ elastic demand", best_t, best_p, best_cal, solve_algo, "always", 1.0))
        if any(c.get("turnover_model") == "oracle" for c in t_lab["candidates"]):
            steps.append(("oracle demand + {}".format(solve_algo),
                          "oracle", best_p, best_cal, solve_algo, "always", 0.0))
    rows = []
    prev_gm = None
    for i, (label, t, p, cal, algo, policy, e) in enumerate(steps):
        scored = _score_stack(labeled, pricer, t, p, cal, algo, policy, e,
                              need_opt=(algo != "hold"))
        row = _candidate("attr-{}".format(i), scored, turnover_model=t,
                         true_prob_source=p, calibrator=cal, algo=algo,
                         policy=policy, elasticity=e, optimized=(algo != "hold"))
        row["step"] = label
        gm = row["gm"]["realized_gm"]
        row["delta"] = 0.0 if prev_gm is None else round(gm - prev_gm, 2)
        prev_gm = gm
        rows.append(row)
    return rows


def _cross_heatmap(t_lab, p_lab) -> dict:
    """Realised GM at the current offer: turnover × (source/raw only)."""
    t_names = [c["turnover_model"] for c in t_lab["candidates"] if c["turnover_model"] != "zero"]
    p_rows = [c for c in p_lab["candidates"] if c["calibrator"] == "raw"]
    p_names = [c["true_prob_source"] for c in p_rows]
    # Each lab froze the other axis, so we cannot honestly fill a full
    # grid without re-scoring. Use the independent scores as a proxy:
    # turnover quality does not change GM at hold (same offer, same
    # actual tickets). The useful grid is log-loss (columns) vs MAE
    # (rows) so the meeting sees the two errors on one page.
    mae = {c["turnover_model"]: c["turnover"]["mae"] for c in t_lab["candidates"]}
    ll = {c["true_prob_source"]: c["true_odds"]["logloss"] for c in p_rows}
    gm = {c["true_prob_source"]: c["gm"]["realized_gm"] for c in p_rows}
    values = []
    for t in t_names:
        row = []
        for p in p_names:
            # Independent: GM at hold is a property of the belief (and
            # the posted offer), not of the turnover forecast.
            row.append(gm.get(p))
        values.append(row)
    return {
        "hint": "Realised GM at the current offer does not use the turnover forecast. "
                "Columns are the belief; the turnover MAE is the row annotation.",
        "row_mae": [{"key": t, "value": mae.get(t)} for t in t_names],
        "col_logloss": [{"key": p, "value": ll.get(p)} for p in p_names],
        "heatmap": heatmap(t_names, p_names, values),
        "turnover_mae": [{"key": t, "value": mae.get(t)} for t in t_names],
        "logloss": [{"key": p, "value": ll.get(p)} for p in p_names],
    }


def _best_id(rows, keyfn, prefer_oracle=True):
    if not rows:
        return None
    pool = rows
    if not prefer_oracle:
        pool = [r for r in rows if r.get("turnover_model") not in ("oracle", "zero")] or rows
    return min(pool, key=keyfn)["id"]


def _best_name(rows, keyfn, key, skip=()):
    pool = [r for r in rows if r.get(key) not in skip] or rows
    return min(pool, key=keyfn)[key]


def print_report(report: dict) -> None:
    """Meeting-readable comparison tables on stdout."""
    cfg = report["config"]
    head = report["headline"]
    print("\nBacktest {}   {:.1f}s   mode={}".format(
        report["run_id"], report["seconds"], report.get("mode", "labs")))
    print("  {} ticks × {} matches × {} rows   seed={}".format(
        report["n_ticks"], report["n_matches"], report["n_rows"], cfg["seed"]))
    print("  MVP {}:  turnover MAE {:.1f}   log-loss {:.4f}   ECE {:.4f}".format(
        head["mvp"], head["mvp_turnover_mae"], head["mvp_logloss"],
        head.get("mvp_ece", 0.0)))
    print("  SLSQP vs hold:  days {} / {}   ticks {} / {}   R[lift] {:+.0f}".format(
        head["days_better"], head["days_worse"],
        head["ticks_better"], head["ticks_worse"],
        head.get("mvp_realized_lift", 0.0)))

    labs = report.get("labs") or {}
    _print_lab("TURNOVER", labs.get("turnover"),
               ("id", "mae", "mse", "wape%", "share_mae", "r2", "theil_u"),
               lambda c: {
                   "id": c["id"],
                   "mae": c["turnover"]["mae"],
                   "mse": c["turnover"].get("mse", 0),
                   "wape%": c["turnover"].get("wape_pct", 0),
                   "share_mae": c["turnover"].get("share_mae", 0),
                   "r2": c["turnover"].get("r2", 0),
                   "theil_u": c["turnover"].get("theil_u", 0),
               })
    _print_lab("TRUE PROB / CALIBRATION", labs.get("true_prob"),
               ("id", "logloss", "brier", "acc", "ece", "bias", "auc", "book|1|"),
               lambda c: {
                   "id": c["id"],
                   "logloss": c["true_odds"]["logloss"],
                   "brier": c["true_odds"]["brier"],
                   "acc": c["true_odds"].get("accuracy", 0),
                   "ece": c["true_odds"].get("ece", 0),
                   "bias": c["true_odds"]["bias"],
                   "auc": c["true_odds"].get("auc", 0),
                   "book|1|": c["true_odds"].get("book_abs", 0),
               })
    _print_lab("OPTIMIZER", labs.get("optimizer"),
               ("id", "R[GM]", "R[lift]", "E[lift]", "opt.", "hit", "cvar5", "sec"),
               lambda c: {
                   "id": c["id"],
                   "R[GM]": c["gm"].get("realized_gm", 0),
                   "R[lift]": c["gm"]["realized_lift"],
                   "E[lift]": c["gm"]["exp_lift"],
                   "opt.": c["gm"].get("optimism", 0),
                   "hit": c["gm"].get("hit_rate", 0),
                   "cvar5": c["gm"].get("cvar_5", 0),
                   "sec": c.get("diag", {}).get("seconds", 0),
               })
    _print_lab("POLICY / DEMAND", labs.get("policy"),
               ("id", "R[GM]", "R[lift]", "e", "policy"),
               lambda c: {
                   "id": c["id"],
                   "R[GM]": c["gm"].get("realized_gm", 0),
                   "R[lift]": c["gm"]["realized_lift"],
                   "e": c.get("elasticity", 0),
                   "policy": c.get("policy"),
               })
    attr = report.get("attribution") or []
    if attr:
        print("\nATTRIBUTION  (realised GM, incremental)")
        for row in attr:
            print("  {:>32}  {:+12.0f}   d {:+10.0f}".format(
                row.get("step", row["id"]),
                row["gm"]["realized_gm"],
                row.get("delta", 0.0)))
    print("\nLeaders  turnover={}   belief={}   algo={}".format(
        head.get("best_turnover"), head.get("best_true_odds"), head.get("best_algo")))


def _print_lab(title, lab, cols, rowfn):
    if not lab or not lab.get("candidates"):
        return
    print("\n{}".format(title))
    frame = pd.DataFrame([rowfn(c) for c in lab["candidates"]])
    print(frame.to_string(index=False, float_format=lambda v: "{: .4f}".format(v)))
    print("  leader {}".format(lab.get("leader")))
