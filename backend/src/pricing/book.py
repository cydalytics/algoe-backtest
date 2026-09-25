"""
Book Pricing

Turns an 8-dimensional theta plus the current match state into a set of
score grids, then resolves any pool/line/selection against them.

The eight dimensions are the ones algo_param carries and the ones the
cockpit's theta rail shows:

    goal_tg   goal_sup   goal_fh_tg   goal_fh_sup
    corner_tg corner_sup corner_fh_tg corner_fh_sup

Under THETA_SEMANTICS = "remaining" these describe what is still to come,
so the grids are shifted by whatever is already on the board. Pre-match the
two readings coincide; in-play they do not, which is why the choice is
config-driven and reported by the API.

Change Log:
-----------
2026-08-30      Initialize
"""

import math
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from src.core import config
from src.pricing import grids, pools

DIMS = (
    "goal_tg", "goal_sup", "goal_fh_tg", "goal_fh_sup",
    "corner_tg", "corner_sup", "corner_fh_tg", "corner_fh_sup",
)

DIM_LABELS = {
    "goal_tg": "TG FT",
    "goal_sup": "SUP FT",
    "goal_fh_tg": "TG HT",
    "goal_fh_sup": "SUP HT",
    "corner_tg": "TC FT",
    "corner_sup": "CSUP FT",
    "corner_fh_tg": "TC HT",
    "corner_fh_sup": "CSUP HT",
}

# Which cockpit theta key each dimension maps onto.
DIM_KEYS = {
    "goal_tg": "tg", "goal_sup": "sup",
    "goal_fh_tg": "tg", "goal_fh_sup": "sup",
    "corner_tg": "tc", "corner_sup": "csup",
    "corner_fh_tg": "tc", "corner_fh_sup": "csup",
}

TTG_CAP = 7
CRS_CAP = 4


@dataclass
class MatchState:
    """Everything about where the match stands that changes a price."""

    home_score: int = 0
    away_score: int = 0
    ht_home: Optional[int] = None
    ht_away: Optional[int] = None
    home_corner: int = 0
    away_corner: int = 0
    ht_home_corner: Optional[int] = None
    ht_away_corner: Optional[int] = None
    minute: float = 0.0
    phase: str = "prematch"

    @property
    def ht_done(self) -> bool:
        """True once the first half is over, whatever the clock says."""
        return self.phase in (
            "half_time", "second_half", "et_first_half",
            "et_half_time", "et_second_half", "penalty_shootout", "full_time",
        )

    @property
    def in_play(self) -> bool:
        return self.phase not in ("prematch", "full_time")

    def half_time_score(self) -> Tuple[int, int]:
        """Half-time score, falling back to the live score before the break."""
        if self.ht_done and self.ht_home is not None and self.ht_away is not None:
            return int(self.ht_home), int(self.ht_away)
        return int(self.home_score), int(self.away_score)

    def half_time_corners(self) -> Tuple[int, int]:
        if self.ht_done and self.ht_home_corner is not None:
            return int(self.ht_home_corner), int(self.ht_away_corner or 0)
        return int(self.home_corner), int(self.away_corner)


def theta_vector(values: Dict[str, float]) -> Tuple[float, ...]:
    """Dict -> the canonical 8-tuple the optimizer works in."""
    return tuple(float(values.get(d, 0.0)) for d in DIMS)


def theta_dict(vector) -> Dict[str, float]:
    """The canonical 8-tuple -> dict."""
    return {d: float(v) for d, v in zip(DIMS, vector)}


