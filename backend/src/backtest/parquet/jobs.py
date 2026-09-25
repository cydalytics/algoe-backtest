"""Background jobs for parquet builds.

Building a window of days takes minutes, which is longer than a browser will
wait, so the API starts a job and the page polls it. One job runs at a time:
two concurrent builds could target the same cached day and race on its
manifest entry, and there is nothing to gain from that anyway since the work
is already IO-bound on one folder of parquet.

Progress is a snapshot plus a bounded log. The snapshot is what the coverage
bar reads; the log is what you look at when a run behaves oddly.

Change Log:
-----------
2026-09-15      Initialize (W06 parquet workbench)
"""

from __future__ import annotations

import threading
import traceback
import uuid
from collections import deque
from datetime import datetime
from typing import Callable, Deque, Dict, List, Optional

from src.core.logging import get_logger

log = get_logger(__name__)

LOG_LINES = 400
KEEP_JOBS = 20

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
ERROR = "error"
CANCELLED = "cancelled"
LIVE = (QUEUED, RUNNING)


class Job:
    """One build, its progress, and the flag that asks it to stop."""

    def __init__(self, kind: str, request: dict):
        self.id = "job-{}".format(uuid.uuid4().hex[:10])
        self.kind = kind
        self.request = request
        self.status = QUEUED
        self.created_at = datetime.now().isoformat(timespec="seconds")
        self.started_at: Optional[str] = None
        self.finished_at: Optional[str] = None
        self.progress: dict = {"stage": "queued", "done": 0, "total": 0}
        self.result: Optional[dict] = None
        self.error: Optional[str] = None
        self.run_id: Optional[str] = None
        self._log: Deque[str] = deque(maxlen=LOG_LINES)
        self._cancel = threading.Event()
        self._lock = threading.Lock()

    # ------------------------------------------------------------ from worker
    def note(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        with self._lock:
            self._log.append("{} {}".format(stamp, message))

    def update(self, event: dict) -> None:
        """Called by the engine on every day it finishes."""
        with self._lock:
            self.progress = {**self.progress, **event}
        text = _describe(event)
        if text:
            self.note(text)

    def cancel(self) -> None:
        self._cancel.set()
        self.note("cancel requested")

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    # --------------------------------------------------------------- readable
    def snapshot(self, log_tail: int = 60) -> dict:
        with self._lock:
            tail = list(self._log)[-log_tail:] if log_tail else []
            return {
                "id": self.id,
                "kind": self.kind,
                "status": self.status,
                "request": self.request,
                "created_at": self.created_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "progress": dict(self.progress),
                "result": self.result,
                "error": self.error,
                "run_id": self.run_id,
                "log": tail,
                "cancel_requested": self._cancel.is_set(),
            }


def _describe(event: dict) -> str:
    stage = event.get("stage")
    if stage == "day":
        return "{} rows={:,} turnover={:,.0f}{}".format(
            event.get("day"), int(event.get("rows") or 0),
            float(event.get("turnover") or 0.0),
            "" if event.get("opt_rows") is None
            else " opt={:,}".format(int(event["opt_rows"])),
        )
    if stage == "optimize":
        return "{} solved {:,} buckets".format(event.get("day"),
                                               int(event.get("opt_rows") or 0))
    if stage == "assemble":
        return None if event.get("done", 0) % 10 else "assembled through {}".format(
            event.get("day"))
    return event.get("message")


class JobManager:
    """Runs one job at a time and remembers the last few for inspection."""

    def __init__(self):
        self._jobs: Dict[str, Job] = {}
        self._order: List[str] = []
        self._current: Optional[str] = None
        self._lock = threading.Lock()

    def current(self) -> Optional[Job]:
        with self._lock:
            job = self._jobs.get(self._current or "")
        return job if job is not None and job.status in LIVE else None

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> List[dict]:
        with self._lock:
            jobs = [self._jobs[j] for j in reversed(self._order) if j in self._jobs]
        return [j.snapshot(log_tail=0) for j in jobs]

    def start(self, kind: str, request: dict,
              work: Callable[[Job], dict]) -> Job:
        """Queue a job. Raises RuntimeError when one is already running."""
        with self._lock:
            live = self._jobs.get(self._current or "")
            if live is not None and live.status in LIVE:
                raise RuntimeError(
                    "a {} job is already running ({})".format(live.kind, live.id))
            job = Job(kind, request)
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._current = job.id
            self._prune()

        thread = threading.Thread(target=self._run, args=(job, work),
                                  name=job.id, daemon=True)
        thread.start()
        return job

    def _prune(self) -> None:
        while len(self._order) > KEEP_JOBS:
            old = self._order.pop(0)
            if old != self._current:
                self._jobs.pop(old, None)

    def _run(self, job: Job, work: Callable[[Job], dict]) -> None:
        from .cache import Cancelled

        job.status = RUNNING
        job.started_at = datetime.now().isoformat(timespec="seconds")
        job.note("started {}".format(job.kind))
        try:
            job.result = work(job)
            job.status = CANCELLED if job.cancelled else DONE
        except Cancelled:
            job.status = CANCELLED
            job.note("cancelled")
        except Exception as exc:                                # noqa: BLE001
            job.status = ERROR
            job.error = "{}: {}".format(type(exc).__name__, exc)
            job.note("failed: {}".format(job.error))
            log.warning("job %s failed\n%s", job.id, traceback.format_exc())
        finally:
            job.finished_at = datetime.now().isoformat(timespec="seconds")
            job.note("finished with status {}".format(job.status))


_manager: Optional[JobManager] = None


def get_job_manager() -> JobManager:
    global _manager
    if _manager is None:
        _manager = JobManager()
    return _manager
