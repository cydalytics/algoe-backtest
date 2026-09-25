"""
Backtest package (W06).

Replay a coherent multi-tick meeting and score each lever on its own
lab: turnover, calibrated true-prob, optimizer, policy / demand.
"""

from .runner import print_report, run_backtest
from .store import get_backtest_store
from .world import MeetingWorld, build_world

__all__ = [
    "MeetingWorld",
    "build_world",
    "get_backtest_store",
    "print_report",
    "run_backtest",
]