class BookModel:
    """Score grids for one match at one theta.

    Grids are built lazily: a match with no corner pools open never pays
    for a corner convolution, which matters because the optimizer rebuilds
    this object on every objective evaluation.
    """

    def __init__(self, theta, state: MatchState, semantics: str = None):
        self.theta = theta_dict(theta) if not isinstance(theta, dict) else dict(theta)
        self.state = state
        self.semantics = semantics or config.THETA_SEMANTICS
        self._grids: Dict[str, object] = {}
        self._markets: Dict[tuple, dict] = {}

    # -- grid construction ---------------------------------------------------

    def _build_domain(self, domain: str):
        """Half-time, second-half and full-time grids for one domain."""
        if domain == "goal":
            tg, sup = self.theta["goal_tg"], self.theta["goal_sup"]
            fh_tg, fh_sup = self.theta["goal_fh_tg"], self.theta["goal_fh_sup"]
            depth = config.MAX_GOALS
            live_h, live_a = self.state.home_score, self.state.away_score
            ht_h, ht_a = self.state.half_time_score()
        else:
            tg, sup = self.theta["corner_tg"], self.theta["corner_sup"]
            fh_tg, fh_sup = self.theta["corner_fh_tg"], self.theta["corner_fh_sup"]
            depth = config.MAX_CORNERS
            live_h, live_a = self.state.home_corner, self.state.away_corner
            ht_h, ht_a = self.state.half_time_corners()

        # Second-half expectation is whatever the full-match figure has left
        # once the first-half slice is taken out.
        sh_tg = max(tg - fh_tg, 0.0)
        sh_sup = sup - fh_sup

        if self.semantics == "full_match":
            ht_grid = grids.score_grid(fh_tg, fh_sup, depth)
            sh_grid = grids.score_grid(sh_tg, sh_sup, depth)
        elif self.state.ht_done:
            # The first half is settled, so it contributes a point mass and
            # everything still to come lands in the second half - on top of
            # whatever the second half has already produced.
            ht_grid = grids.point_grid(ht_h, ht_a)
            sh_grid = grids.shift_grid(
                grids.score_grid(tg, sup, depth),
                max(live_h - ht_h, 0),
                max(live_a - ht_a, 0),
            )
        else:
            ht_grid = grids.shift_grid(
                grids.score_grid(fh_tg, fh_sup, depth), live_h, live_a
            )
            sh_grid = grids.score_grid(sh_tg, sh_sup, depth)

        ft_grid = grids.convolve_grids(ht_grid, sh_grid)
        self._grids[domain + ":HT"] = ht_grid
        self._grids[domain + ":SH"] = sh_grid
        self._grids[domain + ":FT"] = ft_grid

    def grid(self, domain: str, seg: str):
        """Grid for one domain/segment, building the domain on first use."""
        key = "{}:{}".format(domain, seg)
        if key not in self._grids:
            self._build_domain(domain)
        return self._grids[key]

    # -- market resolution ---------------------------------------------------

    def market(self, pool: pools.PoolDef, line: Optional[float]) -> dict:
        """All selection probabilities for one pool/line, memoised."""
        key = (pool.kind, pool.domain, pool.seg, line)
        cached = self._markets.get(key)
        if cached is not None:
            return cached

        seg = "HT" if pool.seg == "HT" else "FT"
        grid = self.grid(pool.domain, seg)

        if pool.kind == "three_way":
            out = grids.three_way(grid)
        elif pool.kind == "handicap_3w":
            out = grids.three_way_handicap(grid, line or 0.0)
        elif pool.kind == "asian_hcp":
            out = grids.asian_handicap(grid, line or 0.0)
        elif pool.kind == "asian_total":
            out = grids.over_under(grid, line or 0.0)
        elif pool.kind == "odd_even":
            out = grids.odd_even(grid)
        elif pool.kind == "exact_total":
            out = grids.exact_total(grid, TTG_CAP)
        elif pool.kind == "correct_score":
            out = grids.correct_score(grid, CRS_CAP)
        elif pool.kind == "half_full":
            out = grids.half_full(
                self.grid(pool.domain, "HT"), self.grid(pool.domain, "SH")
            )
        else:
            out = {}

        # Convolution and the 1 - covered residual can both land a hair
        # below zero; a negative probability would come back as a negative
        # price further down.
        out = {k: max(float(v), 0.0) for k, v in out.items() if k != "detail"}
        self._markets[key] = out
        return out

    def probability(self, pool: pools.PoolDef, line, selection) -> Optional[float]:
        """Fair probability for one selection, or None if unresolvable."""
        market = self.market(pool, line)
        value = market.get(selection)
        if value is None:
            return None
        return float(value)


# ---------------------------------------------------------------------------
# Odds
# ---------------------------------------------------------------------------

def fair_odds(prob: Optional[float]) -> float:
    """Zero-margin decimal odds."""
    if not prob or prob <= 0:
        return 0.0
    return min(1.0 / prob, config.ODDS_CAP)


def sell_odds(fair: float, margin: float, weighting: float = 1.0) -> float:
    """Apply the pool margin and the desk's selection weighting.

    Mirrors optimizer_v2.get_sell_odds: the offer is the fair price divided
    by ``1 + margin``. The weighting is a desk multiplier on top - above 1
    lengthens the price and gives value away, below 1 shortens it.
    """
    if fair <= 0:
        return 0.0
    raw = fair / (1.0 + float(margin)) * float(weighting or 1.0)
    return ladder(raw)


def ladder(odds: float) -> float:
    """Round to the price ladder the feed actually quotes on."""
    if odds <= 0:
        return 0.0
    if odds >= config.ODDS_CAP:
        return config.ODDS_CAP
    if odds < 4:
        step = 0.01
    elif odds < 10:
        step = 0.05
    elif odds < 30:
        step = 0.5
    elif odds < 100:
        step = 1.0
    else:
        step = 5.0
    value = math.floor(odds / step + 1e-9) * step
    return max(config.ODDS_FLOOR, round(value, 2))


def demarginalize(odds_list):
    """Strip an overround from a set of prices that should sum to one."""
    total = sum((1.0 / o) for o in odds_list if o and o > 0)
    if total <= 0:
        return [0.0 for _ in odds_list]
    return [(o * total) if o and o > 0 else 0.0 for o in odds_list]


def selection_status(pool: pools.PoolDef, prob: Optional[float],
                     state: MatchState, feed_status: str = "open") -> str:
    """Whether a selection is still tradable this tick.

    Returns one of ``open``, ``settled``, ``closed`` or ``suspended``.
    A first-half market after the break is settled; an outcome the score
    has already ruled out is settled; a price nobody would write is closed.
    """
    if feed_status and feed_status.lower() in ("suspended", "sell_stopped", "closed"):
        return "suspended"
    if pool.seg == "HT" and state.ht_done:
        return "settled"
    if prob is None:
        return "closed"
    if prob <= 0:
        return "settled"
    if fair_odds(prob) > config.MAX_SELLABLE_ODDS:
        return "closed"
    return "open"


def implied_probs(odds_list):
    """Normalised implied probabilities from a set of quoted prices."""
    probs = [(1.0 / o) if o and o > 0 else 0.0 for o in odds_list]
    total = sum(probs)
    if total <= 0:
        return probs
    return [p / total for p in probs]
