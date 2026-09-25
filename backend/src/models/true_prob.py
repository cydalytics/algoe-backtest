"""
True Probability

Step 4 of the loop: what the desk believes each selection is really worth.

The MVP rule is deliberately blunt - TrueProb is 1 / HKJC true odds. The
feed already carries a de-margined price per selection (HKJC_Odds.true_odds),
so the belief is simply read off it rather than modelled. That makes the
optimizer's objective honest: it can only move the offer, never the belief,
so it cannot improve expected margin by quietly rewriting what it thinks is
going to happen.

Not every selection has a true price on every tick, so the source falls
back in a fixed order and every row records which rung it landed on:

    hkjc_true   1 / true_odds straight from the feed          (the MVP rule)
    hkjc_offer  the offered price, de-margined across its line
    market      the external consensus, de-margined
    model       our own grid at the Current algo_param theta

BOOK COHERENCE: 1/true_odds across one line rarely sums to exactly 1. The
raw value is kept as the belief - that is what the rule says - and the sum
is reported per line as ``book_sum`` so an incoherent feed shows up in the
cockpit instead of being silently normalised away. Set
TRUE_PROB_NORMALIZE to rescale exhaustive lines onto 1.0 instead.

Change Log:
-----------
2026-08-30      Rewritten for the offline HKJC feed (MVP rule 1)
"""

import numpy as np
import pandas as pd

from src.core import config
from src.core.logging import get_logger
from src.pricing import book as book_lib
from src.pricing import pools as pool_defs

logger = get_logger(__name__)

# Kinds whose selections partition the outcome space, so their probabilities
# are expected to sum to one and may legitimately be rescaled onto it.
EXHAUSTIVE_KINDS = frozenset({
    "three_way", "handicap_3w", "asian_hcp", "asian_total",
    "odd_even", "exact_total", "half_full",
})

LINE_KEYS = ["match_id", "pool_id", "line_id"]

SOURCE_ORDER = ("hkjc_true", "hkjc_offer", "market", "model")


