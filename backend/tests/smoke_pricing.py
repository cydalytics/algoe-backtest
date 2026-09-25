"""Quick manual check of the pricing engine (not part of unittest)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pricing import pools                      # noqa: E402
from src.pricing.book import (                     # noqa: E402
    BookModel, MatchState, fair_odds, sell_odds,
)

TH = dict(
    goal_tg=2.6, goal_sup=0.4, goal_fh_tg=1.17, goal_fh_sup=0.18,
    corner_tg=10.2, corner_sup=1.2, corner_fh_tg=4.6, corner_fh_sup=0.54,
)

CASES = [
    ("HAD", None), ("FHAD", None), ("HHAD", -1.0),
    ("HDC", -0.5), ("HDC", -0.75), ("FHDC", -0.25),
    ("HILO", 2.5), ("HILO", 2.75), ("FHLO", 1.5),
    ("OOE", None), ("TTG", None), ("CRS", None), ("FCRS", None), ("HFT", None),
    ("CHLO", 10.5), ("CHDC", -1.5),
]


def dump(model, title):
    print("\n=== {} ===".format(title))
    for code, line in CASES:
        pool = pools.resolve(code)
        market = model.market(pool, line)
        if not market:
            print("{:6} {:>6}  (empty)".format(code, str(line)))
            continue
        total = sum(market.values())
        head = {k: round(v, 4) for k, v in list(market.items())[:4]}
        prices = {
            k: sell_odds(fair_odds(v), pool.margin)
            for k, v in list(market.items())[:4]
        }
        print("{:6} {:>6}  sum={:.4f}  p={}  sell={}".format(
            code, str(line), total, head, prices))


def main():
    dump(BookModel(TH, MatchState(phase="prematch")), "pre-match")

    live = dict(TH)
    live.update(
        goal_tg=1.10, goal_sup=0.10, goal_fh_tg=0.0, goal_fh_sup=0.0,
        corner_tg=4.5, corner_sup=0.4, corner_fh_tg=0.0, corner_fh_sup=0.0,
    )
    state = MatchState(
        home_score=1, away_score=0, ht_home=1, ht_away=0,
        home_corner=5, away_corner=3, minute=60, phase="second_half",
    )
    dump(BookModel(live, state), "in-play 1-0, 60', remaining theta")

    first = dict(TH)
    first.update(goal_tg=2.2, goal_fh_tg=0.55, corner_tg=8.5, corner_fh_tg=1.9)
    dump(
        BookModel(first, MatchState(
            home_score=0, away_score=1, home_corner=2, away_corner=1,
            minute=32, phase="first_half")),
        "in-play 0-1, 32', first half",
    )

    print("\nladder spot checks:")
    for raw in (1.234, 3.999, 4.02, 9.94, 12.7, 44.4, 250.0, 5000.0):
        print("  {:>8} -> {}".format(raw, sell_odds(raw, 0.0)))


if __name__ == "__main__":
    main()
