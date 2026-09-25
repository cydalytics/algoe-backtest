"""
Tradable-Match Filter Checks

The notebook's active-pool query is `start_sell_datetime <= GETDATE()` with
no stop-sell condition, so it keeps returning pools for matches that
finished hours ago. That is why a live card came back full of second halves
in the ninety-third minute when `sim` had looked perfect.

This pins down what makes a match tradable, and - just as important - that
the filter can never empty the board on its own.

    python tests/test_tradable.py

Change Log:
-----------
2026-08-30      Initialize
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import config                             # noqa: E402
from src.features.preprocessor import Preprocessor      # noqa: E402
from src.hkjc.snapshot import Snapshot                  # noqa: E402
from src.hkjc.source import get_source                  # noqa: E402

FAILURES = []
AS_OF = datetime(2026, 9, 1, 20, 15)


def check(name, ok, detail=""):
    print("  {:<58} {}".format(name, "ok" if ok else "FAILED"))
    if not ok:
        FAILURES.append("{}{}".format(name, ": " + detail if detail else ""))


def live_snapshot():
    """A simulated snapshot, which by construction is all tradable."""
    return get_source("sim").fetch(AS_OF)


def finish(snap: Snapshot, match_id, home=2, away=1):
    """Publish a full-time result, the way the feed does when it ends."""
    info = snap.match_info.copy()
    rows = info["match_id"].astype(int) == int(match_id)
    info.loc[rows, "HomeScoreFT"] = home
    info.loc[rows, "AwayScoreFT"] = away
    return info


def main():
    print("tradable-match filter\n")
    pre = Preprocessor()

    snap = live_snapshot()
    frame = pre.build(snap)
    live_ids = sorted(frame.contexts)
    check("a live card survives untouched", len(live_ids) > 3,
          "{} matches".format(len(live_ids)))

    # -- 1. a published full-time result ------------------------------------
    print("\na match the feed has settled")
    ended = live_ids[0]
    snap_done = live_snapshot()
    snap_done.match_info = finish(snap_done, ended)
    frame_done = pre.build(snap_done)
    check("the finished match is off the board", ended not in frame_done.contexts)
    check("nothing else is lost",
          sorted(frame_done.contexts) == [i for i in live_ids if i != ended])
    check("its selections go with it",
          frame_done.selections[
              frame_done.selections["match_id"] == ended].empty)
    check("the drop is explained",
          any("not tradable" in w for w in frame_done.warnings),
          "; ".join(frame_done.warnings))

    # -- 2. a voided match ---------------------------------------------------
    print("\na voided match")
    # The same bit arrives as True, 1 or "1" depending on the driver, so
    # every spelling has to mean the same thing.
    voided = live_ids[1]
    for spelling in (True, 1, "1", "True"):
        snap_void = live_snapshot()
        info = snap_void.match_info.copy()
        info["IsVoid"] = info["IsVoid"].astype(object)
        info.loc[info["match_id"].astype(int) == int(voided), "IsVoid"] = spelling
        snap_void.match_info = info
        frame_void = pre.build(snap_void)
        check("IsVoid as {!r} drops the match".format(spelling),
              voided not in frame_void.contexts)

    # -- 3. a match nobody is pricing ---------------------------------------
    print("\na match that stopped pricing")
    quiet = live_ids[2]
    snap_quiet = live_snapshot()
    odds = snap_quiet.odds.copy()
    rows = odds["match_id"].astype(int) == int(quiet)
    odds.loc[rows, "effective_datetime"] = (
        AS_OF - timedelta(minutes=config.DEAD_ODDS_MINUTES + 20))
    snap_quiet.odds = odds
    frame_quiet = pre.build(snap_quiet)
    check("the silent match is off the board", quiet not in frame_quiet.contexts)

    # -- 4. a match with no active pool --------------------------------------
    print("\na match whose pools all closed")
    closed = live_ids[3]
    snap_closed = live_snapshot()
    pools = snap_closed.active_pools
    snap_closed.active_pools = pools[
        pools["match_id"].astype(int) != int(closed)].reset_index(drop=True)
    frame_closed = pre.build(snap_closed)
    check("the closed match is off the board", closed not in frame_closed.contexts)

    # -- 5. the filter cannot empty the board --------------------------------
    # A clock that disagrees with the feed ages every price at once. That is
    # a machine problem, not a card problem, and blanking the desk over it
    # would be the worst possible response.
    print("\nevery price looks stale at once")
    snap_stale = live_snapshot()
    odds = snap_stale.odds.copy()
    odds["effective_datetime"] = (
        AS_OF - timedelta(minutes=config.DEAD_ODDS_MINUTES + 120))
    snap_stale.odds = odds
    frame_stale = pre.build(snap_stale)
    check("the board is not emptied", len(frame_stale.contexts) == len(live_ids),
          "{} of {} kept".format(len(frame_stale.contexts), len(live_ids)))
    check("and it says why",
          any("staleness rule was ignored" in w for w in frame_stale.warnings),
          "; ".join(frame_stale.warnings))

    # -- 6. a finished match is still reachable when asked for ---------------
    print("\nALGOE_SHOW_FINISHED")
    original = config.SHOW_FINISHED
    try:
        config.SHOW_FINISHED = True
        snap_show = live_snapshot()
        snap_show.match_info = finish(snap_show, ended)
        frame_show = pre.build(snap_show)
        check("the finished match comes back", ended in frame_show.contexts)
        check("and is marked full time",
              frame_show.contexts[ended].state.phase == "full_time",
              frame_show.contexts[ended].state.phase)
    finally:
        config.SHOW_FINISHED = original

    print()
    if FAILURES:
        print("FAILED - {} check(s):".format(len(FAILURES)))
        for f in FAILURES:
            print("  - {}".format(f))
        return 1
    print("OK - every check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
