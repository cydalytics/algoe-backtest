"""
Demand Response

The live desk treats next-bucket turnover as price-insensitive. That
makes every optimizer that can lengthen a price look like free money,
and it is why a naive backtest slams theta to the trust-region wall.

A constant-elasticity response is the smallest honest alternative:

    t(odds') = t(odds) * (odds' / odds) ** (-e)

e = 0 is the MVP. e = 1 means a 10% longer price attracts 10% more
stake. The same map is applied to the forecast (so the solver and the
E[GM] number agree) and to the realised next-bucket money (so the
counterfactual GM is not "same tickets, better prices").

Change Log:
-----------
2026-09-11      Initialize (W06 backtest labs)
"""

import numpy as np
import pandas as pd

from src.core import config


def scale_stake(stake, odds_from, odds_to, elasticity) -> pd.Series:
    """Reallocate stake from the posted price to a counterfactual price."""
    stake = pd.to_numeric(stake, errors="coerce").fillna(0.0)
    src = pd.to_numeric(odds_from, errors="coerce")
    dst = pd.to_numeric(odds_to, errors="coerce")
    e = float(elasticity or 0.0)
    if e <= 0:
        return stake
    ratio = dst / src.where(src > 0)
    factor = ratio.where(ratio.notna() & (ratio > 0), 1.0) ** (-e)
    return (stake * factor.clip(lower=0.05, upper=20.0)).astype(float)


def attach_counterfactual(sel: pd.DataFrame, elasticity=None) -> pd.DataFrame:
    """Write t_hat_star / t_actual_star for the posted vs recommended offer."""
    e = config.DEMAND_ELASTICITY if elasticity is None else float(elasticity)
    now = sel.get("sell_odds")
    star = sel.get("sell_odds_star", now)
    sel["t_hat_star"] = scale_stake(sel.get("t_hat"), now, star, e)
    if "t_actual" in sel.columns:
        sel["t_actual_star"] = scale_stake(sel.get("t_actual"), now, star, e)
    else:
        sel["t_actual_star"] = sel.get("t_hat_star")
    sel["elasticity"] = e
    return sel
