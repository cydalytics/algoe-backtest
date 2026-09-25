"""
API State

Holds the pipeline and the most recent tick for the process.

THE BOARD MUST NEVER WAIT FOR THE SOLVER. Fetching, preprocessing and
pricing a full card takes a couple of seconds; optimising it takes minutes.
If one request does both, the desk stares at a dead screen for the length
of the solve and the browser gives up long before it ends.

So a tick is published in two stages. The priced book goes out as soon as
it exists - every price, every edge, the whole tape - and a background
worker then solves the matches one at a time, richest first, publishing
each recommendation onto the live tick as it lands. The board fills in
while the trader is already reading it.

That means a tick is mutated after it is served. Only the optimizer's own
outputs are ever written (results, and the theta* price columns for the
match just solved), always under the tick's lock, and the payload readers
take the same lock. Nothing else about a published tick changes.

Change Log:
-----------
2026-08-30      Initialize
2026-08-30      Publish the priced book before the solve, then fill in
"""

import threading
import time
from typing import Optional

from src.core import config
from src.core.logging import get_logger
from src.optimizer import optimizer as opt
from src.pipeline import DeskTick, Pipeline

logger = get_logger(__name__)


class DeskState:
    """The process-wide latest tick, plus the worker that finishes it."""

    def __init__(self, source=None, ttl_seconds=None):
        self.pipeline = Pipeline(source=source)
        self.ttl = config.TICK_TTL_SECONDS if ttl_seconds is None else ttl_seconds
        self._tick: Optional[DeskTick] = None
        self._built_at = 0.0
        self._lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None
        self._generation = 0
        self.last_error: Optional[str] = None

    # -- what the routes read ------------------------------------------------

    @property
    def age_seconds(self) -> float:
        return 0.0 if self._tick is None else time.time() - self._built_at

    @property
    def stale(self) -> bool:
        return self._tick is None or self.age_seconds > self.ttl

    @property
    def solving(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def current(self, refresh: bool = False) -> DeskTick:
        """The latest tick, running one first if it is missing or stale."""
        if refresh or self.stale:
            return self.refresh()
        return self._tick

    def refresh(self, **kwargs) -> DeskTick:
        """Publish a freshly priced tick, then solve it in the background.

        Returns as soon as the book is priced. The recommendation columns
        arrive over the next seconds; until then a match reports itself as
        unsolved rather than pretending to have an answer.
        """
        with self._lock:
            started = time.time()
            # Another request may have refreshed while we waited for the
            # lock; serving that is better than immediately doing it again.
            if not kwargs and self._tick is not None and self._built_at > started - 1.0:
                return self._tick
            try:
                tick = self.pipeline.run(optimize=False, **kwargs)
            except Exception as exc:
                self.last_error = str(exc)
                logger.exception("tick failed")
                if self._tick is None:
                    raise
                return self._tick
            self.last_error = None
            self._tick = tick
            self._built_at = time.time()
            self._generation += 1

        if config.OPTIMIZE_ASYNC:
            self._start_worker(tick, self._generation)
        else:
            self._solve(tick, self._generation)
        return tick

    # -- the solve -----------------------------------------------------------

    def _start_worker(self, tick: DeskTick, generation: int):
        """Hand the solve to a background thread.

        The previous worker is not interrupted - it checks the generation
        after each match and stops on its own once its tick is superseded,
        which avoids killing a solve halfway through writing a result.
        """
        worker = threading.Thread(
            target=self._solve, args=(tick, generation),
            name="algoe-optimizer", daemon=True,
        )
        self._worker = worker
        worker.start()

    def _solve(self, tick: DeskTick, generation: int):
        started = time.time()

        def publish(match_id, result):
            if generation != self._generation:
                raise _Superseded()
            self.pipeline.publish(tick, match_id, result)

        try:
            opt.optimize_tick(
                tick, budget_seconds=config.OPTIMIZER_BUDGET_SECONDS or None,
                on_result=publish,
            )
        except _Superseded:
            logger.info("solve for %s abandoned: a newer tick arrived", tick.run_id)
            return
        except Exception:                                     # noqa: BLE001
            logger.exception("background solve failed for %s", tick.run_id)
            return

        tick.timings["optimize"] = round(time.time() - started, 3)
        logger.info(
            "tick %s solved: %d of %d matches in %.1fs",
            tick.run_id, len(tick.results), len(tick.contexts),
            time.time() - started,
        )


class _Superseded(Exception):
    """Raised inside the worker when a newer tick has replaced ours."""


_state: Optional[DeskState] = None


def get_state() -> DeskState:
    global _state
    if _state is None:
        _state = DeskState()
    return _state
