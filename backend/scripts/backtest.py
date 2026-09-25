"""
Backtest CLI

Replay a coherent meeting and score each lever on its own lab:
turnover, calibrated true-prob, optimizer, policy / demand.

    python scripts/backtest.py --quick
    python scripts/backtest.py --matches 6 --ticks 10 --optimize star
    python scripts/backtest.py --turnover persistence,gametime,oracle \\
        --true-prob hkjc_true,model --calibrators raw,shrink,temperature \\
        --algos hold,slsqp,coordinate --elasticities 0,1

Change Log:
-----------
2026-09-11      Initialize (W06 backtest)
2026-09-11      Labs CLI
"""

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from src.backtest.predictors import (
    default_algos, default_calibrators, default_elasticities,
    default_policies, default_true_prob, default_turnover,
    parse_floats, parse_list,
)
from src.backtest.runner import print_report, run_backtest
from src.core.logging import setup_logging


def parse_args():
    parser = argparse.ArgumentParser(
        description="Algo E backtest: independent labs for each lever.")
    parser.add_argument("--quick", action="store_true",
                        help="3 matches, 6 ticks, hold-only optimizer lab")
    parser.add_argument("--matches", type=int, default=None)
    parser.add_argument("--ticks", type=int, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--start", default=None,
                        help="ISO meeting start (default: config.BACKTEST_MEETING_START)")
    parser.add_argument("--turnover", default=None,
                        help="comma-separated turnover predictors")
    parser.add_argument("--true-prob", default=None,
                        help="comma-separated true-odds predictors")
    parser.add_argument("--calibrators", default=None,
                        help="comma-separated calibrators")
    parser.add_argument("--algos", default=None,
                        help="comma-separated optimizer algorithms")
    parser.add_argument("--policies", default=None,
                        help="comma-separated update policies")
    parser.add_argument("--elasticities", default=None,
                        help="comma-separated demand elasticities")
    parser.add_argument("--optimize", default=None,
                        choices=("star", "all", "none"),
                        help="shorthand for --algos (none=hold, star=hold+slsqp, all=catalog)")
    parser.add_argument("--no-store", action="store_true")
    parser.add_argument("--json", default=None,
                        help="write the full report JSON to this path")
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def main():
    args = parse_args()
    setup_logging(log_level=args.log_level)

    n_matches = 3 if args.quick and args.matches is None else args.matches
    n_ticks = 6 if args.quick and args.ticks is None else args.ticks
    optimize = args.optimize
    if optimize is None:
        optimize = "none" if args.quick else "star"

    turnover = parse_list(args.turnover, default_turnover())
    true_prob = parse_list(args.true_prob, default_true_prob())
    calibrators = parse_list(args.calibrators, default_calibrators())
    algos = parse_list(args.algos, None) if args.algos else None
    policies = parse_list(args.policies, default_policies())
    elasticities = parse_floats(args.elasticities, default_elasticities())

    if args.quick and args.turnover is None:
        turnover = ["persistence", "trailing_mean", "gametime", "momentum", "oracle"]
    if args.quick and args.true_prob is None:
        true_prob = ["hkjc_true", "market", "model"]
    if args.quick and args.calibrators is None:
        calibrators = ["raw", "shrink", "temperature"]
    if args.quick and args.policies is None:
        policies = ["always", "threshold"]
    if args.quick and args.elasticities is None:
        elasticities = [0.0, 1.0]

    start = None
    if args.start:
        from datetime import datetime
        start = datetime.fromisoformat(args.start)

    report = run_backtest(
        seed=args.seed,
        n_matches=n_matches,
        n_ticks=n_ticks,
        start=start,
        turnover=turnover,
        true_prob=true_prob,
        calibrators=calibrators,
        algos=algos,
        policies=policies,
        elasticities=elasticities,
        optimize=optimize,
        persist=not args.no_store,
    )
    print_report(report)
    if args.json:
        path = Path(args.json)
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("\nWrote {}".format(path))


if __name__ == "__main__":
    main()
