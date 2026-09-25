"""
Backtest Routes

    GET  /api/backtest                recent runs (headlines)
    GET  /api/backtest/latest         full report of the newest run
    GET  /api/backtest/latest/slice   workbench slice of the newest run
    GET  /api/backtest/catalog        names the UI can offer
    GET  /api/backtest/{id}           one stored report
    GET  /api/backtest/{id}/slice     workbench slice (filters + compose)
    POST /api/backtest/{id}/slice     same, JSON body
    POST /api/backtest                run the lab sweep (synchronous)

Change Log:
-----------
2026-09-11      Initialize (W06 backtest)
2026-09-11      Labs request body
2026-09-12      Workbench slice
"""

from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.backtest.predictors import catalog
from src.backtest.runner import run_backtest
from src.backtest.slice import compare_cached, slice_cached
from src.backtest.store import get_backtest_store

router = APIRouter()


class BacktestRequest(BaseModel):
    seed: Optional[int] = None
    n_matches: int = Field(3, ge=1, le=16)
    n_ticks: int = Field(6, ge=2, le=36)
    start: Optional[str] = None
    turnover: Optional[List[str]] = None
    true_prob: Optional[List[str]] = None
    calibrators: Optional[List[str]] = None
    algos: Optional[List[str]] = None
    policies: Optional[List[str]] = None
    elasticities: Optional[List[float]] = None
    optimize: str = "none"


class SliceRequest(BaseModel):
    turnover: str = "persistence"
    true_prob: str = "hkjc_true"
    calibrator: str = "raw"
    algo: str = "hold"
    group_by: str = "clock_bin"
    filters: Optional[Dict[str, List[str]]] = None


class StackSpec(BaseModel):
    turnover: str = "persistence"
    true_prob: str = "hkjc_true"
    calibrator: str = "raw"
    algo: str = "hold"


class CompareRequest(BaseModel):
    left: StackSpec = StackSpec()
    right: StackSpec = StackSpec(
        turnover="gametime", true_prob="hkjc_true", calibrator="raw", algo="hold",
    )
    group_by: str = "clock_bin"
    filters: Optional[Dict[str, List[str]]] = None


@router.get("/backtest")
def list_runs(limit: int = 20):
    return {"runs": get_backtest_store().list(limit)}


@router.get("/backtest/latest")
def latest():
    report = get_backtest_store().latest()
    if report is None:
        return {"report": None}
    return {"report": report}


@router.get("/backtest/catalog")
def catalog_route():
    return catalog()


@router.get("/backtest/latest/slice")
def latest_slice(
    turnover: str = "persistence",
    true_prob: str = "hkjc_true",
    calibrator: str = "raw",
    algo: str = "hold",
    group_by: str = "clock_bin",
    period: Optional[List[str]] = Query(None),
    half: Optional[List[str]] = Query(None),
    clock_bin: Optional[List[str]] = Query(None),
    pool_code: Optional[List[str]] = Query(None),
    family: Optional[List[str]] = Query(None),
    domain: Optional[List[str]] = Query(None),
    seg: Optional[List[str]] = Query(None),
    selection: Optional[List[str]] = Query(None),
    league: Optional[List[str]] = Query(None),
    competition: Optional[List[str]] = Query(None),
    event_window: Optional[List[str]] = Query(None),
    tg_bin: Optional[List[str]] = Query(None),
    sup_bin: Optional[List[str]] = Query(None),
    tg_sup: Optional[List[str]] = Query(None),
    odds_band: Optional[List[str]] = Query(None),
    kind: Optional[List[str]] = Query(None),
):
    report, facts = get_backtest_store().latest_facts()
    if report is None:
        raise HTTPException(404, "no backtest stored")
    if facts is None:
        raise HTTPException(409, "this run has no fact table — re-run labs")
    filters = _query_filters(
        period=period, half=half, clock_bin=clock_bin, pool_code=pool_code,
        family=family, domain=domain, seg=seg, selection=selection,
        league=league, competition=competition, event_window=event_window,
        tg_bin=tg_bin, sup_bin=sup_bin, tg_sup=tg_sup, odds_band=odds_band,
        kind=kind,
    )
    return {
        "run_id": report["run_id"],
        "slice": slice_cached(
            report["run_id"], facts, turnover=turnover, true_prob=true_prob,
            calibrator=calibrator, algo=algo, filters=filters, group_by=group_by,
        ),
    }


@router.post("/backtest/latest/slice")
def latest_slice_post(req: SliceRequest):
    report, facts = get_backtest_store().latest_facts()
    if report is None:
        raise HTTPException(404, "no backtest stored")
    if facts is None:
        raise HTTPException(409, "this run has no fact table — re-run labs")
    return {
        "run_id": report["run_id"],
        "slice": slice_cached(
            report["run_id"], facts, turnover=req.turnover, true_prob=req.true_prob,
            calibrator=req.calibrator, algo=req.algo,
            filters=req.filters, group_by=req.group_by,
        ),
    }


