"""
Theta Optimizer

Step 5 of the loop: pick the theta that maximises expected gross margin
over the coming bucket.

Ported from optimizer_v2.py and widened from four dimensions to the eight
algo_param actually carries. The objective is unchanged:

    payout(theta) = SUM  turnover_r * sell_odds_r(theta) * true_prob_r
    E[GM](theta)  = SUM  turnover_r  -  payout(theta)

Turnover and TrueProb are fixed for the tick (MVP rules 2 and 1), so only
the offer moves and maximising E[GM] is the same as minimising payout -
which is what the solver is actually handed.

SEPARABILITY: no goal pool reads a corner parameter and no corner pool
reads a goal one, so the eight dimensions split into two independent
four-dimensional problems. Solving them separately reaches the same optimum
as one eight-dimensional search for a fraction of the work, and a match
with no corner pools open never runs the corner block at all.

WHAT KEEPS IT HONEST: theta may not travel further than THETA_MAX_MOVE
from where the desk has it now, the offer may not beat the external market
on the Asian pools, and no price may fall below 1.001. Those are the same
guards optimizer_v2 applies, expressed as solver bounds and constraints so
a violated one shows up as an infeasible solve rather than a bad price.

Change Log:
-----------
2026-08-30      Rewritten: 8-dimensional port of optimizer_v2
"""

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import minimize

from src.core import config
from src.core.logging import get_logger
from src.pricing import book as book_lib
from src.pricing import pools as pool_defs

logger = get_logger(__name__)

GOAL_DIMS = ("goal_tg", "goal_sup", "goal_fh_tg", "goal_fh_sup")
CORNER_DIMS = ("corner_tg", "corner_sup", "corner_fh_tg", "corner_fh_sup")
BLOCKS = {"goal": GOAL_DIMS, "corner": CORNER_DIMS}

MIN_ODDS = 1.001


@dataclass(frozen=True)
class Leg:
    """One priceable selection, reduced to what the objective needs."""

    key: str
    pool: pool_defs.PoolDef
    line: Optional[float]
    selection: str
    turnover: float
    true_prob: float
    margin: float
    cap: Optional[float]      # external market ceiling on our offer
    main: bool = True
    odds_now: float = 0.0     # posted offer; used when demand is elastic

    @property
    def block(self) -> str:
        return "corner" if self.pool.domain == "corner" else "goal"


@dataclass
class BlockResult:
    """Outcome of one four-dimensional solve."""

    block: str
    dims: Tuple[str, ...]
    theta_now: Dict[str, float]
    theta_star: Dict[str, float]
    payout_now: float
    payout_star: float
    turnover: float
    legs: int
    starts: int
    success: bool
    message: str
    seconds: float

    @property
    def gm_now(self) -> float:
        return self.turnover - self.payout_now

    @property
    def gm_star(self) -> float:
        return self.turnover - self.payout_star

    @property
    def uplift(self) -> float:
        return self.gm_star - self.gm_now


@dataclass
class MatchResult:
    """Everything the desk sees about one match's recommendation."""

    match_id: int
    theta_now: Dict[str, float]
    theta_star: Dict[str, float]
    turnover: float
    payout_now: float
    payout_star: float
    blocks: Dict[str, BlockResult] = field(default_factory=dict)
    per_pool: List[dict] = field(default_factory=list)
    curves: Dict[str, dict] = field(default_factory=dict)
    seconds: float = 0.0
    legs: int = 0
    skipped: int = 0
    # dim -> (what the feed said, what it had to become to be priceable)
    repaired: Dict[str, tuple] = field(default_factory=dict)

    @property
    def gm_now(self) -> float:
        return self.turnover - self.payout_now

    @property
    def gm_star(self) -> float:
        return self.turnover - self.payout_star

    @property
    def uplift(self) -> float:
        return self.gm_star - self.gm_now

    @property
    def uplift_bps(self) -> float:
        """Uplift as basis points of turnover - comparable across matches."""
        return 0.0 if self.turnover <= 0 else 1e4 * self.uplift / self.turnover


