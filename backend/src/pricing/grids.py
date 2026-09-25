"""
Score Grids

Poisson score-matrix machinery, ported from the poisson_betting cell of
"5 Data Extraction - Real Time.ipynb". Everything downstream prices off a
2-D grid where ``grid[h, a]`` is P(home scores h, away scores a), so the
grid is built once per theta and reused across every selection instead of
being rebuilt per row the way the notebook does it.

Grids also carry the in-play shift: with THETA_SEMANTICS = "remaining",
Goal90*TG/SUP describe what is still to come, so the grid is convolved with
the goals already on the board before any line is resolved.

Change Log:
-----------
2026-08-30      Initialize (port of notebook poisson_betting)
"""

from functools import lru_cache

import numpy as np
from scipy.signal import fftconvolve

from src.core import config

EPS = 1e-12


@lru_cache(maxsize=64)
def _log_factorial(n: int) -> np.ndarray:
    """Cumulative log-factorial for 0..n, used by the Poisson pmf."""
    out = np.zeros(n + 1)
    out[1:] = np.cumsum(np.log(np.arange(1, n + 1)))
    return out


def poisson_pmf(lam: float, n: int) -> np.ndarray:
    """P(X = k) for k in 0..n under Poisson(lam).

    Written out rather than calling scipy so the optimizer can afford to
    rebuild grids inside its objective function.
    """
    lam = max(float(lam), EPS)
    k = np.arange(n + 1)
    return np.exp(-lam + k * np.log(lam) - _log_factorial(n))


def score_grid(total, supremacy, max_n=None, draw_factor=None):
    """Joint score distribution from a total/supremacy pair.

    Args:
        total (float): Expected total events (home + away).
        supremacy (float): Expected difference (home - away).
        max_n (int): Grid depth per team.
        draw_factor (float): Diagonal inflation; 1.0 leaves the grid alone.

    Returns:
        np.ndarray: ``(max_n+1, max_n+1)`` grid summing to 1.
    """
    max_n = config.MAX_GOALS if max_n is None else max_n
    draw_factor = config.DRAW_FACTOR if draw_factor is None else draw_factor

    total = max(float(total), 0.0)
    # A supremacy wider than the total implies a negative expectancy for one
    # side. Clamping the rates independently would quietly turn that into a
    # certainty - the weaker side could never score again, so the other side
    # of the market would price at zero. Clamping the supremacy instead keeps
    # the total intact and degrades to "one-sided but still possible".
    supremacy = min(max(float(supremacy), -total), total)

    home = max((total + supremacy) / 2.0, 0.0)
    away = max((total - supremacy) / 2.0, 0.0)

    grid = np.outer(poisson_pmf(home, max_n), poisson_pmf(away, max_n))
    if draw_factor != 1.0:
        idx = np.diag_indices_from(grid)
        grid[idx] *= draw_factor
    total_mass = grid.sum()
    return grid / total_mass if total_mass > 0 else grid


def shift_grid(grid, dh, da):
    """Add ``dh`` home and ``da`` away events already on the board."""
    dh, da = int(dh), int(da)
    if dh <= 0 and da <= 0:
        return grid
    out = np.zeros((grid.shape[0] + dh, grid.shape[1] + da))
    out[dh:, da:] = grid
    return out


def convolve_grids(a, b):
    """Distribution of the sum of two independent score grids."""
    return fftconvolve(a, b, mode="full")


def point_grid(h, a, shape=None):
    """A degenerate grid with all mass on a known scoreline."""
    h, a = int(h), int(a)
    rows = max(h + 1, 1 if shape is None else shape[0])
    cols = max(a + 1, 1 if shape is None else shape[1])
    out = np.zeros((rows, cols))
    out[h, a] = 1.0
    return out


# ---------------------------------------------------------------------------
# Index helpers, memoised per grid shape
# ---------------------------------------------------------------------------

@lru_cache(maxsize=64)
def _indices(rows: int, cols: int):
    i, j = np.indices((rows, cols))
    return i, j


def diff_matrix(grid):
    """``home - away`` for every cell of the grid."""
    i, j = _indices(*grid.shape)
    return i - j


def total_matrix(grid):
    """``home + away`` for every cell of the grid."""
    i, j = _indices(*grid.shape)
    return i + j


# ---------------------------------------------------------------------------
# Market resolvers - each returns fair probabilities, never odds
# ---------------------------------------------------------------------------

def three_way(grid):
    """Home / Draw / Away probabilities."""
    d = diff_matrix(grid)
    return {
        "H": float(grid[d > 0].sum()),
        "D": float(grid[d == 0].sum()),
        "A": float(grid[d < 0].sum()),
    }


def three_way_handicap(grid, line):
    """HHAD: three-way after the home team's handicap is applied.

    A handicap draw is only reachable on whole-goal lines; on half lines
    the draw leg is a dead selection and comes back at zero.
    """
    adj = diff_matrix(grid) + float(line)
    return {
        "H": float(grid[adj > 0].sum()),
        "D": float(grid[np.isclose(adj, 0.0)].sum()),
        "A": float(grid[adj < 0].sum()),
    }


def _split_line(value):
    """Quarter lines settle as two half-stakes on the neighbouring lines."""
    is_quarter = (abs(value) * 4) % 2 != 0
    return (value - 0.25, value + 0.25) if is_quarter else None


