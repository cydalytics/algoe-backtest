"""
Pipeline

One tick of the loop, start to finish:

    source -> preprocess -> true prob -> turnover -> price
           -> optimize -> re-price at theta* -> DeskTick

Everything the desk sees comes out of one of these. The tick is held in
memory and served straight to the API, and optionally written to SQLite so
a session can be replayed or audited afterwards.

The re-price at the end is what makes the cockpit's before-and-after
possible: the same selections are priced twice, once at the theta the desk
is running and once at the theta the optimizer recommends, and both sets of
columns travel together on the same rows.

Change Log:
-----------
2026-08-30      Initialize
"""

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from src.core import config
from src.core.logging import get_logger
from src.features.preprocessor import Preprocessor, TickFrame
from src.hkjc.source import get_source
from src.models.pricer import Pricer
from src.models.true_prob import TrueProbModel
from src.models.turnover import TurnoverModel
from src.optimizer import optimizer as opt

logger = get_logger(__name__)

STAR = "_star"


@dataclass
class DeskTick:
    """A finished tick: the book, the recommendation, and the timings.

    A tick is published as soon as it is priced and then filled in by the
    background solver, so ``results`` and the theta* columns grow after
    readers already hold it. ``lock`` guards exactly that window: writers
    take it around one match's publish, readers take it around building a
    payload, and nothing else about the tick ever changes.
    """

    run_id: str
    as_of: datetime
    source: str
    frame: TickFrame
    results: Dict[int, opt.MatchResult] = field(default_factory=dict)
    timings: Dict[str, float] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    totals: Dict[str, float] = field(default_factory=dict)
    lock: threading.RLock = field(
        default_factory=threading.RLock, repr=False, compare=False)

    @property
    def selections(self) -> pd.DataFrame:
        return self.frame.selections

    @property
    def solved(self) -> int:
        return len(self.results)

    @property
    def pending(self) -> int:
        return max(len(self.frame.contexts) - len(self.results), 0)

    @property
    def contexts(self):
        return self.frame.contexts

    def match(self, match_id: int):
        return self.frame.contexts.get(int(match_id))

    def result(self, match_id: int) -> Optional[opt.MatchResult]:
        return self.results.get(int(match_id))

    def rows(self, match_id: int) -> pd.DataFrame:
        sel = self.frame.selections
        return sel[sel["match_id"] == int(match_id)]


class Pipeline:
    """Runs ticks against whichever source is configured."""

    def __init__(self, source=None, optimize=True):
        self.source_name = source or config.DATA_SOURCE
        self.optimize = optimize
        self.pre = Preprocessor()
        self.true_prob = TrueProbModel()
        self.turnover = TurnoverModel()
        self.pricer = Pricer()
        self._seq = 0

    def run(self, as_of=None, match_ids=None, with_curves=False,
            optimize=None) -> DeskTick:
        """Execute one tick and return everything it produced.

        Args:
            optimize: override the instance default. The API passes False
                and then solves in the background, so the desk gets the
                priced book without waiting for the recommendation.
        """
        optimize = self.optimize if optimize is None else optimize
        timings = {}
        clock = time.time()

        snapshot = get_source(self.source_name).fetch(as_of, match_ids)
        timings["fetch"] = _since(clock)

        clock = time.time()
        frame = self.pre.build(snapshot)
        timings["preprocess"] = _since(clock)

        clock = time.time()
        self.true_prob.attach(frame)
        self.turnover.attach(frame)
        timings["models"] = _since(clock)

        clock = time.time()
        self.pricer.attach(frame)
        timings["price"] = _since(clock)

        results = {}
        if optimize:
            clock = time.time()
            results = opt.optimize_tick(frame, with_curves=with_curves)
            timings["optimize"] = _since(clock)

            clock = time.time()
            self.pricer.attach(
                frame,
                theta_by_match={m: r.theta_star for m, r in results.items()},
                suffix=STAR,
            )
            timings["reprice"] = _since(clock)

        self._seq += 1
        tick = DeskTick(
            run_id="{}-{:04d}".format(
                frame.as_of.strftime("%Y%m%d-%H%M%S"), self._seq),
            as_of=frame.as_of,
            source=frame.source,
            frame=frame,
            results=results,
            timings=timings,
            warnings=list(frame.warnings),
        )
        tick.totals = self._totals(tick)
        logger.info(
            "tick %s: %d matches, %d selections, E[GM] %.0f -> %.0f in %.2fs",
            tick.run_id, len(frame.contexts), len(frame.selections),
            tick.totals.get("gm_now", 0), tick.totals.get("gm_star", 0),
            sum(timings.values()),
        )
        return tick

    # -----------------------------------------------------------------------

    def publish(self, tick: DeskTick, match_id: int, result) -> None:
        """Attach one solved match to a tick that is already being served.

        Prices that match's rows at its recommended theta and refreshes the
        desk totals, so the board's headline grows as the card is solved
        rather than jumping at the end.
        """
        with tick.lock:
            tick.results[int(match_id)] = result
            self.pricer.attach_match(tick, match_id, result.theta_star, STAR)
            tick.totals = self._totals(tick)

    def _totals(self, tick: DeskTick) -> Dict[str, float]:
        """Desk-level headline numbers for the board."""
        sel = tick.selections
        now = self.pricer.expected_gm(sel)
        totals = {
            "turnover": now["turnover"],
            "payout_now": now["payout"],
            "gm_now": now["gm"],
            "margin_now": now["margin_pct"],
            "legs": now["legs"],
            "matches": len(tick.contexts),
            "selections": int(len(sel)),
        }
        if not tick.results:
            totals.update(gm_star=now["gm"], uplift=0.0, uplift_bps=0.0)
            return totals

        # The optimizer's own numbers are the ones to headline: they cover
        # exactly the legs it controls, where the re-priced book also
        # carries pools it was never asked to move.
        gm_now = sum(r.gm_now for r in tick.results.values())
        gm_star = sum(r.gm_star for r in tick.results.values())
        turnover = sum(r.turnover for r in tick.results.values())
        totals.update(
            opt_turnover=round(turnover, 2),
            opt_gm_now=round(gm_now, 2),
            gm_star=round(gm_star, 2),
            uplift=round(gm_star - gm_now, 2),
            uplift_bps=round(1e4 * (gm_star - gm_now) / turnover, 1)
            if turnover > 0 else 0.0,
            optimized_legs=sum(r.legs for r in tick.results.values()),
            skipped_legs=sum(r.skipped for r in tick.results.values()),
        )
        return totals


def _since(clock) -> float:
    return round(time.time() - clock, 3)
