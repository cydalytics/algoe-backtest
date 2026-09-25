"""
Turnover Forecast

Step 3 of the loop: how much money each selection is about to take.

The MVP rule is that the next five minutes look like the last five, so the
forecast is simply the trailing bucket. It is a weak forecast and it is
meant to be - it keeps the optimizer's weighting transparent while the
pricing engine is what is being proven.

Two things stop the rule from degenerating. A selection that took nothing
in the last bucket is not necessarily dead - it may just have been quiet -
so an empty bucket falls back to the match's longer trailing average
distributed on that selection's usual share. And a match that has never
taken a bet (a fresh pre-match card) gets a small floor so the optimizer
still has something to weigh its lines by, rather than treating the whole
book as worthless.

Every row records which of those three rungs produced its number, so a
forecast is never mistaken for observed money.

Change Log:
-----------
2026-08-30      Rewritten for the offline HKJC feed (MVP rule 2)
"""

import numpy as np
import pandas as pd

from src.core import config
from src.core.logging import get_logger

logger = get_logger(__name__)


class TurnoverModel:
    """Attaches the turnover forecast to a tick's selection frame."""

    def __init__(self, model=None, floor=None):
        self.model = model or config.TURNOVER_MODEL
        self.floor = config.TURNOVER_FLOOR if floor is None else float(floor)

    def attach(self, tick):
        """Add ``t_hat`` (forecast next-bucket turnover) and friends.

        Adds:
            t_hat        forecast turnover for the coming bucket
            t_hat_src    observed | trailing | floor
            t_share      this selection's share of its match's forecast
            t_trend      last bucket over the one before it, as a ratio
        """
        sel = tick.selections
        if sel.empty:
            for col in ("t_hat", "t_share", "t_trend"):
                sel[col] = pd.Series(dtype=float)
            sel["t_hat_src"] = pd.Series(dtype=object)
            return sel

        known = (
            "persistence", "naive_lag5", "trailing_mean",
            "gametime", "blend", "momentum", "match_share",
            "phase_scale", "oracle", "zero",
        )
        if self.model not in known:
            tick.warnings.append(
                "unknown TURNOVER_MODEL '{}', falling back to "
                "persistence".format(self.model)
            )

        recent = pd.to_numeric(sel["t5m"], errors="coerce").fillna(0.0)
        t_hat, src = self._forecast(sel, recent, getattr(tick, "turnover_profile", None))

        quiet = t_hat <= 0
        if quiet.any():
            backfill = self._trailing(sel)
            use = quiet & backfill.notna() & (backfill > 0)
            t_hat = t_hat.where(~use, backfill)
            src = src.where(~use, "trailing")

        still_quiet = t_hat <= 0
        if still_quiet.any():
            t_hat = t_hat.where(~still_quiet, self.floor)
            src = src.where(~still_quiet, "floor")

        sel["t_hat"] = t_hat.round(2)
        sel["t_hat_src"] = src
        match_total = sel.groupby("match_id")["t_hat"].transform("sum")
        sel["t_share"] = (sel["t_hat"] / match_total.where(match_total > 0)).fillna(0.0)
        sel["t_trend"] = self._trend(sel)

        observed = int((src == "observed").sum())
        if observed < len(sel):
            tick.warnings.append(
                "turnover forecast: {} observed, {} backfilled from the "
                "longer window, {} on the floor".format(
                    observed, int((src == "trailing").sum()),
                    int((src == "floor").sum()))
            )
        logger.debug("turnover forecast built for %d selections", len(sel))
        return sel

    # -----------------------------------------------------------------------

    def _forecast(self, sel, recent, profile):
        """Dispatch the configured strategy. Quiet-bucket backfill is shared."""
        name = self.model
        src = pd.Series("observed", index=sel.index, dtype=object)
        if name in ("persistence", "naive_lag5", ""):
            return recent.copy(), src
        if name == "zero":
            return pd.Series(0.0, index=sel.index, dtype=float), pd.Series(
                "zero", index=sel.index, dtype=object)
        if name == "trailing_mean":
            mean = self._window_mean(sel)
            use = mean.notna() & (mean > 0)
            out = recent.where(~use, mean)
            src = src.where(~use, "trailing_mean")
            return out, src
        if name == "gametime":
            mapped = self._from_profile(sel, profile)
            use = mapped.notna() & (mapped > 0)
            out = recent.where(~use, mapped)
            src = src.where(~use, "gametime")
            return out, src
        if name == "blend":
            mapped = self._from_profile(sel, profile)
            weight = float(getattr(config, "BACKTEST_BLEND_PERSIST", 0.65))
            blended = weight * recent + (1.0 - weight) * mapped.fillna(recent)
            use = mapped.notna()
            src = src.where(~use, "blend")
            return blended.where(use, recent), src
        if name == "momentum":
            trend = self._trend(sel).clip(lower=0.4, upper=2.5)
            out = recent * trend
            src = pd.Series("momentum", index=sel.index, dtype=object)
            return out, src
        if name == "match_share":
            return self._match_share(sel, recent, profile)
        if name == "phase_scale":
            return self._phase_scale(sel, recent, profile)
        if name == "oracle":
            if "t_actual" in sel.columns:
                actual = pd.to_numeric(sel["t_actual"], errors="coerce")
                use = actual.notna()
                src = pd.Series("oracle", index=sel.index, dtype=object)
                return actual.where(use, recent), src.where(use, "observed")
            return recent.copy(), src
        return recent.copy(), src

    def _match_share(self, sel, recent, profile):
        """Persist the match total; allocate by walk-forward pool profile.

        Persistence on every selection copies last-bucket noise onto the
        next one. The match usually keeps taking similar money; the
        question is which pools it lands in. Using the phase×pool
        profile for the mix and the last match total for the level
        separates those two errors.
        """
        match_tot = recent.groupby(sel["match_id"]).transform("sum")
        mapped = self._from_profile(sel, profile)
        weight = mapped.where(mapped.notna() & (mapped > 0), recent)
        weight = weight.clip(lower=0.0)
        w_sum = weight.groupby(sel["match_id"]).transform("sum")
        share = weight / w_sum.where(w_sum > 0)
        out = match_tot * share.fillna(0.0)
        empty = (w_sum <= 0) | out.isna()
        out = out.where(~empty, recent)
        src = pd.Series("match_share", index=sel.index, dtype=object)
        src = src.where(~empty, "observed")
        return out, src

    def _phase_scale(self, sel, recent, profile):
        """Scale last-bucket money toward the walk-forward phase×pool mean.

        If this tick is quiet against what that phase usually does, we
        lift the forecast; if it is hot, we pull it back. Persistence
        alone cannot do that.
        """
        mapped = self._from_profile(sel, profile)
        pool = sel["pool_code"] if "pool_code" in sel.columns else None
        if pool is None or mapped.isna().all():
            src = pd.Series("observed", index=sel.index, dtype=object)
            return recent.copy(), src
        curr = recent.groupby(pool).transform("mean")
        scale = (mapped / curr.where(curr > 0)).clip(lower=0.4, upper=2.5)
        use = scale.notna()
        out = recent * scale.fillna(1.0)
        src = pd.Series("observed", index=sel.index, dtype=object)
        src = src.where(~use, "phase_scale")
        return out, src

    def _window_mean(self, sel):
        """Mean of the trailing 5-minute buckets that actually have money."""
        cols = ["t{}m".format(w) for w in config.TURNOVER_WINDOWS]
        have = [c for c in cols if c in sel.columns]
        if not have:
            return pd.Series(np.nan, index=sel.index, dtype=float)
        frame = sel[have].apply(pd.to_numeric, errors="coerce")
        frame = frame.mask(frame <= 0)
        return frame.mean(axis=1)

    def _from_profile(self, sel, profile):
        """Look up a walk-forward (phase, pool) mean, if the runner supplied one."""
        if not profile:
            return pd.Series(np.nan, index=sel.index, dtype=float)
        phase = sel["phase"] if "phase" in sel.columns else pd.Series(
            "", index=sel.index)
        keys = list(zip(phase.astype(str), sel["pool_code"].astype(str)))
        values = [profile.get(key) for key in keys]
        return pd.Series(values, index=sel.index, dtype=float)

    def _trailing(self, sel):
        """Per-bucket average over the longest window we keep.

        A selection that was quiet in the last five minutes but busy over
        the last twenty is far more likely to trade again than one that has
        never traded, and the average over the longer window says so.
        """
        longest = max(config.TURNOVER_WINDOWS)
        col = "t{}m".format(longest)
        if col not in sel.columns:
            return pd.Series(np.nan, index=sel.index, dtype=float)
        buckets = max(longest / config.BUCKET_MINUTES, 1.0)
        return pd.to_numeric(sel[col], errors="coerce") / buckets

    def _trend(self, sel):
        """Last bucket against the one before it - momentum, for the UI."""
        windows = sorted(config.TURNOVER_WINDOWS)
        if len(windows) < 2:
            return pd.Series(1.0, index=sel.index, dtype=float)
        last = pd.to_numeric(sel["t{}m".format(windows[0])], errors="coerce").fillna(0.0)
        prior = pd.to_numeric(sel["t{}m".format(windows[1])], errors="coerce").fillna(0.0)
        return (last / prior.where(prior > 0)).fillna(1.0).clip(upper=10.0).round(3)
