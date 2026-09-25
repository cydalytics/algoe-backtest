"""
Book Pricing Pass

Prices the whole board at one theta and lays our number next to the
feed's, so every downstream consumer - the optimizer's leg filter, the
risk grid, the cockpit - reads the same columns.

For each selection this adds our model probability, the zero-margin fair
odds, the offer after the pool margin, and where that offer sits against
what HKJC is showing and what the external market is showing. It also
decides whether the selection is tradable at all: a first-half market
after the break is settled, a scoreline the board has ruled out is
settled, and a price nobody would write is closed.

The same pass runs twice per tick - once at the desk's current theta and
once at the optimizer's recommendation - which is what lets the cockpit
show a before and after on the same rows.

Change Log:
-----------
2026-08-30      Initialize
"""

import numpy as np
import pandas as pd

from src.core import config
from src.core.logging import get_logger
from src.pricing import book as book_lib
from src.pricing import pools as pool_defs

logger = get_logger(__name__)

PRICE_COLS = (
    "model_prob", "fair_odds", "sell_odds", "status",
    "edge_hkjc", "edge_mkt", "exp_gm_unit",
)


class Pricer:
    """Attaches our own price to every selection on a tick."""

    def __init__(self, semantics=None, layer=None):
        self.semantics = semantics or config.THETA_SEMANTICS
        self.layer = layer or config.THETA_LAYER

    def attach(self, tick, theta_by_match=None, suffix=""):
        """Price ``tick.selections`` and return it.

        Args:
            theta_by_match: optional ``{match_id: theta dict}`` override,
                used to re-price at the optimizer's recommendation. When
                omitted each match is priced at its configured layer.
            suffix: appended to the column names, so a second pass can sit
                alongside the first instead of overwriting it.

        Adds (before the suffix):
            model_prob    our fair probability for the selection
            fair_odds     1 / model_prob, capped
            sell_odds     the offer after the pool margin, on the ladder
            status        open | settled | closed | suspended
            edge_hkjc     our offer over the HKJC price, as a ratio
            edge_mkt      our offer over the external consensus
            exp_gm_unit   margin per $1 sold at the belief in true_prob
        """
        sel = tick.selections
        if sel.empty:
            for col in PRICE_COLS:
                sel[col + suffix] = pd.Series(dtype=object)
            return sel

        probs = np.full(len(sel), np.nan)
        status = np.empty(len(sel), dtype=object)
        status[:] = "closed"
        positions = {key: i for i, key in enumerate(sel.index)}

        # For the default (what-if) pricing pass at a user-supplied theta,
        # use the Poisson grid so the slider actually moves numbers.
        for match_id, group in sel.groupby("match_id"):
            ctx = tick.contexts.get(int(match_id))
            if ctx is None:
                continue
            theta = (theta_by_match or {}).get(int(match_id))
            if theta is None:
                theta = ctx.theta.get(self.layer, {})
            model = book_lib.BookModel(theta, ctx.state, self.semantics)

            for idx, row in group.iterrows():
                pos = positions[idx]
                pool = pool_defs.resolve(row["pool_code"])
                if pool is None:
                    continue
                try:
                    prob = model.probability(
                        pool, row["line_value"], row["selection"]
                    )
                except Exception as exc:
                    logger.debug("cannot price %s: %s", row.get("key"), exc)
                    prob = None
                feed = "open" if row.get("pool_open", True) else "suspended"
                status[pos] = book_lib.selection_status(pool, prob, ctx.state, feed)
                if prob is not None:
                    probs[pos] = prob

        sel["model_prob" + suffix] = probs
        sel["status" + suffix] = status
        fair = np.array([book_lib.fair_odds(p if p == p else 0.0) for p in probs])
        sel["fair_odds" + suffix] = np.round(fair, 3)
        sel["sell_odds" + suffix] = [
            book_lib.sell_odds(f, m)
            for f, m in zip(fair, sel["margin"].astype(float))
        ]

        offer = sel["sell_odds" + suffix].astype(float)
        hkjc = pd.to_numeric(sel["hkjc_odds"], errors="coerce")
        mkt = pd.to_numeric(sel.get("mkt_avg"), errors="coerce")
        sel["edge_hkjc" + suffix] = (offer / hkjc.where(hkjc > 1.0) - 1.0).round(4)
        sel["edge_mkt" + suffix] = (offer / mkt.where(mkt > 1.0) - 1.0).round(4)

        # What a dollar sold on this selection is worth, given the belief.
        if "true_prob" in sel.columns:
            belief = pd.to_numeric(sel["true_prob"], errors="coerce").fillna(0.0)
            sel["exp_gm_unit" + suffix] = (1.0 - offer * belief).round(5)
        else:
            sel["exp_gm_unit" + suffix] = np.nan

        counts = pd.Series(status).value_counts().to_dict()
        logger.debug("priced %d selections: %s", len(sel), counts)
        if suffix == "" and counts.get("open", 0) == 0:
            tick.warnings.append("no selection priced as open - check the theta")
        return sel

    def attach_match(self, tick, match_id, theta, suffix):
        """Price one match's rows in place, leaving the rest of the board.

        The background solver finishes one match at a time and publishes
        each recommendation as it lands, so the theta* columns have to be
        fillable a match at a time. A full ``attach`` would rebuild every
        row on the card for each match solved - quadratic work, and it
        would briefly blank the matches already done.
        """
        sel = tick.selections
        if sel.empty:
            return
        rows = sel.index[sel["match_id"] == int(match_id)]
        if not len(rows):
            return
        ctx = tick.contexts.get(int(match_id))
        if ctx is None:
            return

        for col in PRICE_COLS:
            name = col + suffix
            if name not in sel.columns:
                sel[name] = np.nan if col != "status" else None

        true_odds_vals = pd.to_numeric(sel.loc[rows, "hkjc_true_odds"], errors="coerce").values
        probs = np.where(true_odds_vals > 1.0, 1.0 / true_odds_vals, 0.0)

        status = []
        for i, (_, row) in enumerate(sel.loc[rows].iterrows()):
            pool = pool_defs.resolve(row["pool_code"])
            feed = "open" if row.get("pool_open", True) else "suspended"
            status.append(
                book_lib.selection_status(pool, probs[i], ctx.state, feed)
                if pool is not None else "closed"
            )
        status = np.array(status, dtype=object)

        probs = np.asarray(probs, dtype=float)
        fair = np.array([book_lib.fair_odds(p if p == p else 0.0) for p in probs])
        margins = sel.loc[rows, "margin"].astype(float).to_numpy()
        offer = np.array([book_lib.sell_odds(f, m) for f, m in zip(fair, margins)])

        sel.loc[rows, "model_prob" + suffix] = probs
        sel.loc[rows, "status" + suffix] = status
        sel.loc[rows, "fair_odds" + suffix] = np.round(fair, 3)
        sel.loc[rows, "sell_odds" + suffix] = offer

        hkjc = pd.to_numeric(sel.loc[rows, "hkjc_odds"], errors="coerce")
        mkt = pd.to_numeric(sel.loc[rows].get("mkt_avg"), errors="coerce")
        sel.loc[rows, "edge_hkjc" + suffix] = (
            offer / hkjc.where(hkjc > 1.0) - 1.0).round(4)
        sel.loc[rows, "edge_mkt" + suffix] = (
            offer / mkt.where(mkt > 1.0) - 1.0).round(4)
        if "true_prob" in sel.columns:
            belief = pd.to_numeric(
                sel.loc[rows, "true_prob"], errors="coerce").fillna(0.0)
            sel.loc[rows, "exp_gm_unit" + suffix] = (1.0 - offer * belief).round(5)

    def expected_gm(self, sel, suffix=""):
        """Expected gross margin over the bucket for a priced frame.

        Only open selections carrying both a forecast and a belief count;
        anything else has no money riding on it this tick.
        """
        if sel.empty:
            return {"turnover": 0.0, "payout": 0.0, "gm": 0.0, "legs": 0}
        live = sel[
            (sel["status" + suffix] == "open")
            & (sel.get("t_hat", 0) > 0)
            & (sel.get("true_prob", 0) > 0)
        ]
        turnover = float(live["t_hat"].sum())
        payout = float(
            (live["t_hat"] * live["sell_odds" + suffix] * live["true_prob"]).sum()
        )
        return {
            "turnover": round(turnover, 2),
            "payout": round(payout, 2),
            "gm": round(turnover - payout, 2),
            "margin_pct": round(
                100.0 * (turnover - payout) / turnover, 3
            ) if turnover > 0 else 0.0,
            "legs": int(len(live)),
        }