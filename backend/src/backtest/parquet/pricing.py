"""Poisson pricer for the pools the desk optimises.

Same maths as `poisson_betting.py`, restructured for speed: that module rebuilds
the whole score matrix for every line, which is the dominant cost inside an
optimiser loop. Here the matrix is built once per (total_goals, supremacy) and
every line of the match is then priced off it with boolean masks.

`selftest.py` asserts this agrees with `poisson_betting` to 1e-9 across a grid of
total goals, supremacy and lines, including quarter lines. If you change
anything here, that parity test is what tells you whether you broke it.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

import numpy as np

MAX_GOALS = 10

# score-matrix index helpers, built once
_I, _J = np.indices((MAX_GOALS + 1, MAX_GOALS + 1))
_GOAL_DIFF = _I - _J          # home goals - away goals
_TOTAL = _I + _J              # home goals + away goals

# pool -> (domain, market kind). domain picks which theta pair prices it.
POOL_KIND: Dict[str, Tuple[str, str]] = {
    "HILO": ("goal", "ou"),
    "HDC": ("goal", "ah"),
    "CHLO": ("corner", "ou"),
    "CHDC": ("corner", "ah"),
    "FHILO": ("goal_fh", "ou"),
    "FHLO": ("goal_fh", "ou"),
    "FHDC": ("goal_fh", "ah"),
    "FCHLO": ("corner_fh", "ou"),
    "FCHDC": ("corner_fh", "ah"),
}


def _poisson_pmf(lam: float) -> np.ndarray:
    """P(N=k) for k in 0..MAX_GOALS. Recurrence beats scipy here because this
    runs tens of thousands of times inside the solver."""
    lam = max(float(lam), 0.0)
    out = np.empty(MAX_GOALS + 1)
    out[0] = np.exp(-lam)
    for k in range(1, MAX_GOALS + 1):
        out[k] = out[k - 1] * lam / k
    return out


def score_matrix(total_goals: float, supremacy: float,
                 draw_factor: float = 1.0) -> np.ndarray:
    """Normalised joint distribution of (home goals, away goals)."""
    home = max((total_goals + supremacy) / 2.0, 0.0)
    away = max((total_goals - supremacy) / 2.0, 0.0)
    m = np.outer(_poisson_pmf(home), _poisson_pmf(away))
    if draw_factor != 1.0:
        idx = np.diag_indices_from(m)
        m[idx] *= draw_factor
    s = m.sum()
    return m / s if s > 0 else m


def _is_quarter(line: float) -> bool:
    return (abs(line) * 4) % 2 != 0


def asian_handicap(m: np.ndarray, handicap: float) -> Tuple[float, float]:
    """Fair (home, away) decimal odds for a home handicap. Quarter lines split
    into two half-lines exactly as poisson_betting does."""
    gd = _GOAL_DIFF
    if _is_quarter(handicap):
        l1, l2 = handicap + 0.25, handicap - 0.25
        a1, a2 = gd + l1, gd + l2
        w1, p1, s1 = a1 > 0, np.isclose(a1, 0), a1 < 0
        w2, p2, s2 = a2 > 0, np.isclose(a2, 0), a2 < 0
        full_win = m[w1 & w2].sum()
        half_win = m[(w1 & p2) | (p1 & w2)].sum()
        half_loss = m[(s1 & p2) | (p1 & s2)].sum()
        full_loss = m[s1 & s2].sum()
        num = 1.0 - 0.5 * half_win - 0.5 * half_loss
        den_h = full_win + 0.5 * half_win
        den_a = full_loss + 0.5 * half_loss
        return (num / den_h if den_h > 0 else 0.0,
                num / den_a if den_a > 0 else 0.0)
    adj = gd + handicap
    win = m[adj > 0].sum()
    loss = m[adj < 0].sum()
    tot = win + loss
    if tot <= 0:
        return 0.0, 0.0
    eh, ea = win / tot, loss / tot
    return (1.0 / eh if eh > 0 else 0.0, 1.0 / ea if ea > 0 else 0.0)


def over_under(m: np.ndarray, threshold: float) -> Tuple[float, float]:
    """Fair (over, under) decimal odds for a total line."""
    tot = _TOTAL
    if _is_quarter(threshold):
        l1, l2 = threshold - 0.25, threshold + 0.25
        o1, p1, u1 = tot > l1, np.isclose(tot, l1), tot < l1
        o2, p2, u2 = tot > l2, np.isclose(tot, l2), tot < l2
        full_win = m[o1 & o2].sum()
        half_win = m[(o1 & p2) | (p1 & o2)].sum()
        half_loss = m[(u1 & p2) | (p1 & u2)].sum()
        full_loss = m[u1 & u2].sum()
        num = 1.0 - 0.5 * half_win - 0.5 * half_loss
        den_o = full_win + 0.5 * half_win
        den_u = full_loss + 0.5 * half_loss
        return (num / den_o if den_o > 0 else 0.0,
                num / den_u if den_u > 0 else 0.0)
    over = m[tot > threshold].sum()
    under = m[tot < threshold].sum()
    s = over + under
    if s <= 0:
        return 0.0, 0.0
    eo, eu = over / s, under / s
    return (1.0 / eo if eo > 0 else 0.0, 1.0 / eu if eu > 0 else 0.0)


# --------------------------------------------------------------------- book
class Book:
    """The live selections of one match-bucket, pre-parsed so the optimiser can
    reprice the whole book from a theta pair without touching pandas.

    Arrays are aligned: row i is one selection.
    """

    __slots__ = ("pool", "domain", "kind", "line", "is_home", "turnover",
                 "p_true", "actual_odds", "margin", "n")

    def __init__(self, pool, domain, kind, line, is_home, turnover, p_true,
                 actual_odds, margin):
        self.pool = np.asarray(pool, dtype=object)
        self.domain = np.asarray(domain, dtype=object)
        self.kind = np.asarray(kind, dtype=object)
        self.line = np.asarray(line, dtype="float64")
        self.is_home = np.asarray(is_home, dtype=bool)
        self.turnover = np.asarray(turnover, dtype="float64")
        self.p_true = np.asarray(p_true, dtype="float64")
        self.actual_odds = np.asarray(actual_odds, dtype="float64")
        self.margin = np.asarray(margin, dtype="float64")
        self.n = len(self.line)

    def fair_odds(self, theta: Dict[str, Tuple[float, float]]) -> np.ndarray:
        """Fair odds per selection for a theta dict {domain: (tg, sup)}.

        One score matrix per domain, then a cache per (kind, line) so the two
        sides of a line share the single call that prices both.
        """
        out = np.zeros(self.n)
        mats = {d: score_matrix(*theta[d]) for d in set(self.domain) if d in theta}
        cache: Dict[tuple, Tuple[float, float]] = {}
        for i in range(self.n):
            d = self.domain[i]
            m = mats.get(d)
            if m is None or not np.isfinite(self.line[i]):
                continue
            key = (d, self.kind[i], self.line[i])
            pair = cache.get(key)
            if pair is None:
                pair = (asian_handicap(m, self.line[i]) if self.kind[i] == "ah"
                        else over_under(m, self.line[i]))
                cache[key] = pair
            out[i] = pair[0] if self.is_home[i] else pair[1]
        return out

    def sell_odds(self, theta) -> np.ndarray:
        """What we would put on the board: fair odds cut by the pool margin."""
        fair = self.fair_odds(theta)
        return np.where(fair > 0, fair / (1.0 + self.margin), 0.0)

    def expected_payout(self, odds: np.ndarray) -> float:
        """E[payout] = sum over selections of turnover x odds x true prob."""
        return float(np.nansum(self.turnover * odds * self.p_true))

    def expected_gm(self, odds: np.ndarray) -> float:
        return float(np.nansum(self.turnover)) - self.expected_payout(odds)


def parse_line(label) -> Optional[float]:
    """'2.5' / '2/2.5' / '-1.5/-2' -> float, quarter lines averaged."""
    if label is None:
        return None
    s = str(label).strip().replace(" ", "")
    if not s or s.lower() in ("nan", "none"):
        return None
    try:
        if "/" in s:
            parts = [float(x) for x in s.split("/") if x]
            return float(np.mean(parts)) if parts else None
        return float(s)
    except ValueError:
        return None