def _two_way_from_metric(grid, metric, line, higher_wins):
    """Shared push / half-win engine for handicaps and totals.

    Args:
        metric (np.ndarray): Goal difference or goal total per cell.
        line (float): The line the metric is compared against.
        higher_wins (bool): True when the first leg wins on metric > line.

    Returns:
        dict: ``{"win", "lose", "detail"}`` where win/lose are the effective
        probabilities that price each side once pushes are refunded.
    """
    line = float(line)
    split = _split_line(line)

    if split is None:
        over = metric > line if higher_wins else metric < line
        under = metric < line if higher_wins else metric > line
        p_over = float(grid[over].sum())
        p_under = float(grid[under].sum())
        p_push = float(grid[np.isclose(metric, line)].sum())
        live = p_over + p_under
        if live <= 0:
            return {"win": 0.0, "lose": 0.0, "detail": {"push": p_push}}
        return {
            "win": p_over / live,
            "lose": p_under / live,
            "detail": {"win": p_over, "lose": p_under, "push": p_push},
        }

    lo, hi = split
    if higher_wins:
        l1_win, l1_push = metric > lo, np.isclose(metric, lo)
        l2_win, l2_push = metric > hi, np.isclose(metric, hi)
    else:
        l1_win, l1_push = metric < lo, np.isclose(metric, lo)
        l2_win, l2_push = metric < hi, np.isclose(metric, hi)
    l1_lose = ~(l1_win | l1_push)
    l2_lose = ~(l2_win | l2_push)

    full_win = float(grid[l1_win & l2_win].sum())
    half_win = float(grid[(l1_win & l2_push) | (l1_push & l2_win)].sum())
    half_lose = float(grid[(l1_lose & l2_push) | (l1_push & l2_lose)].sum())
    full_lose = float(grid[l1_lose & l2_lose].sum())

    # 1 = P_fw*O + P_hw*0.5*(O+1) + P_hl*0.5  ->  O = num / (P_fw + 0.5*P_hw)
    num = 1.0 - 0.5 * half_win - 0.5 * half_lose
    den_win = full_win + 0.5 * half_win
    den_lose = full_lose + 0.5 * half_lose
    return {
        "win": (den_win / num) if num > 0 else 0.0,
        "lose": (den_lose / num) if num > 0 else 0.0,
        "detail": {
            "full_win": full_win,
            "half_win": half_win,
            "half_lose": half_lose,
            "full_lose": full_lose,
        },
    }


def asian_handicap(grid, line):
    """HDC / CHDC. ``line`` is the home handicap: -0.5 means home gives 0.5.

    Home covers when ``diff + line > 0``, i.e. when ``diff > -line``. The
    line is handed over as the comparison threshold rather than folded into
    the metric, otherwise the quarter-line split below would never fire.
    """
    res = _two_way_from_metric(grid, diff_matrix(grid), -float(line), True)
    return {"H": res["win"], "A": res["lose"], "detail": res["detail"]}


def over_under(grid, line):
    """HILO / FHLO / CHLO. H is the high (over) leg, L the low (under)."""
    res = _two_way_from_metric(grid, total_matrix(grid), float(line), True)
    return {"H": res["win"], "L": res["lose"], "detail": res["detail"]}


def odd_even(grid):
    """OOE on the total."""
    t = total_matrix(grid)
    return {
        "O": float(grid[t % 2 == 1].sum()),
        "E": float(grid[t % 2 == 0].sum()),
    }


def exact_total(grid, cap):
    """TTG: exact totals 0..cap-1 plus a ``cap+`` bucket."""
    t = total_matrix(grid)
    out = {str(k): float(grid[t == k].sum()) for k in range(cap)}
    out["{}+".format(cap)] = float(grid[t >= cap].sum())
    return out


def correct_score(grid, cap=4):
    """CRS / FCRS: every scoreline inside the cap, then ``other``.

    Always emits the full cap x cap board even when the grid is smaller
    than that - a half-time point mass would otherwise drop most of the
    keys the feed asks for, and a missing key reads as unpriceable rather
    than as a scoreline that can no longer happen.
    """
    out = {}
    covered = 0.0
    rows, cols = grid.shape
    for h in range(cap + 1):
        for a in range(cap + 1):
            p = float(grid[h, a]) if h < rows and a < cols else 0.0
            p = max(p, 0.0)
            out["{}:{}".format(h, a)] = p
            covered += p
    out["OTHER"] = max(0.0, 1.0 - covered)
    return out


def half_full(ht_grid, sh_grid):
    """HFT: the nine half-time / full-time combinations.

    Convolves the half-time grid with the second-half increment grid so the
    two legs stay dependent, which a product of marginals would lose.
    """
    labels = ("H", "D", "A")
    ht_diff = diff_matrix(ht_grid)
    masks = {
        "H": ht_diff > 0,
        "D": ht_diff == 0,
        "A": ht_diff < 0,
    }
    out = {}
    for ht_label in labels:
        branch = ht_grid * masks[ht_label]
        weight = float(branch.sum())
        if weight <= 0:
            for ft_label in labels:
                out["{}{}".format(ht_label, ft_label)] = 0.0
            continue
        joint = convolve_grids(branch, sh_grid)
        ft = three_way(joint)
        for ft_label in labels:
            out["{}{}".format(ht_label, ft_label)] = float(ft[ft_label])
    return out


def first_to_score(grid, home_rate, away_rate, scored_already):
    """FTS-style split of the no-goal mass across the two teams."""
    if scored_already:
        return None
    p_none = float(grid[0, 0])
    live = 1.0 - p_none
    denom = max(home_rate + away_rate, EPS)
    return {
        "H": live * home_rate / denom,
        "A": live * away_rate / denom,
        "N": p_none,
    }
