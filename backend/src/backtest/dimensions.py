"""
Slice Dimensions

Every scored row gets the cuts the meeting actually asks for: pre vs
in-play, half, clock, bet type, selection, league, competition, TG/SUP
bucket, and where we sit relative to the last goal or corner.

These columns live on the tick so a later filter is just a mask.

Change Log:
-----------
2026-09-12      Initialize (W06 workbench)
"""

from datetime import datetime

import pandas as pd

from src.core import config

DIM_COLS = (
    "as_of", "label_key", "match_id", "home", "away",
    "league", "league_code", "country", "competition",
    "phase", "half", "period", "minute", "clock_bin", "day",
    "pool_code", "family", "domain", "seg", "kind",
    "selection", "sel_label", "is_main_line",
    "tg", "sup", "tg_bin", "sup_bin", "tg_sup",
    "event_kind", "minutes_since_goal", "minutes_since_corner",
    "event_window", "odds_band",
)

FILTER_COLS = (
    "period", "half", "clock_bin", "clock_fine", "phase",
    "pool_code", "family", "domain", "seg", "kind",
    "selection", "sel_label",
    "league", "league_code", "country", "competition",
    "event_window", "event_kind",
    "tg_bin", "sup_bin", "tg_sup", "odds_band",
    "lag_state",
)

GROUP_COLS = FILTER_COLS + ("day",)

HALF = {
    "prematch": "pre",
    "first_half": "1H",
    "half_time": "HT",
    "second_half": "2H",
    "full_time": "FT",
}

PERIOD = {
    "prematch": "prematch",
    "first_half": "inplay",
    "half_time": "inplay",
    "second_half": "inplay",
    "full_time": "full_time",
}

CLOCK_ORDER = (
    "pre", "00-15", "15-30", "30-45", "ht",
    "45-60", "60-75", "75-90", "90+", "ft", "unk",
)

# The finer prematch cut the parquet panel produces. Listed here so a
# breakdown on clock_fine comes out in clock order rather than alphabetically.
CLOCK_FINE_ORDER = (
    "pre >6h", "pre 6-2h", "pre 2-1h", "pre 60-15m", "pre 15-0m",
    "live 0-15m", "live 15-30m", "live 30-45m", "live 45-60m",
    "live 60-75m", "live 75-90m", "live 90m+", "live (no clock)",
    "unknown",
)


def attach_dimensions(frame, world) -> None:
    """Write slice columns onto ``frame.selections`` in place."""
    sel = frame.selections
    if sel.empty:
        return

    meta = pd.DataFrame([
        {
            "match_id": m.match_id,
            "_home": m.home,
            "_away": m.away,
            "_league": m.league_name,
            "_league_code": m.league_code,
            "_country": m.country,
            "_competition": getattr(m, "competition", "league"),
        }
        for m in world.matches
    ])
    if not meta.empty:
        sel["match_id"] = sel["match_id"].astype(int)
        meta["match_id"] = meta["match_id"].astype(int)
        sel = sel.merge(meta, on="match_id", how="left")
        ctx_home, ctx_away, ctx_league, ctx_code = {}, {}, {}, {}
        ctx_tg, ctx_sup = {}, {}
        for mid, ctx in (frame.contexts or {}).items():
            ctx_home[int(mid)] = ctx.home
            ctx_away[int(mid)] = ctx.away
            ctx_league[int(mid)] = ctx.league
            ctx_code[int(mid)] = ctx.league_code
            theta = (ctx.theta or {}).get(config.THETA_LAYER) or (ctx.theta or {}).get("Current") or {}
            ctx_tg[int(mid)] = float(theta.get("goal_tg") or 0.0)
            ctx_sup[int(mid)] = float(theta.get("goal_sup") or 0.0)
        sel["home"] = sel["match_id"].map(ctx_home).fillna(sel["_home"])
        sel["away"] = sel["match_id"].map(ctx_away).fillna(sel["_away"])
        sel["league"] = sel["match_id"].map(ctx_league).fillna(sel["_league"])
        sel["league_code"] = sel["match_id"].map(ctx_code).fillna(sel["_league_code"])
        sel["country"] = sel["_country"]
        sel["competition"] = sel["_competition"].fillna("league")
        sel["tg"] = sel["match_id"].map(ctx_tg).fillna(0.0)
        sel["sup"] = sel["match_id"].map(ctx_sup).fillna(0.0)
        sel.drop(columns=[c for c in sel.columns if c.startswith("_")], inplace=True)
    else:
        sel["home"] = ""
        sel["away"] = ""
        sel["league"] = ""
        sel["league_code"] = ""
        sel["country"] = ""
        sel["competition"] = "league"
        sel["tg"] = 0.0
        sel["sup"] = 0.0

    phase = sel["phase"].astype(str) if "phase" in sel.columns else pd.Series("", index=sel.index)
    sel["half"] = phase.map(HALF).fillna("pre")
    sel["period"] = phase.map(PERIOD).fillna("prematch")
    minute = pd.to_numeric(sel["minute"], errors="coerce") if "minute" in sel.columns else pd.Series(None, index=sel.index)
    sel["clock_bin"] = [_clock_bin(p, m) for p, m in zip(phase, minute)]
    sel["tg_bin"] = sel["tg"].map(_tg_bin)
    sel["sup_bin"] = sel["sup"].map(_sup_bin)
    sel["tg_sup"] = sel["tg_bin"].astype(str) + "|" + sel["sup_bin"].astype(str)

    as_of = pd.Timestamp(frame.as_of).to_pydatetime()
    events = world.events if world.events is not None and not getattr(world.events, "empty", True) else pd.DataFrame()
    ev_map = {}
    for mid, ph in sel.groupby("match_id")["phase"].first().items():
        ev_map[int(mid)] = _event_state(events, int(mid), as_of, str(ph))
    ev = sel["match_id"].map(lambda mid: ev_map.get(int(mid), ("none", None, None, "quiet")))
    sel["event_kind"] = ev.map(lambda r: r[0])
    sel["minutes_since_goal"] = ev.map(lambda r: r[1])
    sel["minutes_since_corner"] = ev.map(lambda r: r[2])
    sel["event_window"] = ev.map(lambda r: r[3])

    odds_col = "hkjc_true_odds" if "hkjc_true_odds" in sel.columns else (
        "hkjc_odds" if "hkjc_odds" in sel.columns else None
    )
    if odds_col:
        sel["odds_band"] = pd.to_numeric(sel[odds_col], errors="coerce").map(_odds_band)
    else:
        sel["odds_band"] = "mid"

    frame.selections = sel