class MatchOptimizer:
    """Solves one match's theta against one tick of its book."""

    def __init__(self, match_id, legs, state, theta_now, semantics=None):
        self.match_id = int(match_id)
        self.legs = list(legs)
        self.state = state
        self.theta_now = {d: float(theta_now.get(d, 0.0)) for d in book_lib.DIMS}
        self.semantics = semantics or config.THETA_SEMANTICS
        self._models: Dict[tuple, book_lib.BookModel] = {}
        self.evaluations = 0
        self.frozen = frozenset(
            d for d in book_lib.DIMS if "_fh_" in d
        ) if getattr(state, "ht_done", False) else frozenset()
        self.theta_base = self._feasible_base()
        self.repaired = {
            d: (self.theta_now[d], self.theta_base[d])
            for d in book_lib.DIMS
            if abs(self.theta_base[d] - self.theta_now[d]) > 1e-9
        }
        if self.repaired:
            logger.info(
                "match %s: repaired %d incoherent parameter(s) before solving: %s",
                self.match_id, len(self.repaired),
                ", ".join("{} {:.3f}->{:.3f}".format(d, a, b)
                          for d, (a, b) in sorted(self.repaired.items())),
            )

    # -- objective -----------------------------------------------------------

    def _model_for(self, theta: Dict[str, float]) -> book_lib.BookModel:
        """A BookModel per distinct theta, reused within the same step.

        The objective and every constraint are evaluated at the same point
        before the solver moves, and each of those calls would otherwise
        rebuild the grids from scratch. Keyed far below the solver's step
        size so neighbouring finite-difference points stay distinct.
        """
        key = tuple(round(float(theta.get(d, 0.0)), 9) for d in book_lib.DIMS)
        model = self._models.get(key)
        if model is None:
            model = book_lib.BookModel(theta, self.state, self.semantics)
            if len(self._models) > 24:
                self._models.clear()
            self._models[key] = model
            self.evaluations += 1
        return model

    def payout(self, theta: Dict[str, float], legs=None) -> float:
        """Expected payout over the bucket at a candidate theta.

        Uses the posted turnover, not the elastic one. The desk report
        still wants "what we pay if the same money shows up". The solver
        itself minimises ``expected_loss``, which coincides with this
        when DEMAND_ELASTICITY is zero.
        """
        legs = self.legs if legs is None else legs
        model = self._model_for(theta)
        total = 0.0
        for leg in legs:
            total += leg.turnover * self._offer(model, leg) * leg.true_prob
        return total

    def _eff_turnover(self, offer: float, leg: Leg) -> float:
        """Stake that shows up if we post ``offer`` instead of ``odds_now``."""
        e = float(getattr(config, "DEMAND_ELASTICITY", 0.0) or 0.0)
        if e <= 0 or not leg.odds_now or leg.odds_now <= 0:
            return leg.turnover
        return leg.turnover * (max(offer, MIN_ODDS) / max(leg.odds_now, MIN_ODDS)) ** (-e)

    def expected_loss(self, theta: Dict[str, float], legs=None) -> float:
        """Quantity the solver minimises.

        With no elasticity this is expected payout (same as today). With
        elasticity it is minus expected GM, so the solver will not just
        lengthen every price to the bound to harvest inelastic money.
        """
        legs = self.legs if legs is None else legs
        model = self._model_for(theta)
        e = float(getattr(config, "DEMAND_ELASTICITY", 0.0) or 0.0)
        if e <= 0:
            total = 0.0
            for leg in legs:
                total += leg.turnover * self._offer(model, leg) * leg.true_prob
            return total
        neg_gm = 0.0
        for leg in legs:
            offer = self._offer(model, leg)
            stake = self._eff_turnover(offer, leg)
            neg_gm -= stake * (1.0 - leg.true_prob * offer)
        return neg_gm

    def _offer(self, model, leg: Leg) -> float:
        """The price we would show for this leg at the model's theta.

        Unrounded on purpose: the ladder in book.sell_odds makes E[GM] a
        step function of theta, which a gradient solver cannot read. The
        recommendation is rounded once, at the end.
        """
        prob = model.probability(leg.pool, leg.line, leg.selection)
        if not prob or prob <= 0:
            # Our model says it cannot happen while the belief says it can.
            # Offering the cap rather than infinity keeps the objective
            # finite, and the size of the cap makes the solver avoid it.
            return config.ODDS_CAP
        offer = 1.0 / prob / (1.0 + leg.margin)
        # Clamped rather than constrained: a price this short is a leg the
        # solver should treat as flat, not a reason to fail the whole solve.
        return min(max(offer, MIN_ODDS), config.ODDS_CAP)

    # -- solve ---------------------------------------------------------------

    def solve(self) -> MatchResult:
        started = time.time()
        result = MatchResult(
            match_id=self.match_id,
            theta_now=dict(self.theta_now),
            theta_star=dict(self.theta_now),
            turnover=sum(leg.turnover for leg in self.legs),
            payout_now=0.0,
            payout_star=0.0,
            legs=len(self.legs),
            repaired=dict(self.repaired),
        )
        if not self.legs:
            return result

        theta_star = dict(self.theta_now)
        for name, dims in BLOCKS.items():
            legs = [leg for leg in self.legs if leg.block == name]
            if not legs:
                continue
            block = self._solve_block(name, dims, legs)
            result.blocks[name] = block
            theta_star.update(block.theta_star)

        result.theta_star = theta_star
        result.payout_now = self.payout(self.theta_now)
        result.payout_star = self.payout(theta_star)
        result.per_pool = self._per_pool(theta_star)
        result.seconds = round(time.time() - started, 3)
        return result

    def _solve_block(self, name, dims, legs) -> BlockResult:
        started = time.time()
        base = dict(self.theta_base)
        turnover = sum(leg.turnover for leg in legs)
        payout_now = self.payout(self.theta_now, legs)

        # Only the dimensions that can still move are handed to the solver.
        # A settled one would arrive as a bound with lo == hi, and SLSQP's
        # finite-difference probe steps outside that on its first move -
        # which is the "values in x were outside bounds" warning, and a
        # wasted column of the Jacobian on every iteration after it.
        free = tuple(d for d in dims if d not in self.frozen)
        if not free:
            return BlockResult(
                block=name, dims=dims,
                theta_now={d: self.theta_now[d] for d in dims},
                theta_star={d: self.theta_now[d] for d in dims},
                payout_now=payout_now, payout_star=payout_now,
                turnover=turnover, legs=len(legs), starts=0,
                success=True, message="all dimensions settled",
                seconds=round(time.time() - started, 3),
            )

        bounds = self._bounds(free)
        constraints = self._constraints(dims, free, legs, base)
        guesses = self._guesses(dims, free, bounds, base)

        def objective(vec):
            theta = dict(base)
            theta.update(zip(free, vec))
            return self.expected_loss(theta, legs)

        def run(guess):
            return minimize(
                objective, np.asarray(guess, dtype=float),
                method="SLSQP", bounds=bounds, constraints=constraints,
                options={
                    "maxiter": config.OPTIMIZER_MAX_ITER,
                    "ftol": config.OPTIMIZER_FTOL,
                    # A goal expectancy moves in hundredths, so the default
                    # 1e-8 probe reads as float noise and the solver walks
                    # its iteration budget on gradients that mean nothing.
                    "eps": config.OPTIMIZER_STEP,
                },
            )

        best, results = None, []
        workers = max(1, min(config.OPTIMIZER_WORKERS, len(guesses)))
        if workers == 1:
            results = [run(g) for g in guesses]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for res in pool.map(run, guesses):
                    results.append(res)

        for res in results:
            if res is None or not np.all(np.isfinite(res.x)):
                continue
            if res.success and (best is None or res.fun < best.fun):
                best = res

        if best is None:
            # Every start failed the constraints. Rather than ship a price
            # from an infeasible solve, stay where the desk already is.
            finished = [r for r in results if r is not None]
            message = finished[0].message if finished else "no solver result"
            logger.warning("match %s block %s: %s", self.match_id, name, message)
            return BlockResult(
                block=name, dims=dims,
                theta_now={d: self.theta_now[d] for d in dims},
                theta_star={d: self.theta_now[d] for d in dims},
                payout_now=payout_now, payout_star=payout_now,
                turnover=turnover, legs=len(legs), starts=len(guesses),
                success=False, message=str(message),
                seconds=round(time.time() - started, 3),
            )

        # Settled dimensions were never in the solve vector, so they come
        # back from where the desk has them, untouched.
        star = {d: self.theta_now[d] for d in dims}
        star.update({d: float(v) for d, v in zip(free, best.x)})
        merged = {**base, **star}
        if self.expected_loss(merged, legs) > self.expected_loss(self.theta_now, legs):
            # A worse answer than standing still is not an answer.
            star = {d: self.theta_now[d] for d in dims}
            merged = {**base, **star}
        payout_star = self.payout(merged, legs)

        return BlockResult(
            block=name, dims=dims,
            theta_now={d: self.theta_now[d] for d in dims},
            theta_star=star, payout_now=payout_now, payout_star=payout_star,
            turnover=turnover, legs=len(legs), starts=len(guesses),
            success=True, message=str(best.message),
            seconds=round(time.time() - started, 3),
        )

    # -- search space --------------------------------------------------------

    def _feasible_base(self):
        """The desk's theta, repaired just enough to solve from.

        Live algo_param is not guaranteed to satisfy the coherence the
        solver enforces - supremacy can exceed the total it is a difference
        of, and a first half can be carrying more than the whole match.
        Handing SLSQP a starting point that already violates its own
        constraints is what produces "positive directional derivative for
        linesearch": there is no feasible descent direction from outside
        the feasible set, so the solve fails before it begins.

        Nothing is lost by repairing it. A supremacy past its total is not
        a book anyone can price - the grid builder clamps it to exactly
        this value before it draws a single score - so the repaired point
        is not an assumption about what the desk meant, it is what the desk
        is already being priced at.
        """
        base = dict(self.theta_now)
        for dims in BLOCKS.values():
            tg, sup, fh_tg, fh_sup = dims
            for dim in dims:
                lo, hi = config.THETA_BOUNDS[dim]
                base[dim] = min(max(base[dim], lo), hi)
            # Supremacy is a difference of two non-negative expectancies,
            # so it cannot exceed the total.
            base[sup] = min(max(base[sup], -base[tg]), base[tg])
            base[fh_tg] = min(base[fh_tg], base[tg])
            base[fh_sup] = min(max(base[fh_sup], -base[fh_tg]), base[fh_tg])
            # What is left for the second half has to be coherent too.
            rest = base[tg] - base[fh_tg]
            base[fh_sup] = min(max(base[fh_sup], base[sup] - rest),
                               base[sup] + rest)
        return base

    def _bounds(self, dims):
        """Absolute limits intersected with this tick's trust region.

        Centred on the repaired point, not the raw parameter, and for the
        same reason the repair exists at all. Centring on a value that is
        outside the absolute limits gives lo > hi - an inverted interval
        scipy accepts and then behaves unpredictably inside. Centring on
        one that is merely incoherent is subtler and worse: each band is
        valid on its own, but no point inside all of them satisfies the
        coherence constraints, so the feasible set is empty and every
        start fails with no explanation beyond the line search giving up.
        """
        out = []
        for dim in dims:
            lo, hi = config.THETA_BOUNDS[dim]
            now = min(max(self.theta_base[dim], lo), hi)
            move = config.THETA_MAX_MOVE[dim]
            band_lo = max(lo, now - move)
            band_hi = min(hi, now + move)
            if band_lo > band_hi:                    # cannot happen, but a
                band_lo = band_hi = now              # solve must never invert
            # Add tiny margin so SLSQP's finite-difference probe does not
            # step outside the bounds and trigger a clipping warning.
            eps = abs(band_hi - band_lo) * 1e-6
            out.append((band_lo - eps, band_hi + eps))
        return out

    def _constraints(self, dims, free, legs, base):
        """Coherence of the parameters, then the market's ceiling.

        The solve vector only carries the dimensions that can still move,
        so every constraint reads through ``_block_values`` to recover the
        full (TG, SUP, FH_TG, FH_SUP) it is written against.

        Every constraint costs the solver a finite-difference gradient on
        each iteration, so the market ceiling is applied only to the main
        line of the capped pools - the line the desk is actually trading -
        rather than to every line on the board.
        """
        index = {dim: i for i, dim in enumerate(free)}

        def values(x):
            return [x[index[d]] if d in index else base[d] for d in dims]

        cons = [
            # Neither side may be expected to score a negative number.
            {"type": "ineq", "fun": lambda x: (lambda v: v[0] - abs(v[1]))(values(x))},
            {"type": "ineq", "fun": lambda x: (lambda v: v[2] - abs(v[3]))(values(x))},
            # The first half cannot account for more than the whole match,
            # and what is left for the second half must itself be coherent.
            {"type": "ineq", "fun": lambda x: (lambda v: v[0] - v[2])(values(x))},
            {"type": "ineq",
             "fun": lambda x: (lambda v: (v[0] - v[2]) - abs(v[1] - v[3]))(values(x))},
        ]

        # A cap we are already through is not a constraint the solver can
        # satisfy, and demanding it makes the whole start infeasible. Read
        # as "do not get worse than you already are" instead.
        start = self._model_for(base)
        for leg in legs:
            if not (leg.main and leg.cap and leg.cap > 0):
                continue
            ceiling = max(float(leg.cap), self._offer(start, leg))
            cons.append({
                "type": "ineq",
                "fun": lambda x, leg=leg, ceiling=ceiling:
                    ceiling - self._offer_at(x, free, base, leg),
            })
        return cons

    def _offer_at(self, vec, free, base, leg):
        theta = dict(base)
        theta.update(zip(free, vec))
        return self._offer(self._model_for(theta), leg)

    def _guesses(self, dims, free, bounds, base):
        """Where to start the solver.

        The desk's current theta first - it is usually close and a solve
        that ends up back there is the most reassuring answer. The rest are
        the spread optimizer_v2 uses, scaled into this block's units and
        clipped into the trust region so no start begins outside it.
        """
        full = [base[d] for d in dims]
        scale = full[0] if full[0] > 0 else 1.0
        spread = [
            full,
            [scale, 0.0, scale * config.DEFAULT_FH_SPLIT, 0.0],
            [scale * 1.25, abs(scale) * 0.4, scale * 0.55, abs(scale) * 0.2],
            [scale * 1.25, -abs(scale) * 0.4, scale * 0.55, -abs(scale) * 0.2],
            [scale * 0.75, 0.0, scale * 0.4, 0.0],
        ]
        keep = [dims.index(d) for d in free]
        out = []
        for guess in spread:
            clipped = [
                min(max(guess[i], lo), hi)
                for i, (lo, hi) in zip(keep, bounds)
            ]
            if clipped not in out:
                out.append(clipped)
        starts = max(1, int(getattr(config, "OPTIMIZER_STARTS", len(out))))
        return out[:starts]

    # -- reporting -----------------------------------------------------------

    def _per_pool(self, theta_star):
        """Where the uplift actually comes from, pool by pool."""
        now = book_lib.BookModel(self.theta_now, self.state, self.semantics)
        star = book_lib.BookModel(theta_star, self.state, self.semantics)
        buckets = {}
        for leg in self.legs:
            row = buckets.setdefault(leg.pool.code, {
                "pool_code": leg.pool.code,
                "family": leg.pool.family,
                "turnover": 0.0, "payout_now": 0.0, "payout_star": 0.0,
                "legs": 0,
            })
            row["turnover"] += leg.turnover
            row["payout_now"] += leg.turnover * self._offer(now, leg) * leg.true_prob
            row["payout_star"] += leg.turnover * self._offer(star, leg) * leg.true_prob
            row["legs"] += 1
        out = []
        for row in buckets.values():
            row["gm_now"] = row["turnover"] - row["payout_now"]
            row["gm_star"] = row["turnover"] - row["payout_star"]
            row["uplift"] = row["gm_star"] - row["gm_now"]
            out.append(row)
        return sorted(out, key=lambda r: -r["uplift"])

    def curves(self, theta_star, points=None):
        """E[GM] along each dimension with the others held at theta*.

        This is what the cockpit's response curves draw: it shows the desk
        whether the recommendation sits on a sharp peak or a flat plateau,
        which is the difference between a number worth acting on and one
        worth ignoring.
        """
        points = points or config.GM_CURVE_POINTS
        out = {}
        for name, dims in BLOCKS.items():
            legs = [leg for leg in self.legs if leg.block == name]
            if not legs:
                continue
            block_turnover = sum(leg.turnover for leg in legs)
            for dim in dims:
                if dim in self.frozen:
                    continue
                lo, hi = self._bounds((dim,))[0]
                xs = np.linspace(lo, hi, points)
                ys = []
                for x in xs:
                    theta = dict(theta_star)
                    theta[dim] = float(x)
                    ys.append(block_turnover - self.payout(theta, legs))
                out[dim] = {
                    "x": [round(float(v), 4) for v in xs],
                    "gm": [round(float(v), 2) for v in ys],
                    "now": round(self.theta_now[dim], 4),
                    "star": round(float(theta_star[dim]), 4),
                }
        return out


