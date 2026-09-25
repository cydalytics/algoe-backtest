"""
History Routes

Reads the SQLite tick store - the record of what the desk was shown and
what it was told to do, tick by tick.

    GET /api/history            recent ticks
    GET /api/history/match/{id} one match's trail across ticks
    GET /api/history/{run_id}   the stored book for one tick

Change Log:
-----------
2026-08-30      Rewritten against the tick store
"""

from fastapi import APIRouter, HTTPException, Query

from src.database.sqlite_store import get_store

router = APIRouter()


def _records(frame):
    return frame.where(frame.notna(), None).to_dict(orient="records")


@router.get("/history")
def history(limit: int = Query(50, ge=1, le=500)):
    """Recent ticks, newest first."""
    return {"ticks": _records(get_store().list_ticks(limit))}


@router.get("/history/match/{match_id}")
def match_history(match_id: int, limit: int = Query(200, ge=1, le=1000)):
    """How one match's recommendation moved from tick to tick."""
    frame = get_store().match_history(match_id, limit)
    if frame.empty:
        raise HTTPException(404, "no stored ticks for match {}".format(match_id))
    return {"match_id": match_id, "ticks": _records(frame)}


@router.get("/history/{run_id}")
def tick_detail(run_id: str, match_id: int = Query(None)):
    """The stored book for one tick, optionally one match of it."""
    frame = get_store().legs(run_id, match_id)
    if frame.empty:
        raise HTTPException(404, "no stored legs for run {}".format(run_id))
    return {"run_id": run_id, "legs": _records(frame)}