def _clock_bin(phase: str, minute) -> str:
    if phase == "prematch":
        return "pre"
    if phase == "half_time":
        return "ht"
    if phase == "full_time":
        return "ft"
    try:
        if minute is None or (isinstance(minute, float) and pd.isna(minute)):
            return "unk"
        m = float(minute)
    except (TypeError, ValueError):
        return "unk"
    if m < 15:
        return "00-15"
    if m < 30:
        return "15-30"
    if m < 45:
        return "30-45"
    if m < 60:
        return "45-60"
    if m < 75:
        return "60-75"
    if m < 90:
        return "75-90"
    return "90+"


def _tg_bin(tg) -> str:
    try:
        v = float(tg)
    except (TypeError, ValueError):
        return "mid"
    if v < 1.8:
        return "low"
    if v < 2.6:
        return "mid"
    if v < 3.4:
        return "high"
    return "very_high"


def _sup_bin(sup) -> str:
    try:
        v = float(sup)
    except (TypeError, ValueError):
        return "balanced"
    if v < -0.45:
        return "away"
    if v < 0.45:
        return "balanced"
    return "home"


def _odds_band(odds) -> str:
    try:
        if odds is None or (isinstance(odds, float) and pd.isna(odds)):
            return "mid"
        o = float(odds)
    except (TypeError, ValueError):
        return "mid"
    if o < 1.8:
        return "favourite"
    if o > 3.5:
        return "longshot"
    return "mid"


# Public names for the bin helpers. The parquet adapter has to cut TG, SUP and
# odds exactly the same way, and a second copy of the thresholds would drift.
tg_bin = _tg_bin
sup_bin = _sup_bin
odds_band = _odds_band


def _event_state(events, match_id, as_of: datetime, phase: str):
    if phase == "prematch" or events is None or getattr(events, "empty", True):
        return "none", None, None, "prematch" if phase == "prematch" else "quiet"
    rows = events[events["match_id"] == match_id]
    if rows.empty:
        return "none", None, None, "quiet"
    if "provider_id" in rows.columns:
        rows = rows[rows["provider_id"] == 1]
    rows = rows[pd.to_datetime(rows["incident_datetime"]) <= as_of]
    if rows.empty:
        return "none", None, None, "quiet"

    def age(kind_code):
        hit = rows[rows["incident_type"] == kind_code]
        if hit.empty:
            return None
        last = pd.to_datetime(hit["incident_datetime"]).max().to_pydatetime()
        return (as_of - last).total_seconds() / 60.0

    sg = age(config.INCIDENT_GOAL)
    sc = age(config.INCIDENT_CORNER)
    last_row = rows.sort_values("incident_datetime").iloc[-1]
    itype = int(last_row["incident_type"])
    if itype == config.INCIDENT_GOAL:
        last_kind = "goal"
    elif itype == config.INCIDENT_CORNER:
        last_kind = "corner"
    elif itype == config.INCIDENT_YELLOW:
        last_kind = "yellow"
    else:
        last_kind = "other"

    if sg is not None and sg <= 5:
        window = "post_goal_5"
    elif sc is not None and sc <= 5:
        window = "post_corner_5"
    elif sg is not None and sg <= 15:
        window = "post_goal_15"
    else:
        window = "quiet"
    return (
        last_kind,
        None if sg is None else round(sg, 2),
        None if sc is None else round(sc, 2),
        window,
    )
