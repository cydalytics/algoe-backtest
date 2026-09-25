"""
API Payloads

Turns a DeskTick into the JSON the frontend consumes. Kept apart from the
routes so the shape of the contract lives in one readable place, and apart
from the pipeline so no serialisation concern leaks into the maths.

Two payloads carry the product:

    board    one row per match - enough to triage a full card at a glance
    cockpit  one match in full - every selection, both prices, the
             optimizer's reasoning, the tape and the external market

Both are built from the same tick, so a number shown on the board and the
same number inside the cockpit can never disagree.

NaN is not JSON. Every float that reaches the wire goes through _num, which
turns pandas' missing values into null rather than letting them serialise
into something the browser will read as a string.

Change Log:
-----------
2026-08-30      Initialize
"""

import math
from typing import Optional

import numpy as np
import pandas as pd

from src.core import config
from src.pricing import book as book_lib
from src.pricing import pools as pool_defs

STAR = "_star"

# Columns that travel to the browser for every selection.
BASE_COLS = (
    "key", "pool_id", "pool_code", "pool_name", "family", "seg", "domain",
    "kind", "optimized", "line_id", "line_label", "line_value", "comb_id",
    "selection", "sel_label", "is_main_line",
)
FEED_COLS = (
    "hkjc_odds", "hkjc_odds_prev", "hkjc_true_odds", "odds_age_sec",
    "mkt_min", "mkt_avg", "mkt_max", "mkt_n", "mkt_age_sec",
)
MODEL_COLS = (
    "true_prob", "true_prob_src", "book_sum", "model_prob", "fair_odds",
    "sell_odds", "status", "edge_hkjc", "edge_mkt", "exp_gm_unit",
)
FLOW_COLS = (
    "t5m", "tickets5m", "avg_stake", "t_hat", "t_hat_src", "t_share",
    "t_trend", "invested", "tickets_today",
)
STAR_COLS = ("model_prob", "fair_odds", "sell_odds", "status", "exp_gm_unit")


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

def _num(value, digits: Optional[int] = None):
    """A JSON-safe float, or None where pandas would hand us NaN."""
    if value is None:
        return None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return round(out, digits) if digits is not None else out


def _text(value):
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return str(value)


