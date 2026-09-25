"""
Trigger Evaluator

Decides whether the pipeline should rerun right now, skip this tick, or
alert. Used by the future live loop (scripts/tick.py) so the 5-minute
schedule is not blind: a big odds or theta jump reruns immediately,
while quiet markets skip the heavy work.

    decision = "run"    (material move -> rerun now)
    decision = "skip"   (nothing changed -> save compute)
    decision = "alert"  (anomaly -> rerun + surface to the board)

v1 compares the latest snapshot against the previous run's snapshot.
Thresholds come from config (TRIGGER_ODDS_MOVE_THRESHOLD etc.).

Change Log:
-----------
2026-08-18      Initialize
"""

from dataclasses import dataclass

from src.core import config
from src.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class TriggerDecision:
    """One trigger evaluation: what to do + why."""

    decision: str      # "run" | "skip" | "alert"
    reason: str
    odds_move: float = 0.0
    theta_move: float = 0.0


class TriggerEvaluator:
    """Compare the newest snapshot against the previous one."""

    def __init__(self, odds_threshold=None, theta_threshold=None):
        self.odds_threshold = (
            config.TRIGGER_ODDS_MOVE_THRESHOLD if odds_threshold is None else odds_threshold
        )
        self.theta_threshold = (
            config.TRIGGER_THETA_MOVE_THRESHOLD if theta_threshold is None else theta_threshold
        )

    def evaluate(self, current, previous):
        """Compare two snapshots (dicts of odds/theta per match).

        Args:
            current:  {"odds": {match_id: ...}, "theta": {match_id: ...}}
            previous: same shape, from the last run.

        Returns:
            TriggerDecision.
        """
        odds_move = self._max_move(current.get("odds", {}), previous.get("odds", {}))
        theta_move = self._max_move(current.get("theta", {}), previous.get("theta", {}))

        if odds_move > 2 * self.odds_threshold or theta_move > 2 * self.theta_threshold:
            return TriggerDecision(
                "alert",
                "large move: odds={:.3f} theta={:.3f}".format(odds_move, theta_move),
                odds_move, theta_move,
            )
        if odds_move > self.odds_threshold or theta_move > self.theta_threshold:
            return TriggerDecision(
                "run",
                "material move: odds={:.3f} theta={:.3f}".format(odds_move, theta_move),
                odds_move, theta_move,
            )
        return TriggerDecision(
            "skip",
            "no material move: odds={:.3f} theta={:.3f}".format(odds_move, theta_move),
            odds_move, theta_move,
        )

    @staticmethod
    def _max_move(current, previous):
        """Max absolute difference across shared keys (0 if nothing shared)."""
        moves = [
            abs(float(current[k]) - float(previous[k]))
            for k in current
            if k in previous
        ]
        return max(moves) if moves else 0.0