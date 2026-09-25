"""
Capture

Records one raw snapshot from the live SQL Servers to parquet, so it can be
replayed later with --source capture.

This is the only way real data leaves the database for debugging. A capture
is exactly what the notebook's queries returned at that moment - no
preprocessing, no pricing, no models - so replaying it reproduces the tick
byte for byte, including whatever was odd about it.

    python scripts/capture.py                  one snapshot, now
    python scripts/capture.py --every 5 --for 60   every 5 min for an hour
    python scripts/capture.py --list           what is already on disk

Captures land in ALGOE_CAPTURE_DIR (default: captures/ beside the project),
one folder per timestamp. A full card is a few megabytes, so a session's
worth is easy to copy off.

Change Log:
-----------
2026-08-30      Initialize
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core import config                                  # noqa: E402
from src.core.logging import get_logger                      # noqa: E402
from src.hkjc.capture_source import capture_dirs, write_capture   # noqa: E402
from src.hkjc.source import get_source                       # noqa: E402

log = get_logger(__name__)


def show_existing():
    dirs = capture_dirs()
    if not dirs:
        print("no captures in {}".format(config.CAPTURE_DIR))
        return
    print("{} capture(s) in {}\n".format(len(dirs), config.CAPTURE_DIR))
    for path in dirs:
        size = sum(f.stat().st_size for f in path.glob("*")) / 1e6
        print("  {:<20} {:>8.1f} MB".format(path.name, size))


def capture_once(source_name: str) -> bool:
    """One snapshot. Returns False if it failed, so a loop can keep going."""
    started = time.time()
    try:
        source = get_source(source_name)
        snapshot = source.fetch(as_of=datetime.now())
    except Exception as exc:                                  # noqa: BLE001
        print("  FAILED  {}: {}".format(type(exc).__name__, exc))
        return False

    target = write_capture(snapshot)
    rows = snapshot.summary()
    total = sum(rows.values())
    print("  {}  {:>7} rows in {:.1f}s".format(
        target.name, total, time.time() - started))
    for name, count in sorted(rows.items()):
        print("      {:<16} {:>7}".format(name, count))
    if total == 0:
        print("      (empty - no live matches right now?)")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Record raw HKJC snapshots.")
    ap.add_argument("--source", default="sql",
                    help="normally sql; sim is useful for testing the loop")
    ap.add_argument("--every", type=float, default=0,
                    help="minutes between captures (default: capture once)")
    ap.add_argument("--for", dest="duration", type=float, default=0,
                    help="total minutes to keep capturing")
    ap.add_argument("--list", action="store_true",
                    help="list captures already on disk and exit")
    args = ap.parse_args()

    if args.list:
        show_existing()
        return 0

    print("capturing from {} into {}\n".format(args.source, config.CAPTURE_DIR))

    if not args.every:
        return 0 if capture_once(args.source) else 1

    deadline = time.time() + (args.duration or args.every) * 60
    taken = failed = 0
    try:
        while time.time() < deadline:
            if capture_once(args.source):
                taken += 1
            else:
                failed += 1
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            time.sleep(min(args.every * 60, remaining))
    except KeyboardInterrupt:
        print("\nstopped")

    print("\n{} captured, {} failed".format(taken, failed))
    return 0 if taken else 1


if __name__ == "__main__":
    sys.exit(main())
