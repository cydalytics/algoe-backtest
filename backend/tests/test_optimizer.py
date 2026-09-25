"""
Optimizer Regression Checks

The simulator only ever produces well-behaved parameters, so the desk ran
clean on `sim` and fell apart on `sql`. Live algo_param does not promise
any of the things the solver quietly assumed:

    a parameter can sit outside the configured absolute bounds
    supremacy can exceed the total it is supposed to be a difference of
    a first half can be carrying more than the whole match
    our current offer can already be through the external market's price

Each of those produced either scipy's "values in x were outside bounds"
warning or an immediate "positive directional derivative for linesearch",
and both end in a match with no recommendation. This file feeds the
solver exactly those inputs and asserts it comes back with a usable
answer and a silent scipy.

Run it directly - no pytest needed offline:

    python tests/test_optimizer.py

Change Log:
-----------
2026-08-30      Initialize
"""

import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import config                        # noqa: E402
from src.optimizer import optimizer as opt         # noqa: E402
from src.pricing import book as book_lib           # noqa: E402
from src.pricing import pools as pool_defs         # noqa: E402

FAILURES = []


def check(name, ok, detail=""):
    print("  {:<58} {}".format(name, "ok" if ok else "FAILED"))
    if not ok:
        FAILURES.append("{}{}".format(name, ": " + detail if detail else ""))


def legs_for(state, main_cap=None):
    """A small but representative book: three-way, Asian, corners."""
    spec = [
        ("HAD", None, "H", 40000.0, 0.42),
        ("HAD", None, "D", 20000.0, 0.28),
        ("HAD", None, "A", 30000.0, 0.30),
        ("HDC", -0.75, "H", 80000.0, 0.52),
        ("HDC", -0.75, "A", 70000.0, 0.48),
        ("HILO", 2.5, "O", 60000.0, 0.51),
        ("HILO", 2.5, "U", 55000.0, 0.49),
        ("CHLO", 9.5, "O", 12000.0, 0.50),
        ("CHLO", 9.5, "U", 11000.0, 0.50),
    ]
    out = []
    for code, line, sel, turnover, prob in spec:
        pool = pool_defs.resolve(code)
        out.append(opt.Leg(
            key="{}-{}-{}".format(code, line, sel), pool=pool, line=line,
            selection=sel, turnover=turnover, true_prob=prob,
            margin=pool.margin, cap=main_cap, main=True,
        ))
    return out


def solve(theta, state, cap=None):
    """Solve once, capturing anything scipy wants to say about it."""
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        engine = opt.MatchOptimizer(1, legs_for(state, cap), state, theta)
        result = engine.solve()
    noise = [
        str(w.message) for w in seen
        if "outside bounds" in str(w.message)
        or "Singular matrix" in str(w.message)
    ]
    return engine, result, noise


BASE = {
    "goal_tg": 2.6, "goal_sup": 0.3, "goal_fh_tg": 1.1, "goal_fh_sup": 0.15,
    "corner_tg": 10.0, "corner_sup": 0.8, "corner_fh_tg": 4.4,
    "corner_fh_sup": 0.35,
}

PREMATCH = book_lib.MatchState(minute=0.0, phase="prematch")
SECOND_HALF = book_lib.MatchState(
    home_score=1, away_score=0, ht_home=1, ht_away=0,
    home_corner=4, away_corner=3, ht_home_corner=2, ht_away_corner=2,
    minute=62.0, phase="second_half",
)


