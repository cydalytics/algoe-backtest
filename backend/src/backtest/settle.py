"""
Selection Settlement

Turns a finished scoreline into a result for one priced selection.

The result is a signed stake multiple in {-1, -0.5, 0, +0.5, +1}:

    +1    full win
    +0.5  half win   (quarter line)
     0    push
    -0.5  half lose
    -1    full lose

Payout per unit staked at decimal odds O is then:

    win        O
    half-win   0.5 * O + 0.5
    push       1
    half-lose  0.5
    lose       0

which is what realized GM uses. Probability scores (log-loss, Brier,
calibration) map the same number onto [0, 1] and drop pushes.

Change Log:
-----------
2026-09-11      Initialize (W06 backtest)
"""

from typing import Optional

import numpy as np

from src.pricing.book import CRS_CAP, TTG_CAP
from src.pricing.grids import _split_line
from src.pricing.pools import PoolDef, resolve


EPS = 1e-9


def payout_unit(odds: float, result: float) -> float:
    """Cash returned per $1 staked, given a signed settlement result."""
    if result >= 1.0 - EPS:
        return float(odds)
    if result >= 0.5 - EPS:
        return 0.5 * float(odds) + 0.5
    if result > -0.5 + EPS:
        return 1.0
    if result > -1.0 + EPS:
        return 0.5
    return 0.0


def payout_unit_vec(odds, result):
    """Array form of payout_unit. NaN result stays NaN (unsettled)."""
    odds = np.asarray(odds, dtype=float)
    result = np.asarray(result, dtype=float)
    return np.select(
        [
            result >= 1.0 - EPS,
            result >= 0.5 - EPS,
            result > -0.5 + EPS,
            result > -1.0 + EPS,
        ],
        [odds, 0.5 * odds + 0.5, 1.0, 0.5],
        default=0.0,
    ) + np.where(np.isnan(result), np.nan, 0.0)


def outcome_y(result: float) -> Optional[float]:
    """Soft label in [0, 1] for probability scoring. None on a push."""
    if abs(result) < EPS:
        return None
    return (float(result) + 1.0) / 2.0


def settle_row(row, ft, ht, corners_ft, corners_ht) -> Optional[float]:
    """Settle one selection row. Returns None when the pool is unknown."""
    pool = resolve(row.get("pool_code"))
    if pool is None:
        return None
    return settle(
        pool,
        str(row.get("selection") or ""),
        row.get("line_value"),
        ft, ht, corners_ft, corners_ht,
    )


def settle(pool: PoolDef, selection: str, line,
           ft, ht, corners_ft, corners_ht) -> Optional[float]:
    """Settle one (pool, selection, line) against the finished boards."""
    board = _board(pool, ft, ht, corners_ft, corners_ht)
    if board is None:
        return None
    home, away = board
    kind = pool.kind
    sel = str(selection or "").strip().upper()

    if kind == "three_way":
        return _three_way(home, away, sel)
    if kind == "handicap_3w":
        return _three_way(home - away + float(line or 0.0), 0.0, sel)
    if kind == "asian_hcp":
        return _asian(home - away, -float(line or 0.0), sel, home_is_high=True)
    if kind == "asian_total":
        return _asian(home + away, float(line or 0.0), sel, home_is_high=False)
    if kind == "odd_even":
        odd = ((home + away) % 2) == 1
        if sel == "O":
            return 1.0 if odd else -1.0
        if sel == "E":
            return -1.0 if odd else 1.0
        return None
    if kind == "exact_total":
        return _exact_total(home + away, sel)
    if kind == "correct_score":
        return _correct_score(home, away, sel)
    if kind == "half_full":
        return _half_full(ht, ft, sel)
    return None


def _board(pool, ft, ht, corners_ft, corners_ht):
    if pool.domain == "corner":
        src = corners_ht if pool.seg == "HT" else corners_ft
    else:
        src = ht if pool.seg == "HT" else ft
    if src is None or src[0] is None or src[1] is None:
        return None
    return int(src[0]), int(src[1])


def _three_way(home, away, sel):
    if home > away:
        won = "H"
    elif home < away:
        won = "A"
    else:
        won = "D"
    if sel not in ("H", "D", "A"):
        return None
    return 1.0 if sel == won else -1.0


def _compare(value, line):
    """How ``value`` sits versus a (possibly quarter) line, over-side first."""
    split = _split_line(float(line))
    if split is None:
        if value > float(line) + EPS:
            return 1.0
        if value < float(line) - EPS:
            return -1.0
        return 0.0
    lo, hi = split
    return 0.5 * (_compare(value, lo) + _compare(value, hi))


def _asian(metric, line, sel, home_is_high):
    """Asian two-way. ``sel`` is H/A for handicap, H/L for totals."""
    over = _compare(metric, line)
    if home_is_high:
        if sel == "H":
            return over
        if sel in ("A", "L"):
            return -over
    else:
        if sel == "H":
            return over
        if sel == "L":
            return -over
    return None


def _exact_total(total, sel):
    plus = sel.endswith("+")
    digits = sel[:-1] if plus else sel
    if not digits.isdigit():
        return None
    n = int(digits)
    if plus:
        return 1.0 if total >= n else -1.0
    cap = TTG_CAP
    if n >= cap:
        return 1.0 if total >= cap else -1.0
    return 1.0 if total == n else -1.0


def _correct_score(home, away, sel):
    if sel == "OTHER":
        covered = home <= CRS_CAP and away <= CRS_CAP
        return -1.0 if covered else 1.0
    if ":" not in sel:
        return None
    try:
        h, a = sel.split(":", 1)
        return 1.0 if (int(h), int(a)) == (home, away) else -1.0
    except ValueError:
        return None


def _half_full(ht, ft, sel):
    if ht is None or ht[0] is None or ft is None or ft[0] is None:
        return None
    if len(sel) != 2 or sel[0] not in "HDA" or sel[1] not in "HDA":
        return None

    def letter(h, a):
        if h > a:
            return "H"
        if h < a:
            return "A"
        return "D"

    actual = letter(int(ht[0]), int(ht[1])) + letter(int(ft[0]), int(ft[1]))
    return 1.0 if sel == actual else -1.0
