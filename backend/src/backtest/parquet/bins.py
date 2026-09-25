"""TG and SUP buckets that do not import the rest of the backend.

The offline script bundle copies this folder on its own. These thresholds
are the same ones in ``src.backtest.dimensions``; keep the two copies aligned.
"""
from __future__ import annotations


def tg_bin(tg) -> str:
    try:
        v = float(tg)
    except (TypeError, ValueError):
        return "mid"
    if v < 1.8:
        return "low"
    if v < 2.6:
        return "mid"
    if v < 3.4:
        return "high"
    return "very_high"


def sup_bin(sup) -> str:
    try:
        v = float(sup)
    except (TypeError, ValueError):
        return "balanced"
    if v < -0.45:
        return "away"
    if v < 0.45:
        return "balanced"
    return "home"