@router.post("/backtest/latest/compare")
def latest_compare(req: CompareRequest):
    report, facts = get_backtest_store().latest_facts()
    if report is None:
        raise HTTPException(404, "no backtest stored")
    if facts is None:
        raise HTTPException(409, "this run has no fact table — re-run labs")
    return {
        "run_id": report["run_id"],
        "compare": compare_cached(
            report["run_id"], facts,
            left=_spec(req.left),
            right=_spec(req.right),
            filters=req.filters,
            group_by=req.group_by,
        ),
    }


@router.get("/backtest/{run_id}/slice")
def get_slice(
    run_id: str,
    turnover: str = "persistence",
    true_prob: str = "hkjc_true",
    calibrator: str = "raw",
    algo: str = "hold",
    group_by: str = "clock_bin",
    period: Optional[List[str]] = Query(None),
    half: Optional[List[str]] = Query(None),
    clock_bin: Optional[List[str]] = Query(None),
    pool_code: Optional[List[str]] = Query(None),
    family: Optional[List[str]] = Query(None),
    domain: Optional[List[str]] = Query(None),
    seg: Optional[List[str]] = Query(None),
    selection: Optional[List[str]] = Query(None),
    league: Optional[List[str]] = Query(None),
    competition: Optional[List[str]] = Query(None),
    event_window: Optional[List[str]] = Query(None),
    tg_bin: Optional[List[str]] = Query(None),
    sup_bin: Optional[List[str]] = Query(None),
    tg_sup: Optional[List[str]] = Query(None),
    odds_band: Optional[List[str]] = Query(None),
    kind: Optional[List[str]] = Query(None),
):
    return _slice_run(
        run_id,
        turnover=turnover, true_prob=true_prob, calibrator=calibrator,
        algo=algo, group_by=group_by,
        filters=_query_filters(
            period=period, half=half, clock_bin=clock_bin, pool_code=pool_code,
            family=family, domain=domain, seg=seg, selection=selection,
            league=league, competition=competition, event_window=event_window,
            tg_bin=tg_bin, sup_bin=sup_bin, tg_sup=tg_sup, odds_band=odds_band,
            kind=kind,
        ),
    )


@router.post("/backtest/{run_id}/slice")
def post_slice(run_id: str, req: SliceRequest):
    return _slice_run(
        run_id,
        turnover=req.turnover, true_prob=req.true_prob,
        calibrator=req.calibrator, algo=req.algo,
        group_by=req.group_by, filters=req.filters,
    )


@router.post("/backtest/{run_id}/compare")
def post_compare(run_id: str, req: CompareRequest):
    store = get_backtest_store()
    report = store.get(run_id)
    if report is None:
        raise HTTPException(404, "no backtest {}".format(run_id))
    facts = store.load_facts(run_id)
    if facts is None:
        raise HTTPException(409, "this run has no fact table — re-run labs")
    return {
        "run_id": run_id,
        "compare": compare_cached(
            run_id, facts,
            left=_spec(req.left),
            right=_spec(req.right),
            filters=req.filters,
            group_by=req.group_by,
        ),
    }


@router.get("/backtest/{run_id}")
def get_run(run_id: str):
    report = get_backtest_store().get(run_id)
    if report is None:
        raise HTTPException(404, "no backtest {}".format(run_id))
    return {"report": report}


@router.post("/backtest")
def start_run(req: BacktestRequest):
    if req.optimize not in ("star", "all", "none"):
        raise HTTPException(400, "optimize must be star, all or none")
    start = None
    if req.start:
        try:
            start = datetime.fromisoformat(req.start)
        except ValueError as exc:
            raise HTTPException(400, "bad start: {}".format(exc)) from exc
    try:
        report = run_backtest(
            seed=req.seed,
            n_matches=req.n_matches,
            n_ticks=req.n_ticks,
            start=start,
            turnover=req.turnover,
            true_prob=req.true_prob,
            calibrators=req.calibrators,
            algos=req.algos,
            policies=req.policies,
            elasticities=req.elasticities,
            optimize=req.optimize,
            persist=True,
        )
    except Exception as exc:                              # noqa: BLE001
        raise HTTPException(500, str(exc)) from exc
    return {"report": report}


def _slice_run(run_id, turnover, true_prob, calibrator, algo, group_by, filters):
    store = get_backtest_store()
    report = store.get(run_id)
    if report is None:
        raise HTTPException(404, "no backtest {}".format(run_id))
    facts = store.load_facts(run_id)
    if facts is None:
        raise HTTPException(409, "this run has no fact table — re-run labs")
    return {
        "run_id": run_id,
        "slice": slice_cached(
            run_id, facts, turnover=turnover, true_prob=true_prob,
            calibrator=calibrator, algo=algo, filters=filters, group_by=group_by,
        ),
    }


def _spec(model):
    return model.model_dump() if hasattr(model, "model_dump") else model.dict()


def _query_filters(**kwargs):
    out = {}
    for key, values in kwargs.items():
        if not values:
            continue
        out[key] = [str(v) for v in values if v not in (None, "", "all")]
    return out
