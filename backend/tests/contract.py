"""
API Contract Check

The frontend's types in src/types/api.ts are hand-written, so nothing in the
build catches the day a payload key is renamed here and not there. This
script is that catch: it calls every endpoint the UI calls and asserts the
exact field names the UI reads.

It is deliberately not a schema validator. It checks the things that break
silently in a browser - a missing key reads as undefined and renders as an
empty cell rather than an error, which is the worst possible failure on a
trading screen.

Run it against a live server:

    python -m uvicorn src.api.main:app --port 8001
    python tests/contract.py                     # or --base http://host:port

Exit code is 0 only when every endpoint matches.

Change Log:
-----------
2026-08-30      Initialize
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8001"

# ---------------------------------------------------------------------------
# What the frontend reads. Mirrors src/types/api.ts.
# ---------------------------------------------------------------------------

RULES = {"true_prob", "turnover", "theta_semantics", "theta_layer",
         "bucket_minutes", "optimizer", "optimized_pools"}

BOARD = {"run_id", "as_of", "source", "totals", "warnings", "timings",
         "rules", "progress", "matches"}

PROGRESS = {"solved", "total", "solving"}

BOARD_MATCH = {
    "match_id", "home", "away", "league", "league_code", "kickoff", "phase",
    "game_state", "minute", "in_play", "score", "ht_score", "corners",
    "theta_age_min", "theta_now", "theta_star", "pools", "selections", "open",
    "turnover_5m", "tickets_5m", "turnover", "gm_now", "gm_star", "uplift",
    "uplift_bps", "solved", "pending", "moves", "alerts",
}

COCKPIT = {"run_id", "as_of", "source", "pending", "solving", "rules",
           "match", "theta", "optimizer", "book", "events", "quotes",
           "history"}

MATCH_HEADER = {
    "match_id", "home", "away", "league", "league_code", "kickoff",
    "frontend_id", "phase", "game_state", "minute", "in_play", "ht_done",
    "score", "ht_score", "corners", "ht_corners",
}

THETA_BLOCK = {"layers", "layer", "age_min", "splits", "semantics", "dims"}
DIM_SPEC = {"dim", "label", "bounds", "max_move", "domain"}

OPTIMIZER = {
    "theta_now", "theta_star", "turnover", "payout_now", "payout_star",
    "gm_now", "gm_star", "uplift", "uplift_bps", "legs", "skipped", "seconds",
    "moves", "repaired", "blocks", "per_pool", "curves",
}
REPAIR = {"dim", "label", "from", "to"}
BLOCK = {"block", "dims", "legs", "starts", "success", "message", "seconds",
         "gm_now", "gm_star", "uplift"}
MOVE = {"dim", "label", "now", "star", "delta", "at_limit"}

BOOK = {"families", "totals"}
FAMILY = {"family", "turnover", "pools"}
POOL = {"pool_code", "pool_name", "domain", "seg", "kind", "optimized",
        "margin", "turnover", "open", "lines"}
LINE = {"line_id", "line_label", "line_value", "is_main_line", "book_sum",
        "turnover", "selections"}

SELECTION = {
    "key", "pool_id", "pool_code", "pool_name", "family", "seg", "domain",
    "kind", "optimized", "line_id", "line_label", "line_value", "comb_id",
    "selection", "sel_label", "is_main_line",
    "hkjc_odds", "hkjc_odds_prev", "hkjc_true_odds", "odds_age_sec",
    "mkt_min", "mkt_avg", "mkt_max", "mkt_n", "mkt_age_sec",
    "true_prob", "true_prob_src", "book_sum", "model_prob", "fair_odds",
    "sell_odds", "status", "edge_hkjc", "edge_mkt", "exp_gm_unit",
    "t5m", "tickets5m", "avg_stake", "t_hat", "t_hat_src", "t_share",
    "t_trend", "invested", "tickets_today",
    "model_prob_star", "fair_odds_star", "sell_odds_star", "status_star",
    "exp_gm_unit_star", "odds_delta",
}

EVENT = {"at", "type", "side", "detail", "provider_id"}
QUOTE = {"key", "label", "bookmaker", "odds", "age_sec"}
HISTORY_POINT = {"at", "odds", "true_odds"}

WHATIF = {"match_id", "theta", "expected", "baseline", "selections"}
WHATIF_SEL = {"key", "pool_code", "line_label", "sel_label", "hkjc_odds",
              "sell_odds", "sell_odds_what", "model_prob_what", "status_what",
              "exp_gm_unit_what", "t_hat"}
EXPECTED_GM = {"turnover", "payout", "gm", "margin_pct", "legs"}

HEALTH = {"status", "source", "tick_age_seconds", "stale", "solving",
          "solved", "matches", "as_of", "last_error"}
CURVE = {"x", "gm", "now", "star"}

DIMS = ("goal_tg", "goal_sup", "goal_fh_tg", "goal_fh_sup",
        "corner_tg", "corner_sup", "corner_fh_tg", "corner_fh_sup")

STATUSES = {"open", "settled", "closed", "suspended"}
PHASES = {"prematch", "first_half", "half_time", "second_half",
          "et_first_half", "et_half_time", "et_second_half",
          "penalty_shootout", "full_time"}


# ---------------------------------------------------------------------------

class Check:
    """Collects failures instead of stopping at the first one - one run
    should tell you everything that drifted, not just the earliest field."""

    def __init__(self):
        self.failures = []
        self.checked = 0

    def keys(self, where: str, obj, expected: set):
        self.checked += 1
        if not isinstance(obj, dict):
            self.failures.append("{}: expected an object, got {}".format(
                where, type(obj).__name__))
            return
        missing = expected - set(obj)
        if missing:
            self.failures.append("{}: missing {}".format(
                where, ", ".join(sorted(missing))))

    def one_of(self, where: str, value, allowed: set):
        self.checked += 1
        if value not in allowed:
            self.failures.append("{}: {!r} is not one of {}".format(
                where, value, ", ".join(sorted(allowed))))

    def theta(self, where: str, obj):
        self.keys(where, obj, set(DIMS))

    def truthy(self, where: str, ok: bool, note: str):
        self.checked += 1
        if not ok:
            self.failures.append("{}: {}".format(where, note))


def get(base: str, path: str, body=None):
    url = base + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=300) as res:
            return json.loads(res.read())
    except urllib.error.HTTPError as exc:
        raise SystemExit("{} -> HTTP {}: {}".format(
            path, exc.code, exc.read().decode()[:400]))
    except urllib.error.URLError as exc:
        raise SystemExit("cannot reach {} ({}). Is the API running?".format(
            url, exc.reason))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default=BASE)
    args = ap.parse_args()
    base = args.base.rstrip("/")
    c = Check()

    print("contract check against {}".format(base))

    # -- health -------------------------------------------------------------
    health = get(base, "/api/health")
    c.keys("health", health, HEALTH)
    c.one_of("health.status", health.get("status"), {"ok", "degraded"})

    # -- board --------------------------------------------------------------
    # The board answers before the card is solved, so wait for the
    # recommendations before checking their shape - otherwise the optimizer
    # block is simply absent and the check passes for the wrong reason.
    board = get(base, "/api/desk")
    c.keys("board", board, BOARD)
    c.keys("board.progress", board.get("progress"), PROGRESS)
    for _ in range(600):
        progress = board.get("progress") or {}
        if not progress.get("solving"):
            break
        print("  waiting for the solver: {}/{}".format(
            progress.get("solved"), progress.get("total")))
        time.sleep(1.0)
        board = get(base, "/api/desk")

    c.keys("board.rules", board.get("rules"), RULES)
    matches = board.get("matches") or []
    c.truthy("board.matches", bool(matches), "the board has no matches")
    c.truthy("board.progress", not (board["progress"]["solving"]),
             "the solver never finished")

    for m in matches:
        where = "board.matches[{}]".format(m.get("match_id"))
        c.keys(where, m, BOARD_MATCH)
        c.one_of(where + ".phase", m.get("phase"), PHASES)
        c.theta(where + ".theta_now", m.get("theta_now"))
        c.truthy(where + ".score", isinstance(m.get("score"), list)
                 and len(m["score"]) == 2, "score must be a pair")
        for mv in m.get("moves") or []:
            c.keys(where + ".moves[]", mv, MOVE)
        for al in m.get("alerts") or []:
            c.keys(where + ".alerts[]", al, {"level", "text"})
            c.one_of(where + ".alerts[].level", al.get("level"),
                     {"good", "warn", "bad"})

    print("  board: {} matches".format(len(matches)))

    # -- cockpit, on every match so one odd fixture cannot hide -------------
    n_sel = 0
    for m in matches:
        mid = m["match_id"]
        ck = get(base, "/api/desk/{}".format(mid))
        where = "cockpit[{}]".format(mid)
        c.keys(where, ck, COCKPIT)
        c.keys(where + ".match", ck.get("match"), MATCH_HEADER)
        c.keys(where + ".rules", ck.get("rules"), RULES)

        theta = ck.get("theta") or {}
        c.keys(where + ".theta", theta, THETA_BLOCK)
        for layer, values in (theta.get("layers") or {}).items():
            c.theta(where + ".theta.layers." + layer, values)
        dims = theta.get("dims") or []
        c.truthy(where + ".theta.dims", len(dims) == 8,
                 "expected 8 dimensions, got {}".format(len(dims)))
        for d in dims:
            c.keys(where + ".theta.dims[]", d, DIM_SPEC)
            c.one_of(where + ".theta.dims[].dim", d.get("dim"), set(DIMS))
            c.truthy(where + ".theta.dims[].bounds",
                     isinstance(d.get("bounds"), list) and len(d["bounds"]) == 2,
                     "bounds must be a pair")

        opt = ck.get("optimizer")
        if opt is not None:
            c.keys(where + ".optimizer", opt, OPTIMIZER)
            c.theta(where + ".optimizer.theta_now", opt.get("theta_now"))
            c.theta(where + ".optimizer.theta_star", opt.get("theta_star"))
            for r in opt.get("repaired") or []:
                c.keys(where + ".optimizer.repaired[]", r, REPAIR)
                c.one_of(where + ".optimizer.repaired[].dim", r.get("dim"),
                         set(DIMS))
            for b in opt.get("blocks") or []:
                c.keys(where + ".optimizer.blocks[]", b, BLOCK)
            for p in opt.get("per_pool") or []:
                c.keys(where + ".optimizer.per_pool[]", p,
                       {"pool_code", "gm_now", "gm_star", "uplift"})
            for dim, curve in (opt.get("curves") or {}).items():
                c.one_of(where + ".optimizer.curves key", dim, set(DIMS))
                c.keys(where + ".optimizer.curves." + dim, curve, CURVE)

        book = ck.get("book") or {}
        c.keys(where + ".book", book, BOOK)
        c.keys(where + ".book.totals", book.get("totals"),
               {"selections", "open", "turnover"})
        for fam in book.get("families") or []:
            c.keys(where + ".book.families[]", fam, FAMILY)
            for pool in fam.get("pools") or []:
                c.keys(where + ".book.pools[]", pool, POOL)
                for line in pool.get("lines") or []:
                    c.keys(where + ".book.lines[]", line, LINE)
                    for sel in line.get("selections") or []:
                        n_sel += 1
                        c.keys(where + ".book.selections[]", sel, SELECTION)
                        c.one_of(where + ".selection.status",
                                 sel.get("status"), STATUSES)
                        if sel.get("status_star") is not None:
                            c.one_of(where + ".selection.status_star",
                                     sel.get("status_star"), STATUSES)

        for ev in ck.get("events") or []:
            c.keys(where + ".events[]", ev, EVENT)
        quotes = ck.get("quotes") or {}
        c.keys(where + ".quotes", quotes, {"books", "rows"})
        for q in quotes.get("rows") or []:
            c.keys(where + ".quotes.rows[]", q, QUOTE)
        for key, points in (ck.get("history") or {}).items():
            for pt in points:
                c.keys(where + ".history[]", pt, HISTORY_POINT)

    print("  cockpit: {} matches, {} selections".format(len(matches), n_sel))

    # -- curves and what-if, on the first match only (both are expensive) ---
    if matches:
        mid = matches[0]["match_id"]

        curves = get(base, "/api/desk/{}/curves".format(mid))
        c.keys("curves", curves, {"match_id", "turnover", "curves"})
        for dim, curve in (curves.get("curves") or {}).items():
            c.one_of("curves key", dim, set(DIMS))
            c.keys("curves." + dim, curve, CURVE)
            c.truthy("curves." + dim, len(curve.get("x") or []) ==
                     len(curve.get("gm") or []), "x and gm must be the same length")
        print("  curves: {} dimensions".format(len(curves.get("curves") or {})))

        what = get(base, "/api/price",
                   {"match_id": mid, "theta": {"goal_tg": 2.75}})
        c.keys("price", what, WHATIF)
        c.theta("price.theta", what.get("theta"))
        c.keys("price.expected", what.get("expected"), EXPECTED_GM)
        c.keys("price.baseline", what.get("baseline"), EXPECTED_GM)
        for sel in what.get("selections") or []:
            c.keys("price.selections[]", sel, WHATIF_SEL)
        c.truthy("price.theta.goal_tg", what["theta"]["goal_tg"] == 2.75,
                 "the posted dimension was not applied")
        print("  what-if: {} selections re-priced".format(
            len(what.get("selections") or [])))

    # -- report -------------------------------------------------------------
    print()
    if c.failures:
        print("FAILED - {} problem(s) across {} checks".format(
            len(c.failures), c.checked))
        for f in dict.fromkeys(c.failures):   # de-duplicate, keep order
            print("  - {}".format(f))
        return 1
    print("OK - {} checks passed".format(c.checked))
    return 0


if __name__ == "__main__":
    sys.exit(main())