class TrueProbModel:
    """Attaches the belief columns to a tick's selection frame."""

    def __init__(self, source=None, normalize=None, fallback=True, layer=None):
        self.source = source or config.TRUE_PROB_SOURCE
        self.normalize = (
            config.TRUE_PROB_NORMALIZE if normalize is None else bool(normalize)
        )
        # The live desk walks the fallback chain so a missing true price
        # does not blank the book. A backtest that is comparing sources
        # must not silently substitute another one.
        self.fallback = bool(fallback)
        self.layer = layer or config.THETA_LAYER

    # -----------------------------------------------------------------------

    def attach(self, tick):
        """Add belief columns to ``tick.selections`` in place and return it.

        Adds:
            true_prob        the belief, per the fallback chain
            true_prob_raw    before any normalisation
            true_prob_src    which rung of the chain produced it
            true_odds_used   1 / true_prob, the zero-margin price
            book_sum         sum of raw beliefs across the line
            book_overround   book_sum - 1, positive when the feed overprices
            hold             1 - hkjc_odds * true_prob, margin per $1 sold
        """
        sel = tick.selections
        if sel.empty:
            for col in ("true_prob", "true_prob_raw", "true_odds_used",
                        "book_sum", "book_overround", "hold"):
                sel[col] = pd.Series(dtype=float)
            sel["true_prob_src"] = pd.Series(dtype=object)
            return sel

        raw, src = self._raw_belief(sel, tick)
        sel["true_prob_raw"] = raw
        sel["true_prob_src"] = src

        book = sel.groupby(LINE_KEYS)["true_prob_raw"].transform("sum")
        sel["book_sum"] = book.round(6)
        sel["book_overround"] = (book - 1.0).round(6)

        sel["true_prob"] = self._normalised(sel, book) if self.normalize else raw
        sel["true_prob"] = sel["true_prob"].clip(lower=0.0, upper=1.0)

        with np.errstate(divide="ignore"):
            sel["true_odds_used"] = np.where(
                sel["true_prob"] > 0, 1.0 / sel["true_prob"].replace(0, np.nan), 0.0
            )
        sel["true_odds_used"] = sel["true_odds_used"].fillna(0.0).round(3)
        sel["hold"] = (1.0 - sel["hkjc_odds"] * sel["true_prob"]).round(6)

        self._report(sel, tick)
        return sel

    # -----------------------------------------------------------------------

    def _raw_belief(self, sel, tick):
        """Walk the fallback chain, taking the first source that has a price."""
        n = len(sel)
        prob = pd.Series(np.nan, index=sel.index, dtype=float)
        src = pd.Series("", index=sel.index, dtype=object)

        chain = [self.source]
        if self.fallback:
            chain = [self.source] + [s for s in SOURCE_ORDER if s != self.source]
        for name in chain:
            missing = prob.isna()
            if not missing.any():
                break
            candidate = self._from_source(name, sel, tick)
            usable = missing & candidate.notna() & (candidate > 0)
            prob = prob.where(~usable, candidate)
            src = src.where(~usable, name)

        unresolved = int(prob.isna().sum())
        if unresolved:
            tick.warnings.append(
                "{} of {} selections have no true price on any source; "
                "priced at zero and left out of the objective".format(unresolved, n)
            )
        return prob.fillna(0.0), src.replace("", "none")

    def _from_source(self, name, sel, tick):
        if name == "hkjc_true":
            return self._reciprocal(sel["hkjc_true_odds"])
        if name == "hkjc_offer":
            return self._demargined(sel, sel["hkjc_odds"])
        if name == "market":
            return self._demargined(sel, sel.get("mkt_avg"))
        if name == "model":
            return self._model(sel, tick)
        return pd.Series(np.nan, index=sel.index, dtype=float)

    @staticmethod
    def _reciprocal(odds):
        odds = pd.to_numeric(odds, errors="coerce")
        return (1.0 / odds).where(odds > 1.0)

    def _demargined(self, sel, odds):
        """Implied probs rescaled so their line sums to one.

        A quoted book carries the seller's margin; dividing by the book sum
        is the standard way to read a belief out of it. Only lines whose
        selections partition the outcome space can be treated this way.
        """
        if odds is None:
            return pd.Series(np.nan, index=sel.index, dtype=float)
        implied = self._reciprocal(odds)
        frame = pd.DataFrame({"p": implied, "kind": sel["kind"]}, index=sel.index)
        for key in LINE_KEYS:
            frame[key] = sel[key]
        total = frame.groupby(LINE_KEYS)["p"].transform("sum")
        out = frame["p"] / total.where(total > 0)
        return out.where(frame["kind"].isin(EXHAUSTIVE_KINDS), implied)

    def _model(self, sel, tick):
        """Our own grid price - the last resort when the feed is silent."""
        out = pd.Series(np.nan, index=sel.index, dtype=float)
        layer = self.layer
        for match_id, group in sel.groupby("match_id"):
            ctx = tick.contexts.get(int(match_id))
            if ctx is None:
                continue
            model = book_lib.BookModel(ctx.theta.get(layer, {}), ctx.state)
            for idx, row in group.iterrows():
                pool = pool_defs.resolve(row["pool_code"])
                if pool is None:
                    continue
                try:
                    value = model.probability(pool, row["line_value"], row["selection"])
                except Exception:                      # a bad line label, say
                    value = None
                if value:
                    out.at[idx] = value
        return out

    def _normalised(self, sel, book):
        """Rescale exhaustive lines onto 1.0, leave partial books alone."""
        scaled = sel["true_prob_raw"] / book.where(book > 0)
        return scaled.where(sel["kind"].isin(EXHAUSTIVE_KINDS), sel["true_prob_raw"])

    def _report(self, sel, tick):
        counts = sel["true_prob_src"].value_counts().to_dict()
        primary = counts.get(self.source, 0)
        if primary < len(sel):
            tick.warnings.append(
                "true prob sources: " + ", ".join(
                    "{}={}".format(k, v) for k, v in sorted(counts.items())
                )
            )
        # A line whose beliefs are far off 1.0 will distort expected margin,
        # so it is worth naming rather than burying in a column.
        mains = sel[sel["is_main_line"] & sel["kind"].isin(EXHAUSTIVE_KINDS)]
        if not mains.empty:
            per_line = mains.groupby(LINE_KEYS)["book_sum"].first()
            bad = per_line[(per_line - 1.0).abs() > config.BOOK_SUM_TOLERANCE]
            if not bad.empty:
                tick.warnings.append(
                    "{} main line(s) have an incoherent true book "
                    "(worst sum {:.3f})".format(len(bad), bad.iloc[
                        (bad - 1.0).abs().values.argmax()])
                )
        logger.debug("true prob sources: %s", counts)
