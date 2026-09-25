"""
Parquet Backtest Routes

Driving a real-data backtest from the page, without the page having to wait
for it:

    POST /api/parquet/probe        what is in the raw folders
    POST /api/parquet/coverage     which days of a window are already built
    POST /api/parquet/build        build the missing days, then register a run
    POST /api/parquet/optimize     solve TG/SUP over a window already built
    POST /api/parquet/register     re-register a window with no building
    POST /api/parquet/forget       drop cached days so they rebuild
    GET  /api/parquet/jobs         recent jobs
    GET  /api/parquet/jobs/{id}    one job with its log tail
    POST /api/parquet/jobs/{id}/cancel
    GET  /api/parquet/spaces       every cache space on this machine
    GET  /api/parquet/defaults     what to prefill the form with

Once a run is registered the existing /api/backtest/{id}/slice endpoints serve
it, so changing turnover or belief never comes back through here.

Change Log:
-----------
2026-09-15      Initialize (W06 parquet workbench)
"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.backtest.parquet import adapter, service
from src.backtest.parquet.cache import ENGINE_VERSION, day_list
from src.backtest.parquet.config import Config
from src.backtest.parquet.jobs import get_job_manager

router = APIRouter()


class WindowRequest(BaseModel):
    """A window plus the knobs that decide what a cached day contains."""

    start: str
    end: str
    data_dir: Optional[str] = None
    pools: Optional[List[str]] = None
    bucket_minutes: int = Field(5, ge=1, le=60)
    prematch_window_min: int = Field(360, ge=0, le=10080)
    full_span: bool = False
    lookback_days: int = Field(2, ge=0, le=14)
    true_prob_source: str = "auto"
    normalize_book: bool = True

    # optimizer stage
    optimize: bool = False
    optimize_every: int = Field(6, ge=1, le=200)
    optimize_max_buckets: int = Field(20000, ge=1, le=2_000_000)
    optimize_starts: int = Field(3, ge=1, le=5)
    optimize_demand: str = "f_persist"
    hdc_sign: str = "home"
    cache_dir: Optional[str] = None

    # assembly
    calibrators: Optional[List[str]] = None
    rebuild: bool = False

    # the three dropdowns
    turnover_model: Optional[str] = None
    belief_model: Optional[str] = None
    objective: Optional[str] = None
    solve: bool = True


def _cfg(req: WindowRequest) -> Config:
    try:
        cfg = service.config_from_request(req)
        service.check_window(cfg)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return cfg


def _calibrators(req: WindowRequest) -> List[str]:
    wanted = req.calibrators or list(adapter.DEFAULT_CALIBRATORS)
    return ["raw"] + [c for c in wanted if c != "raw"]


@router.get("/parquet/defaults")
def defaults():
    d = Config()
    return {
        "data_dir": str(d.data_dir),
        "start": d.start_date,
        "end": d.end_date,
        "pools": [],
        "bucket_minutes": d.bucket_minutes,
        "prematch_window_min": d.prematch_window_min,
        "lookback_days": d.lookback_days,
        "true_prob_source": d.true_prob_source,
        "calibrators": list(adapter.DEFAULT_CALIBRATORS),
        "optimize_every": d.optimize_every,
        "optimize_demand": d.optimize_demand,
        "hdc_sign": d.hdc_sign,
        "engine": ENGINE_VERSION,
        "max_days": service.MAX_DAYS,
        "cache_root": str(service.cache_root()),
        "choices": service.choices(),
        "turnover_labels": dict(adapter.TURNOVER_LABELS),
        "belief_labels": dict(adapter.BELIEF_LABELS),
    }


@router.post("/parquet/probe")
def probe(req: WindowRequest):
    return service.probe(_cfg(req))


@router.post("/parquet/coverage")
def coverage(req: WindowRequest):
    cov = service.coverage(_cfg(req))
    job = get_job_manager().current()
    return {"coverage": cov, "job": job.snapshot() if job else None}


@router.post("/parquet/build")
def build(req: WindowRequest):
    cfg = _cfg(req)
    try:
        job = service.start_build(cfg, optimize=req.optimize, rebuild=req.rebuild,
                                  calibrators=_calibrators(req))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"job": job.snapshot()}


@router.post("/parquet/optimize")
def optimize(req: WindowRequest):
    cfg = _cfg(req)
    try:
        job = service.start_optimizer(cfg, rebuild=req.rebuild,
                                      calibrators=_calibrators(req))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"job": job.snapshot()}


@router.post("/parquet/open")
def open_window(req: WindowRequest):
    """Show one turnover x belief x objective choice, producing what is missing."""
    cfg = _cfg(req)
    try:
        job = service.start_open(cfg, calibrators=_calibrators(req), solve=req.solve)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    cache = service.open_cache(cfg)
    return {"job": job.snapshot(), "run_id": service.expected_run_id(cfg, cache)}


@router.post("/parquet/register")
def register(req: WindowRequest):
    """Re-assemble a window from cached days, without building anything.

    Used when the calibrator list changes: that is an assembly choice, not a
    panel one, so it must not trigger a rebuild.
    """
    cfg = _cfg(req)
    cache = service.open_cache(cfg)
    if not [d for d in day_list(cfg.start_date, cfg.end_date) if cache.has_day(d)]:
        raise HTTPException(
            status_code=409,
            detail="no cached days in this window; run a build first")
    try:
        job = service.start_register(cfg, calibrators=_calibrators(req))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"job": job.snapshot()}


@router.post("/parquet/forget")
def forget(req: WindowRequest):
    cfg = _cfg(req)
    cache = service.open_cache(cfg)
    n = cache.drop(day_list(cfg.start_date, cfg.end_date))
    return {"dropped": n, "coverage": service.coverage(cfg)}


@router.get("/parquet/spaces")
def spaces():
    return {"spaces": service.spaces()}


@router.get("/parquet/jobs")
def jobs():
    manager = get_job_manager()
    current = manager.current()
    return {"jobs": manager.list(), "current": current.id if current else None}


@router.get("/parquet/jobs/{job_id}")
def job(job_id: str, log_tail: int = 120):
    found = get_job_manager().get(job_id)
    if found is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return {"job": found.snapshot(log_tail=log_tail)}


@router.post("/parquet/jobs/{job_id}/cancel")
def cancel(job_id: str):
    found = get_job_manager().get(job_id)
    if found is None:
        raise HTTPException(status_code=404, detail="unknown job")
    found.cancel()
    return {"job": found.snapshot()}