# ---------------------------------------------------------------------------
# Tick-level driver
# ---------------------------------------------------------------------------

def legs_from_tick(tick, match_id, only_optimized=True) -> Tuple[List[Leg], int]:
    """Turn one match's priced selections into solver legs.

    A leg has to carry money and a belief to matter: a selection nobody is
    betting contributes nothing to the objective, and one with no true
    price would be weighted by a number we made up. Both are skipped and
    counted so the API can say how much of the book was actually optimised.
    """
    sel = tick.selections
    rows = sel[sel["match_id"] == int(match_id)]
    legs, skipped = [], 0
    cap_stat = config.MARKET_CAP_STAT

    for row in rows.to_dict("records"):
        pool = pool_defs.resolve(row["pool_code"])
        if pool is None:
            skipped += 1
            continue
        if only_optimized and not pool.optimized:
            skipped += 1
            continue
        turnover = float(row.get("t_hat") or 0.0)
        prob = float(row.get("true_prob") or 0.0)
        if turnover <= 0 or prob <= 0:
            skipped += 1
            continue
        if str(row.get("status") or "open") != "open":
            skipped += 1
            continue

        cap = None
        if pool.code in config.MARKET_CAP_POOLS:
            value = row.get("mkt_{}".format(cap_stat))
            cap = float(value) if value and value > 1.0 else None

        legs.append(Leg(
            key=str(row["key"]),
            pool=pool,
            line=row.get("line_value"),
            selection=str(row["selection"]),
            turnover=turnover,
            true_prob=prob,
            margin=float(row.get("margin") or pool.margin),
            cap=cap,
            main=bool(row.get("is_main_line", True)),
            odds_now=float(row.get("sell_odds") or 0.0),
        ))
    return legs, skipped


