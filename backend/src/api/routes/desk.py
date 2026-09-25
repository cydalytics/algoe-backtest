"""
Desk Routes

The endpoints the trading UI actually calls.

    GET  /api/desk            the board - every match, one row each
    GET  /api/desk/{id}       the cockpit - one match in full
    POST /api/tick            force a fresh tick
    POST /api/price           what-if: price a match at an arbitrary theta
    GET  /api/pools           the pool taxonomy, for labels and grouping
    GET  /api/config          what the pipeline is running with

The what-if endpoint is the one that makes the cockpit interactive: the
trader drags a theta dimension, the browser posts the whole vector, and the
same pricing code that produced the board prices it again. Nothing about
the answer comes from a separate implementation in the frontend, so the
number on the slider and the number in the book cannot drift apart.

Change Log:
-----------
2026-08-30      Rewritten for the offline pipeline
"""

from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.api import payload
from src.api.state import get_state
from src.core import config
from src.models.pricer import Pricer
from src.pricing import book as book_lib
from src.pricing import pools as pool_defs

router = APIRouter()


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

class PriceRequest(BaseModel):
    """A what-if: this match, this theta, what does the book look like."""

    match_id: int
    theta: Dict[str, float] = Field(
        default_factory=dict,
        description="Any subset of the eight dimensions; the rest are held "
                    "at the match's current parameters.",
    )
    pools: Optional[List[str]] = Field(
        default=None, description="Restrict the answer to these pool codes."
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/desk")
def desk(refresh: bool = Query(False, description="Run a fresh tick first")):
    """The board: one row per match, sorted by what is worth the most.

    Returns the priced book immediately. Recommendations land over the
    following seconds as the background solver works down the card, and
    ``progress`` says how far it has got.
    """
    state = get_state()
    tick = state.current(refresh=refresh)
    return payload.board(tick, solving=state.solving)


@router.get("/desk/{match_id}")
def cockpit(match_id: int, refresh: bool = Query(False)):
    """The cockpit: one match, every selection, both prices."""
    state = get_state()
    tick = state.current(refresh=refresh)
    out = payload.cockpit(tick, match_id, solving=state.solving)
    if out is None:
        raise HTTPException(404, "match {} is not on this tick".format(match_id))
    return out


@router.post("/desk/{match_id}/solve")
def solve(match_id: int):
    """Jump one match to the front of the queue and solve it now.

    Opening a cockpit is the trader saying this is the match they care
    about, so it should not wait behind the rest of the card.
    """
    from src.optimizer import optimizer as opt

    state = get_state()
    tick = state.current()
    if tick.match(match_id) is None:
        raise HTTPException(404, "match {} is not on this tick".format(match_id))
    result = opt.solve_match(tick, match_id)
    if result is not None:
        state.pipeline.publish(tick, match_id, result)
    return payload.cockpit(tick, match_id, solving=state.solving)


@router.get("/desk/{match_id}/curves")
def curves(match_id: int):
    """E[GM] along each theta dimension for one match.

    Computed on demand rather than on every tick: the board never shows
    them and they cost roughly as much as the solve itself.
    """
    from src.optimizer import optimizer as opt

    tick = get_state().current()
    ctx = tick.match(match_id)
    if ctx is None:
        raise HTTPException(404, "match {} is not on this tick".format(match_id))
    result = tick.result(match_id)
    legs, _ = opt.legs_from_tick(tick.frame, match_id)
    if not legs:
        return {"match_id": match_id, "turnover": 0.0, "curves": {}}
    engine = opt.MatchOptimizer(
        match_id, legs, ctx.state, ctx.theta.get(config.THETA_LAYER, {})
    )
    theta_star = result.theta_star if result else engine.theta_now
    return {
        "match_id": match_id,
        "turnover": payload._num(sum(leg.turnover for leg in legs), 2),
        "curves": engine.curves(theta_star),
    }


@router.post("/tick")
def tick(source: Optional[str] = Query(None, description="Override the source")):
    """Run a fresh tick and return the board it produced."""
    state = get_state()
    if source and source != state.pipeline.source_name:
        state.pipeline.source_name = source
    return payload.board(state.refresh(), solving=state.solving)


@router.post("/price")
def price(request: PriceRequest):
    """Re-price one match at an arbitrary theta.

    Returns the same selection shape the cockpit already draws, plus the
    expected margin at that theta, so the UI can lay a what-if straight
    over the live book.
    """
    tick = get_state().current()
    ctx = tick.match(request.match_id)
    if ctx is None:
        raise HTTPException(
            404, "match {} is not on this tick".format(request.match_id))

    unknown = set(request.theta) - set(book_lib.DIMS)
    if unknown:
        raise HTTPException(
            422, "unknown theta dimensions: {}".format(", ".join(sorted(unknown))))

    theta = dict(ctx.theta.get(config.THETA_LAYER, {}))
    theta.update(request.theta)

    rows = tick.rows(request.match_id).copy()
    if request.pools:
        wanted = {p.upper() for p in request.pools}
        rows = rows[rows["pool_code"].isin(wanted)]
    if rows.empty:
        raise HTTPException(404, "no selections match that filter")

    scratch = _scratch(tick, rows)
    pricer = Pricer()
    pricer.attach(scratch, theta_by_match={int(request.match_id): theta},
                  suffix="_what")

    priced = scratch.selections
    out = []
    for row in priced.to_dict("records"):
        out.append({
            "key": row["key"],
            "pool_code": row["pool_code"],
            "line_label": row["line_label"],
            "sel_label": row["sel_label"],
            "hkjc_odds": payload._num(row.get("hkjc_odds"), 3),
            "sell_odds": payload._num(row.get("sell_odds"), 3),
            "sell_odds_what": payload._num(row.get("sell_odds_what"), 3),
            "model_prob_what": payload._num(row.get("model_prob_what"), 5),
            "status_what": row.get("status_what"),
            "exp_gm_unit_what": payload._num(row.get("exp_gm_unit_what"), 5),
            "t_hat": payload._num(row.get("t_hat"), 2),
        })

    return {
        "match_id": request.match_id,
        "theta": {d: payload._num(theta.get(d), 4) for d in book_lib.DIMS},
        "expected": pricer.expected_gm(priced, suffix="_what"),
        "baseline": pricer.expected_gm(priced),
        "selections": out,
    }


@router.get("/pools")
def pools():
    """The pool taxonomy the frontend groups and labels by."""
    return {
        "families": list(pool_defs.FAMILY_ORDER),
        "pools": [
            {
                "code": p.code,
                "name": p.name,
                "family": p.family,
                "domain": p.domain,
                "seg": p.seg,
                "kind": p.kind,
                "has_line": p.has_line,
                "margin": p.margin,
                "optimized": p.optimized,
                "selections": list(p.selections),
            }
            for p in pool_defs.POOLS
        ],
        "carried": list(pool_defs.CARRIED_POOLS),
        "dims": [
            {"dim": d, "label": book_lib.DIM_LABELS[d],
             "bounds": list(config.THETA_BOUNDS[d]),
             "max_move": config.THETA_MAX_MOVE[d]}
            for d in book_lib.DIMS
        ],
    }


@router.get("/config")
def configuration():
    """What this process is running with - shown in the UI's status bar."""
    state = get_state()
    return {
        "source": state.pipeline.source_name,
        "tick_age_seconds": round(state.age_seconds, 1),
        "tick_ttl_seconds": state.ttl,
        "last_error": state.last_error,
        "rules": payload.rules(),
        "grid": {"max_goals": config.MAX_GOALS, "max_corners": config.MAX_CORNERS},
        "odds": {
            "floor": config.ODDS_FLOOR,
            "cap": config.ODDS_CAP,
            "max_sellable": config.MAX_SELLABLE_ODDS,
        },
    }


# ---------------------------------------------------------------------------

def _scratch(tick, rows):
    """A throwaway tick carrying just the rows a what-if needs.

    Pricing writes its columns onto the frame it is handed, so the live
    tick is never the one passed in - a what-if must not leave a trace on
    the book everyone else is reading.
    """
    from dataclasses import replace

    return replace(tick.frame, selections=rows.copy(), warnings=[])
