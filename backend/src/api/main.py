"""
Algo E API

FastAPI entry point. Run from the backend folder:

    python -m uvicorn src.api.main:app --port 8001

Everything is served from data that never leaves the machine: the HKJC
SQL Server, a parquet capture of it, or the local simulator. There is no
outbound call anywhere in this process.

The first request builds a tick, so it is slower than the ones after it.
Set ALGOE_WARM=1 to build one at startup instead.

Change Log:
-----------
2026-08-18      Initialize
2026-08-30      Rewritten for the offline pipeline
"""

import os

try:
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
except ImportError as exc:
    raise RuntimeError(
        "FastAPI not installed - run: pip install -r backend/requirements.txt"
    ) from exc

from src.api.routes import backtest, desk, parquet, runs
from src.api.state import get_state
from src.core import config
from src.core.logging import get_logger

logger = get_logger(__name__)

app = FastAPI(
    title="Algo E",
    version="0.2.0",
    description="Offline HKJC football trading desk: pricing, turnover "
                "forecast and theta optimisation over a 5-minute bucket.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.API_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(desk.router, prefix="/api", tags=["desk"])
app.include_router(runs.router, prefix="/api", tags=["history"])
app.include_router(backtest.router, prefix="/api", tags=["backtest"])
app.include_router(parquet.router, prefix="/api", tags=["parquet"])


@app.get("/api/health", tags=["desk"])
def health():
    """Liveness, plus whether there is a usable tick behind it."""
    state = get_state()
    tick = state._tick
    return {
        "status": "ok" if state.last_error is None else "degraded",
        "source": state.pipeline.source_name,
        "tick_age_seconds": round(state.age_seconds, 1),
        "stale": state.stale,
        "solving": state.solving,
        "solved": tick.solved if tick else 0,
        "matches": len(tick.contexts) if tick else 0,
        "as_of": tick.as_of.isoformat(timespec="seconds") if tick else None,
        "last_error": state.last_error,
    }


@app.on_event("startup")
def warm():
    if os.environ.get("ALGOE_WARM", "0") != "1":
        return
    try:
        get_state().refresh()
    except Exception:
        logger.exception("warm-up tick failed; the API will retry on request")