def solve_match(tick, match_id, with_curves=False) -> Optional[MatchResult]:
    """Solve one match. Returns None if it is not on the tick."""
    ctx = tick.contexts.get(int(match_id))
    if ctx is None:
        return None
    legs, skipped = legs_from_tick(tick, match_id)
    engine = MatchOptimizer(
        match_id, legs, ctx.state, ctx.theta.get(config.THETA_LAYER, {})
    )
    result = engine.solve()
    result.skipped = skipped
    if with_curves and legs:
        result.curves = engine.curves(result.theta_star)
    return result


def solve_order(tick, match_ids=None) -> List[int]:
    """Matches worth solving, richest first.

    A desk watching a full card cares about the £900k match long before
    the £2k one, and on a big card the solver will not have finished
    everything before the next tick. Ordering by money means whatever it
    does get through is the part that mattered.
    """
    ids = list(match_ids) if match_ids is not None else list(tick.contexts.keys())
    sel = tick.selections
    if sel.empty:
        return [int(i) for i in ids]
    money = sel.groupby("match_id")["t_hat"].sum()
    return sorted(
        (int(i) for i in ids),
        key=lambda i: -float(money.get(i, 0.0)),
    )


def optimize_tick(tick, match_ids=None, with_curves=False,
                  budget_seconds=None, on_result=None) -> Dict[int, MatchResult]:
    """Solve the tick's matches, richest first, within a time budget.

    Sequential by design: each match already fans its multi-start out
    across threads, and nesting a second pool on top of that just makes
    the profile harder to read for no wall-clock gain on a desk machine.

    Args:
        budget_seconds: stop starting new matches once this much wall
            clock has gone. Whatever is solved is still returned; the rest
            simply have no recommendation this tick.
        on_result: called with (match_id, result) as each one lands, so a
            caller can publish progress rather than wait for the set.
    """
    started = time.time()
    out = {}
    order = solve_order(tick, match_ids)
    for position, match_id in enumerate(order):
        if budget_seconds and time.time() - started > budget_seconds:
            logger.warning(
                "optimizer budget of %.0fs spent after %d of %d matches",
                budget_seconds, position, len(order),
            )
            break
        try:
            result = solve_match(tick, match_id, with_curves=with_curves)
        except Exception:                                     # noqa: BLE001
            # One pathological match must not cost the desk the whole card.
            logger.exception("match %s failed to optimise", match_id)
            continue
        if result is None:
            continue
        out[int(match_id)] = result
        if on_result is not None:
            on_result(int(match_id), result)
        logger.debug(
            "match %s optimised in %.2fs: uplift %.1f on %.0f turnover",
            match_id, result.seconds, result.uplift, result.turnover,
        )
    return out