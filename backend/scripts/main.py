"""
Run one tick from the command line.

    python scripts/main.py                    one tick from the configured source
    python scripts/main.py --source sim       force the simulator
    python scripts/main.py --loop 5           keep ticking every 5 minutes
    python scripts/main.py --no-store         do not write to SQLite

Prints the board the API would serve, so the pipeline can be checked
without a browser.

Change Log:
-----------
2026-08-18      Initialize
2026-08-30      Rewritten for the offline pipeline
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd                                     # noqa: E402

from src.core import config                             # noqa: E402
from src.core.logging import get_logger                 # noqa: E402
from src.database.sqlite_store import get_store         # noqa: E402
from src.pipeline import Pipeline                       # noqa: E402

logger = get_logger(__name__)

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 40)


def show(tick):
    print("\n{}  source={}  as of {}".format(
        tick.run_id, tick.source, tick.as_of))
    print("  timings: " + "  ".join(
        "{}={:.2f}s".format(k, v) for k, v in tick.timings.items()))

    totals = tick.totals
    print("  book   : {} matches, {} selections, {} legs optimised".format(
        totals.get("matches"), totals.get("selections"),
        totals.get("optimized_legs", 0)))
    print("  E[GM]  : {:,.0f} -> {:,.0f}  ({:+,.0f}, {:+.0f} bps) on {:,.0f} "
          "turnover".format(
              totals.get("opt_gm_now", 0), totals.get("gm_star", 0),
              totals.get("uplift", 0), totals.get("uplift_bps", 0),
              totals.get("opt_turnover", 0)))

    if tick.warnings:
        print("  warnings:")
        for w in tick.warnings:
            print("    - {}".format(w))

    rows = []
    for match_id, ctx in tick.contexts.items():
        result = tick.result(match_id)
        rows.append({
            "match": "{} v {}".format(ctx.home[:16], ctx.away[:16]),
            "phase": ctx.state.phase,
            "min": ctx.state.minute,
            "score": "{}-{}".format(ctx.state.home_score, ctx.state.away_score),
            "legs": result.legs if result else 0,
            "turnover": round(result.turnover) if result else 0,
            "gm_now": round(result.gm_now) if result else 0,
            "gm_star": round(result.gm_star) if result else 0,
            "uplift": round(result.uplift) if result else 0,
            "bps": round(result.uplift_bps, 1) if result else 0,
        })
    frame = pd.DataFrame(rows).sort_values("uplift", ascending=False)
    print("\n" + frame.to_string(index=False))


def main():
    parser = argparse.ArgumentParser(description="Run the Algo E pipeline.")
    parser.add_argument("--source", default=None,
                        help="sql | capture | sim (default: {})".format(
                            config.DATA_SOURCE))
    parser.add_argument("--loop", type=float, default=0,
                        help="keep running, this many minutes apart")
    parser.add_argument("--no-store", action="store_true",
                        help="do not write the tick to SQLite")
    parser.add_argument("--no-legs", action="store_true",
                        help="store the tick header but not the full book")
    parser.add_argument("--no-optimize", action="store_true",
                        help="price the book but skip the solver")
    args = parser.parse_args()

    pipeline = Pipeline(source=args.source, optimize=not args.no_optimize)
    store = None if args.no_store else get_store()

    while True:
        tick = pipeline.run()
        show(tick)
        if store is not None:
            store.save(tick, with_legs=not args.no_legs)
            print("\nstored in {}".format(store.path))
        if not args.loop:
            return
        print("\nsleeping {:.1f} min...".format(args.loop))
        time.sleep(args.loop * 60)


if __name__ == "__main__":
    main()
