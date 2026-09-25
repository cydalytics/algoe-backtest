"""Solve a simulated tick with every scipy warning turned into noise we see."""

import sys
import time
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.optimizer import optimizer as opt          # noqa: E402
from src.pipeline import Pipeline                   # noqa: E402

warnings.simplefilter("always")

pipe = Pipeline(source=sys.argv[1] if len(sys.argv) > 1 else "sim")
tick = pipe.run(optimize=False)
print("priced {} matches / {} selections".format(
    len(tick.contexts), len(tick.selections)))

caught = []
with warnings.catch_warnings(record=True) as seen:
    warnings.simplefilter("always")
    started = time.time()
    results = opt.optimize_tick(
        tick, on_result=lambda m, r: pipe.publish(tick, m, r))
    elapsed = time.time() - started
    caught = list(seen)

print("solved {} matches in {:.1f}s ({:.2f}s each)".format(
    len(results), elapsed, elapsed / max(len(results), 1)))

kinds = {}
for w in caught:
    kinds[str(w.message)[:70]] = kinds.get(str(w.message)[:70], 0) + 1
print("warnings:", kinds or "none")

failed = [
    (m, name, b.message)
    for m, r in results.items()
    for name, b in r.blocks.items() if not b.success
]
print("failed blocks: {} of {}".format(
    len(failed), sum(len(r.blocks) for r in results.values())))
for row in failed[:8]:
    print("   ", row)

print("\nmatch          turnover      gm now     gm star     uplift   bps  secs")
for m, r in sorted(results.items(), key=lambda kv: -kv[1].uplift):
    print("{:<12} {:>10.0f} {:>11.0f} {:>11.0f} {:>10.1f} {:>5.0f} {:>5.2f}".format(
        m, r.turnover, r.gm_now, r.gm_star, r.uplift, r.uplift_bps, r.seconds))

print("\ntotals:", {k: v for k, v in tick.totals.items()
                    if k in ("gm_now", "gm_star", "uplift", "uplift_bps")})