def _stamp(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    try:
        return pd.Timestamp(value).isoformat()
    except (ValueError, TypeError):
        return None


def _theta(values):
    return {dim: _num(values.get(dim), 4) for dim in book_lib.DIMS}


# ---------------------------------------------------------------------------
# Board
# ---------------------------------------------------------------------------

def board(tick, solving: bool = False) -> dict:
    """One row per match, plus the desk headline.

    Read under the tick's lock: the background solver publishes matches
    onto this same tick while the board is being built, and a payload that
    caught it mid-write would show a match's new theta against its old
    prices.
    """
    with tick.lock:
        rows = []
        for match_id, ctx in tick.contexts.items():
            result = tick.result(match_id)
            sel = tick.rows(match_id)
            rows.append(_board_row(tick, ctx, result, sel))
        totals = {k: _num(v, 2) for k, v in tick.totals.items()}
        solved, total = tick.solved, len(tick.contexts)
        payload = {
            "run_id": tick.run_id,
            "as_of": _stamp(tick.as_of),
            "source": tick.source,
            "totals": totals,
            "warnings": list(tick.warnings),
            "timings": {k: _num(v, 3) for k, v in tick.timings.items()},
            "rules": rules(),
            "progress": {
                "solved": solved,
                "total": total,
                "solving": bool(solving and solved < total),
            },
            "matches": rows,
        }
    rows.sort(key=lambda r: (-(r["uplift"] or 0), r["match_id"]))
    return payload


def _board_row(tick, ctx, result, sel) -> dict:
    state = ctx.state
    open_rows = sel[sel["status"] == "open"] if not sel.empty else sel
    theta_now = ctx.theta.get(config.THETA_LAYER, {})

    row = {
        "match_id": ctx.match_id,
        "home": ctx.home,
        "away": ctx.away,
        "league": ctx.league,
        "league_code": ctx.league_code,
        "kickoff": _stamp(ctx.kickoff),
        "phase": state.phase,
        "game_state": ctx.game_state,
        "minute": _num(state.minute, 1),
        "in_play": bool(state.in_play),
        "score": [state.home_score, state.away_score],
        "ht_score": [state.ht_home, state.ht_away],
        "corners": [state.home_corner, state.away_corner],
        "theta_age_min": _num(ctx.theta_age_min, 1),
        "theta_now": _theta(theta_now),
        "pools": int(sel["pool_id"].nunique()) if not sel.empty else 0,
        "selections": int(len(sel)),
        "open": int(len(open_rows)),
        "turnover_5m": _num(sel["t5m"].sum() if not sel.empty else 0.0, 2),
        "tickets_5m": int(sel["tickets5m"].sum()) if not sel.empty else 0,
        "alerts": _alerts(ctx, sel, result),
    }

    if result is None:
        # Not "no recommendation" but "no recommendation yet": the solver
        # works through the card richest first and this one is still in the
        # queue. The board says so rather than showing a blank uplift that
        # reads as zero.
        row.update(theta_star=None, gm_now=None, gm_star=None, uplift=None,
                   uplift_bps=None, turnover=None, moves=[], solved=False,
                   pending=True)
        return row
    row["pending"] = False

    row.update(
        theta_star=_theta(result.theta_star),
        turnover=_num(result.turnover, 2),
        gm_now=_num(result.gm_now, 2),
        gm_star=_num(result.gm_star, 2),
        uplift=_num(result.uplift, 2),
        uplift_bps=_num(result.uplift_bps, 1),
        legs=result.legs,
        skipped=result.skipped,
        solved=all(b.success for b in result.blocks.values()),
        seconds=_num(result.seconds, 3),
        moves=_moves(result),
    )
    return row


def _moves(result) -> list:
    """The dimensions the recommendation actually wants to move."""
    out = []
    for dim in book_lib.DIMS:
        now = result.theta_now.get(dim, 0.0)
        star = result.theta_star.get(dim, 0.0)
        delta = star - now
        if abs(delta) < 1e-4:
            continue
        limit = config.THETA_MAX_MOVE.get(dim, 0.0)
        out.append({
            "dim": dim,
            "label": book_lib.DIM_LABELS.get(dim, dim),
            "now": _num(now, 4),
            "star": _num(star, 4),
            "delta": _num(delta, 4),
            # A move sitting on its own limit is the solver saying it would
            # have gone further, which is a different message to the desk.
            "at_limit": bool(limit and abs(abs(delta) - limit) < 1e-3),
        })
    return sorted(out, key=lambda m: -abs(m["delta"] or 0))


def _alerts(ctx, sel, result) -> list:
    """Short, specific reasons this match deserves attention now."""
    out = []
    if ctx.theta_age_min >= 10:
        out.append({
            "level": "warn",
            "text": "parameters {:.0f} min old".format(ctx.theta_age_min),
        })
    if not sel.empty:
        stale = sel[sel["odds_age_sec"] > 300]
        if len(stale) > len(sel) * 0.5:
            out.append({"level": "warn", "text": "over half the board is stale"})
        negative = sel[(sel["status"] == "open") & (sel["exp_gm_unit"] < 0)]
        if not negative.empty:
            worst = negative.nsmallest(1, "exp_gm_unit").iloc[0]
            out.append({
                "level": "bad",
                "text": "{} {} prices below true".format(
                    worst["pool_code"], worst["sel_label"]),
            })
    if result is not None:
        if result.repaired:
            out.append({
                "level": "warn",
                "text": "{} incoherent from the feed".format(
                    ", ".join(sorted(result.repaired))),
            })
        if not all(b.success for b in result.blocks.values()):
            out.append({"level": "bad", "text": "optimizer did not converge"})
        elif result.uplift_bps >= 100:
            out.append({
                "level": "good",
                "text": "{:.0f} bps on the table".format(result.uplift_bps),
            })
    return out


# ---------------------------------------------------------------------------
# Cockpit
# ---------------------------------------------------------------------------

def cockpit(tick, match_id: int, solving: bool = False) -> Optional[dict]:
    """One match in full."""
    with tick.lock:
        ctx = tick.match(match_id)
        if ctx is None:
            return None
        sel = tick.rows(match_id).copy()
        result = tick.result(match_id)

    return {
        "run_id": tick.run_id,
        "as_of": _stamp(tick.as_of),
        "source": tick.source,
        "pending": result is None,
        "solving": bool(solving and result is None),
        "rules": rules(),
        "match": _match_header(ctx),
        "theta": {
            "layers": {
                layer: _theta(values) for layer, values in ctx.theta.items()
            },
            "layer": config.THETA_LAYER,
            "age_min": _num(ctx.theta_age_min, 1),
            "splits": {k: _num(v, 4) for k, v in ctx.splits.items()},
            "semantics": config.THETA_SEMANTICS,
            "dims": [
                {
                    "dim": dim,
                    "label": book_lib.DIM_LABELS[dim],
                    "bounds": list(config.THETA_BOUNDS[dim]),
                    "max_move": config.THETA_MAX_MOVE[dim],
                    "domain": "corner" if dim.startswith("corner") else "goal",
                }
                for dim in book_lib.DIMS
            ],
        },
        "optimizer": _optimizer(result),
        "book": _book(sel),
        "events": _events(tick, match_id),
        "quotes": _quotes(tick, match_id),
        "history": _history(tick, match_id),
    }


def _match_header(ctx) -> dict:
    state = ctx.state
    return {
        "match_id": ctx.match_id,
        "home": ctx.home,
        "away": ctx.away,
        "league": ctx.league,
        "league_code": ctx.league_code,
        "kickoff": _stamp(ctx.kickoff),
        "frontend_id": ctx.frontend_id,
        "phase": state.phase,
        "game_state": ctx.game_state,
        "minute": _num(state.minute, 1),
        "in_play": bool(state.in_play),
        "ht_done": bool(state.ht_done),
        "score": [state.home_score, state.away_score],
        "ht_score": [state.ht_home, state.ht_away],
        "corners": [state.home_corner, state.away_corner],
        "ht_corners": [state.ht_home_corner, state.ht_away_corner],
    }


def _optimizer(result) -> Optional[dict]:
    if result is None:
        return None
    return {
        "theta_now": _theta(result.theta_now),
        "theta_star": _theta(result.theta_star),
        "turnover": _num(result.turnover, 2),
        "payout_now": _num(result.payout_now, 2),
        "payout_star": _num(result.payout_star, 2),
        "gm_now": _num(result.gm_now, 2),
        "gm_star": _num(result.gm_star, 2),
        "uplift": _num(result.uplift, 2),
        "uplift_bps": _num(result.uplift_bps, 1),
        "legs": result.legs,
        "skipped": result.skipped,
        "seconds": _num(result.seconds, 3),
        "moves": _moves(result),
        # Dimensions the feed handed us in a state no book can be built
        # from, and what they had to become before the solve could start.
        "repaired": [
            {"dim": dim, "label": book_lib.DIM_LABELS[dim],
             "from": _num(was, 4), "to": _num(now, 4)}
            for dim, (was, now) in sorted((result.repaired or {}).items())
        ],
        "blocks": [
            {
                "block": b.block,
                "dims": list(b.dims),
                "legs": b.legs,
                "starts": b.starts,
                "success": b.success,
                "message": b.message,
                "seconds": _num(b.seconds, 3),
                "gm_now": _num(b.gm_now, 2),
                "gm_star": _num(b.gm_star, 2),
                "uplift": _num(b.uplift, 2),
            }
            for b in result.blocks.values()
        ],
        "per_pool": [
            {k: (_num(v, 2) if isinstance(v, (int, float)) and k not in
                 ("legs",) else v) for k, v in row.items()}
            for row in result.per_pool
        ],
        "curves": dict(result.curves or {}),
    }


def _book(sel: pd.DataFrame) -> dict:
    """Selections grouped the way the cockpit's tree draws them."""
    if sel.empty:
        return {"families": [], "totals": {}}

    families = []
    for family in pool_defs.FAMILY_ORDER:
        fam_rows = sel[sel["family"] == family]
        if fam_rows.empty:
            continue
        pools = []
        for pool_code, pool_rows in fam_rows.groupby("pool_code", sort=False):
            lines = []
            for line_id, line_rows in pool_rows.groupby("line_id", sort=False):
                # Enforce selection order: H > D > A for three-way pools,
                # H > A for two-way pools, otherwise natural order.
                SEL_ORDER = {
                    "H": 0, "D": 1, "A": 2,
                    "O": 0, "L": 1,  # for asian_total: High, Low
                }
                sorted_records = sorted(
                    line_rows.to_dict("records"),
                    key=lambda r: SEL_ORDER.get(r.get("selection", ""), 99))
                lines.append({
                    "line_id": int(line_id),
                    "line_label": _text(line_rows["line_label"].iloc[0]),
                    "line_value": _num(line_rows["line_value"].iloc[0], 3),
                    "is_main_line": bool(line_rows["is_main_line"].iloc[0]),
                    "book_sum": _num(line_rows["book_sum"].iloc[0], 4),
                    "turnover": _num(line_rows["t_hat"].sum(), 2),
                    "selections": [_selection(r) for r in sorted_records],
                })
            lines.sort(key=lambda l: (not l["is_main_line"],
                                      l["line_value"] if l["line_value"]
                                      is not None else 0))
            first = pool_rows.iloc[0]
            pools.append({
                "pool_code": pool_code,
                "pool_name": _text(first["pool_name"]),
                "domain": _text(first["domain"]),
                "seg": _text(first["seg"]),
                "kind": _text(first["kind"]),
                "optimized": bool(first["optimized"]),
                "margin": _num(first["margin"], 4),
                "turnover": _num(pool_rows["t_hat"].sum(), 2),
                "open": int((pool_rows["status"] == "open").sum()),
                "lines": lines,
            })
        # Fixed pool display order: HAD > FHAD > HHAD > HDC > FHDC > HILO > FHLO > ...
        POOL_ORDER = [
            "HAD", "FHAD", "HHAD",
            "HDC", "FHDC",
            "HILO", "FHLO",
            "OOE", "TTG",
            "CRS", "FCRS", "HFT",
            "CHLO", "CHDC", "FCHLO", "FCHDC",
        ]
        pool_rank = {code: i for i, code in enumerate(POOL_ORDER)}
        pools.sort(key=lambda p: (pool_rank.get(p["pool_code"], 99),))
        families.append({
            "family": family,
            "turnover": _num(fam_rows["t_hat"].sum(), 2),
            "pools": pools,
        })

    return {
        "families": families,
        "totals": {
            "selections": int(len(sel)),
            "open": int((sel["status"] == "open").sum()),
            "turnover": _num(sel["t_hat"].sum(), 2),
        },
    }


def _selection(row: dict) -> dict:
    out = {}
    for col in BASE_COLS:
        value = row.get(col)
        if col in ("line_value",):
            out[col] = _num(value, 3)
        elif col in ("optimized", "is_main_line"):
            out[col] = bool(value)
        elif col in ("pool_id", "line_id", "comb_id"):
            out[col] = int(value) if value is not None else None
        else:
            out[col] = _text(value)
    for col in FEED_COLS + MODEL_COLS + FLOW_COLS:
        value = row.get(col)
        out[col] = _text(value) if col.endswith("_src") or col == "status" \
            else _num(value, 5)
    for col in STAR_COLS:
        value = row.get(col + STAR)
        out[col + STAR] = _text(value) if col == "status" else _num(value, 5)
    # What the recommendation would actually change on the ticket.
    now, star = out.get("sell_odds"), out.get("sell_odds" + STAR)
    out["odds_delta"] = _num(star - now, 3) if (now and star) else None
    return out


def _events(tick, match_id: int) -> list:
    ev = tick.frame.events
    if ev.empty:
        return []
    rows = ev[ev["match_id"] == int(match_id)].head(40)
    return [
        {
            "at": _stamp(r["at"]),
            "type": _text(r["type"]),
            "side": _text(r["side"]),
            "detail": _text(r["detail"]),
            "provider_id": int(r["provider_id"]) if r["provider_id"] else None,
        }
        for r in rows.to_dict("records")
    ]


def _quotes(tick, match_id: int) -> dict:
    """External prices per selection, for the market tab."""
    quotes, sel = tick.frame.quotes, tick.rows(match_id)
    if quotes.empty or sel.empty:
        return {"books": [], "rows": []}
    keys = set(zip(sel["pool_id"], sel["line_id"], sel["comb_id"]))
    mine = quotes[[
        (p, l, c) in keys for p, l, c in zip(
            quotes["pool_id"], quotes["line_id"], quotes["combination_id"])
    ]]
    if mine.empty:
        return {"books": [], "rows": []}
    known = {
        (int(r["pool_id"]), int(r["line_id"]), int(r["comb_id"])): (
            str(r["key"]),
            " ".join(str(p) for p in
                     (r["pool_code"], r["line_label"], r["sel_label"]) if p).strip(),
        )
        for r in sel.to_dict("records")
    }
    rows = []
    for r in mine.to_dict("records"):
        ident = known.get((int(r["pool_id"]), int(r["line_id"]),
                           int(r["combination_id"])))
        if ident is None:
            continue
        rows.append({
            "key": ident[0],
            "label": ident[1],
            "bookmaker": _text(r.get("bookmaker_str") or r.get("bookmaker")),
            "odds": _num(r.get("odds"), 3),
            "age_sec": int(r.get("age_sec") or 0),
        })
    books = sorted({r["bookmaker"] for r in rows if r["bookmaker"]})
    return {"books": books, "rows": rows}


def _history(tick, match_id: int) -> dict:
    """Recent HKJC price track per selection, for the sparklines."""
    hist, sel = tick.frame.odds_history, tick.rows(match_id)
    if hist.empty or sel.empty:
        return {}
    keys = set(zip(sel["pool_id"], sel["line_id"], sel["comb_id"]))
    mine = hist[[
        (p, l, c) in keys for p, l, c in zip(
            hist["pool_id"], hist["line_id"], hist["combination_id"])
    ]]
    out = {}
    for (pool_id, line_id, comb_id), group in mine.groupby(
            ["pool_id", "line_id", "combination_id"]):
        match = sel[(sel["pool_id"] == pool_id) & (sel["line_id"] == line_id)
                    & (sel["comb_id"] == comb_id)]
        if match.empty:
            continue
        out[str(match["key"].iloc[0])] = [
            {"at": _stamp(r["effective_datetime"]),
             "odds": _num(r["odds"], 3),
             "true_odds": _num(r["true_odds"], 3)}
            for r in group.tail(40).to_dict("records")
        ]
    return out


# ---------------------------------------------------------------------------
# Static descriptors
# ---------------------------------------------------------------------------

def rules() -> dict:
    """The MVP simplifications, so the UI can label them rather than imply
    a sophistication the numbers do not have."""
    return {
        "true_prob": {
            "id": config.TRUE_PROB_SOURCE,
            "label": "TrueProb = 1 / HKJC true odds",
            "note": "read from the feed, fixed for the tick",
        },
        "turnover": {
            "id": config.TURNOVER_MODEL,
            "label": "Next 5 min turnover = last 5 min",
            "note": "trailing bucket carried forward",
        },
        "theta_semantics": config.THETA_SEMANTICS,
        "theta_layer": config.THETA_LAYER,
        "bucket_minutes": config.BUCKET_MINUTES,
        "optimizer": config.OPTIMIZER_VERSION,
        "optimized_pools": list(config.OPTIMIZED_POOLS),
    }