def main():
    print("optimizer regression checks\n")

    # -- 1. the healthy case still works ------------------------------------
    print("a well-formed match")
    engine, result, noise = solve(BASE, PREMATCH)
    check("solves", bool(result.blocks))
    check("both blocks converge",
          all(b.success for b in result.blocks.values()),
          "; ".join(b.message for b in result.blocks.values() if not b.success))
    check("scipy is silent", not noise, "; ".join(noise))
    check("never recommends a worse book", result.gm_star >= result.gm_now - 1e-6,
          "{} < {}".format(result.gm_star, result.gm_now))

    # -- 2. theta outside the configured absolute bounds --------------------
    # This is what inverted the trust region: centring a +/- max_move band
    # on a value already past the hard limit gives lo > hi.
    print("\na parameter parked outside its absolute bounds")
    for dim, value in (("goal_tg", 12.0), ("corner_tg", 0.05),
                       ("goal_sup", -7.0)):
        theta = dict(BASE, **{dim: value})
        if dim == "goal_tg":
            theta["goal_sup"] = 0.0
        engine, result, noise = solve(theta, PREMATCH)
        lo, hi = engine._bounds((dim,))[0]
        check("{} = {}: bounds are not inverted".format(dim, value), lo <= hi,
              "{} > {}".format(lo, hi))
        check("{} = {}: scipy is silent".format(dim, value), not noise,
              "; ".join(noise))
        check("{} = {}: still returns a theta".format(dim, value),
              all(v == v for v in result.theta_star.values()))

    # -- 3. incoherent parameters -------------------------------------------
    # Supremacy past the total, and a first half bigger than the match.
    print("\nincoherent parameters from the feed")
    for name, theta in (
        ("supremacy exceeds the total", dict(BASE, goal_tg=1.2, goal_sup=2.8)),
        ("first half exceeds the match", dict(BASE, goal_tg=2.0, goal_fh_tg=3.4)),
        ("corner supremacy inverted", dict(BASE, corner_tg=6.0, corner_sup=-9.0)),
    ):
        engine, result, noise = solve(theta, PREMATCH)
        base = engine.theta_base
        check("{}: repaired start is coherent".format(name),
              base["goal_tg"] >= abs(base["goal_sup"]) - 1e-9
              and base["goal_tg"] >= base["goal_fh_tg"] - 1e-9
              and base["corner_tg"] >= abs(base["corner_sup"]) - 1e-9)
        check("{}: the repair is reported".format(name), bool(engine.repaired))
        check("{}: blocks converge".format(name),
              all(b.success for b in result.blocks.values()),
              "; ".join(b.message for b in result.blocks.values() if not b.success))
        check("{}: scipy is silent".format(name), not noise, "; ".join(noise))

    # -- 4. a settled first half --------------------------------------------
    # The frozen dimensions used to be handed to SLSQP as lo == hi, and its
    # finite-difference probe steps straight outside that.
    print("\na match past half time")
    engine, result, noise = solve(BASE, SECOND_HALF)
    check("scipy is silent", not noise, "; ".join(noise))
    check("first-half parameters are frozen",
          engine.frozen == {"goal_fh_tg", "goal_fh_sup",
                            "corner_fh_tg", "corner_fh_sup"},
          str(sorted(engine.frozen)))
    for dim in engine.frozen:
        check("{} is not moved".format(dim),
              abs(result.theta_star[dim] - result.theta_now[dim]) < 1e-9,
              "{} -> {}".format(result.theta_now[dim], result.theta_star[dim]))
    check("no curve is drawn for a settled dimension",
          not (set(engine.curves(result.theta_star)) & engine.frozen))

    # -- 5. an offer already through the market ------------------------------
    # A cap below our current price makes the starting point infeasible,
    # which is the other half of "positive directional derivative".
    print("\nour price is already through the market cap")
    engine, result, noise = solve(BASE, PREMATCH, cap=1.05)
    check("blocks still converge",
          all(b.success for b in result.blocks.values()),
          "; ".join(b.message for b in result.blocks.values() if not b.success))
    check("scipy is silent", not noise, "; ".join(noise))

    # -- 6. the trust region is respected ------------------------------------
    print("\nthe safety band")
    engine, result, noise = solve(BASE, PREMATCH)
    for dim, value in result.theta_star.items():
        move = abs(value - result.theta_now[dim])
        check("{} moves no further than {}".format(dim, config.THETA_MAX_MOVE[dim]),
              move <= config.THETA_MAX_MOVE[dim] + 1e-6, "moved {:.4f}".format(move))
        lo, hi = config.THETA_BOUNDS[dim]
        check("{} stays inside its absolute bounds".format(dim),
              lo - 1e-6 <= value <= hi + 1e-6, "{} not in [{}, {}]".format(value, lo, hi))

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
