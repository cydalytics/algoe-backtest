"""Check the board answers before the solve finishes, then fills in."""

import json
import sys
import time
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8011"


def get(path, timeout=300):
    started = time.time()
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read()), time.time() - started


board, first = get("/api/desk?refresh=true")
print("first board in {:.2f}s - {} matches, progress {}".format(
    first, len(board["matches"]), board["progress"]))

for _ in range(40):
    time.sleep(0.5)
    board, took = get("/api/desk")
    p = board["progress"]
    print("  +{:>5.1f}s  solved {}/{}  solving={}  uplift={}".format(
        time.time(), p["solved"], p["total"], p["solving"],
        board["totals"].get("uplift")))
    if not p["solving"] and p["solved"] >= p["total"]:
        break

match_id = board["matches"][0]["match_id"]
cock, took = get("/api/desk/{}".format(match_id))
print("\ncockpit {} in {:.2f}s: pending={} solving={}".format(
    match_id, took, cock["pending"], cock["solving"]))
print("  optimizer:", None if not cock["optimizer"] else {
    k: cock["optimizer"][k] for k in ("turnover", "gm_now", "gm_star", "uplift")})
print("  book lines:", len(cock["book"]))

curves, took = get("/api/desk/{}/curves".format(match_id))
print("  curves in {:.2f}s: {} dims, turnover {}".format(
    took, len(curves["curves"]), curves["turnover"]))

health, _ = get("/api/health")
print("\nhealth:", health)
