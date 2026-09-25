"""Manual end-to-end check of one tick (not part of unittest)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd                                    # noqa: E402

from src.features.preprocessor import Preprocessor     # noqa: E402
from src.hkjc.source import get_source                 # noqa: E402

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 60)


def main():
    snap = get_source("sim").fetch()
    tick = Preprocessor().build(snap)

    print("warnings:")
    for w in tick.warnings:
        print("  -", w)

    sel = tick.selections
    print("\nselections:", sel.shape)
    print(sel.groupby("pool_code").size().sort_values(ascending=False).to_dict())

    cols = ["match_id", "pool_code", "line_label", "selection", "sel_label",
            "hkjc_odds", "hkjc_true_odds", "mkt_min", "mkt_max", "mkt_n",
            "t5m", "tickets5m", "avg_stake", "is_main_line"]
    print("\n" + sel[cols].head(16).to_string())

    print("\nmain-line count per pool (should be one line per match/pool):")
    mains = sel[sel["is_main_line"]].groupby(["pool_code"])["line_id"].nunique()
    print(mains.to_dict())

    print("\ncontexts:")
    for mid, ctx in tick.contexts.items():
        s = ctx.state
        print("  {} {:<20} v {:<20} {:<11} min={:>5} phase={:<11} "
              "score={}-{} ht={}-{} cor={}-{} htdone={} age={}m".format(
                  mid, ctx.home[:20], ctx.away[:20], ctx.game_state,
                  s.minute, s.phase, s.home_score, s.away_score,
                  s.ht_home, s.ht_away, s.home_corner, s.away_corner,
                  s.ht_done, ctx.theta_age_min))
    first = next(iter(tick.contexts.values()))
    print("\n  theta layers for", first.match_id)
    for layer, dims in first.theta.items():
        print("   ", layer, {k: round(v, 3) for k, v in dims.items()})

    print("\nquotes:", tick.quotes.shape,
          "| books:", sorted(tick.quotes["bookmaker_str"].unique().tolist())[:8])
    print("events:", tick.events.shape)
    if not tick.events.empty:
        print(tick.events.head(6).to_string())


if __name__ == "__main__":
    main()
