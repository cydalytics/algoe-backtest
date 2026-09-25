"""Per-bucket theta optimisation, and the four-way comparison that follows.

For each (match, 5-min bucket) it answers, in order:

  1. what TG/SUP maximises expected gross margin for the money about to arrive
  2. what prices that theta would have put on the board
  3. how those prices compare with the ones HKJC actually opened
  4. how the expected margin compares with what HKJC's board actually earned

Objective is the one from `optimizer_v2b.py`:

    minimise  sum over selections of  turnover x sell_odds(theta) x p_true
    so that   total_goals >= |supremacy|  and  sell_odds >= 1.001

Turnover is constant in theta, so minimising expected payout maximises expected
gross margin.

Goal pools and corner pools are solved **separately**. A corner line's price does
not depend on goal expectancy, so the 4-parameter problem in optimizer_v2b is
really two independent 2-parameter problems. Splitting them is both faster and
less prone to the solver stalling in a corner of a flat 4-D surface.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .config import Config
from .pricing import POOL_KIND, Book, parse_line

try:
    from scipy.optimize import minimize
    HAVE_SCIPY = True
except Exception:                                  # pragma: no cover
    HAVE_SCIPY = False

# fallbacks when HKJC's own parameters are missing for a match
DEFAULT_THETA = {"goal": (2.5, 0.0), "corner": (9.5, 0.0),
                 "goal_fh": (1.35, 0.0), "corner_fh": (4.5, 0.0)}
MIN_ODDS = 1.001
# multi-start guesses, as offsets applied to the warm start
STARTS = [(0.0, 0.0), (-0.6, 0.0), (0.6, 0.0), (0.0, -0.5), (0.0, 0.5)]


def theta_bounds(domain: str) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    if domain.startswith("corner"):
        return (2.0, 20.0), (-8.0, 8.0)
    if domain.endswith("_fh"):
        return (0.2, 5.0), (-3.0, 3.0)
    return (0.3, 8.0), (-5.0, 5.0)


def build_book(g: pd.DataFrame, cfg: Config) -> Optional[Book]:
    """One match-bucket of the panel -> a repriceable Book.

    Rows are dropped when they cannot be priced at all: unknown pool, unparseable
    line, or no true probability to value the payout with.
    """
    pool, domain, kind, line, is_home, margin = [], [], [], [], [], []
    keep = []
    for i, r in enumerate(g.itertuples(index=False)):
        pn = str(getattr(r, "pool_name", "") or "")
        spec = POOL_KIND.get(pn)
        if spec is None:
            continue
        lv = parse_line(getattr(r, "line_label", None))
        if lv is None:
            continue
        p = getattr(r, "p_true", np.nan)
        if not np.isfinite(p) or p <= 0 or p >= 1:
            continue
        cs = str(getattr(r, "combination_string", "") or "").upper()
        cid = getattr(r, "combination_id", None)
        home = cs.startswith("H") or (cs == "" and cid == 1)
        keep.append(i)
        pool.append(pn)
        domain.append(spec[0])
        kind.append(spec[1])
        line.append(lv if spec[1] == "ou" else _home_handicap(lv, cfg))
        is_home.append(home)
        margin.append(cfg.margin_for(pn))
    if not keep:
        return None
    sub = g.iloc[keep]
    return Book(pool, domain, kind, line, is_home,
                sub["turnover_forecast"].to_numpy(dtype="float64"),
                sub["p_true"].to_numpy(dtype="float64"),
                sub["odds"].to_numpy(dtype="float64"),
                margin), sub


def _home_handicap(line_value: float, cfg: Config) -> float:
    """HKJC's HDC/CHDC line_label read as the home handicap.

    The sign convention is not documented in the extraction notebooks, so it is
    a config switch and the report measures it: `--hdc-sign flip` mirrors it, and
    the pricer replication check reports the error under both so you can see
    which one reproduces HKJC's board.
    """
    return -line_value if cfg.hdc_sign == "flip" else line_value


def solve_domain(book: Book, domain: str, warm: Tuple[float, float],
                 n_starts: int = 3) -> Tuple[float, float, bool]:
    """Maximise expected GM over (tg, sup) for the selections of one domain."""
    idx = np.array([d == domain for d in book.domain])
    if not idx.any():
        return warm[0], warm[1], False
    sub = _subset(book, idx)
    (tg_lo, tg_hi), (sup_lo, sup_hi) = theta_bounds(domain)

    def payout(x) -> float:
        tg, sup = float(x[0]), float(x[1])
        odds = sub.sell_odds({domain: (tg, sup)})
        # an unpriceable or sub-minimum price is not a tradable book
        if np.any((odds > 0) & (odds < MIN_ODDS)):
            return 1e18
        if np.any(odds <= 0):
            return 1e18
        return sub.expected_payout(odds)

    if not HAVE_SCIPY:
        return _grid_search(payout, warm, (tg_lo, tg_hi), (sup_lo, sup_hi)) + (True,)

    cons = [
        {"type": "ineq", "fun": lambda x: x[0] - abs(x[1])},   # tg >= |sup|
    ]
    best_x, best_f, ok = None, np.inf, False
    for dtg, dsup in STARTS[:max(1, n_starts)]:
        g0 = [float(np.clip(warm[0] + dtg, tg_lo, tg_hi)),
              float(np.clip(warm[1] + dsup, sup_lo, sup_hi))]
        if g0[0] < abs(g0[1]):
            g0[0] = abs(g0[1]) + 0.1
        try:
            res = minimize(payout, g0, method="SLSQP", constraints=cons,
                           bounds=[(tg_lo, tg_hi), (sup_lo, sup_hi)],
                           options={"maxiter": 60, "ftol": 1e-7})
        except Exception:
            continue
        if res.success and res.fun < best_f:
            best_f, best_x, ok = res.fun, res.x, True
    if best_x is None:
        tg, sup = _grid_search(payout, warm, (tg_lo, tg_hi), (sup_lo, sup_hi))
        return tg, sup, False
    return float(best_x[0]), float(best_x[1]), ok


def _grid_search(payout, warm, tg_rng, sup_rng, steps: int = 13):
    """Coarse-to-fine grid, used when SLSQP fails or scipy is unavailable."""
    best = (warm[0], warm[1])
    best_f = payout([warm[0], warm[1]])
    tg_lo, tg_hi = tg_rng
    sup_lo, sup_hi = sup_rng
    span_tg, span_sup = (tg_hi - tg_lo) / 4, (sup_hi - sup_lo) / 4
    for _ in range(3):
        tgs = np.linspace(max(best[0] - span_tg, tg_lo), min(best[0] + span_tg, tg_hi), steps)
        sups = np.linspace(max(best[1] - span_sup, sup_lo), min(best[1] + span_sup, sup_hi), steps)
        for tg in tgs:
            for sup in sups:
                if tg < abs(sup):
                    continue
                f = payout([tg, sup])
                if f < best_f:
                    best_f, best = f, (float(tg), float(sup))
        span_tg /= 3
        span_sup /= 3
    return best


def _subset(book: Book, mask: np.ndarray) -> Book:
    return Book(book.pool[mask], book.domain[mask], book.kind[mask],
                book.line[mask], book.is_home[mask], book.turnover[mask],
                book.p_true[mask], book.actual_odds[mask], book.margin[mask])


def hkjc_theta(g: pd.DataFrame) -> Dict[str, Tuple[float, float]]:
    """HKJC's own parameters for this bucket, with documented fallbacks."""
    def val(col, default):
        if col not in g.columns:
            return default
        s = pd.to_numeric(g[col], errors="coerce").dropna()
        return float(s.iloc[0]) if len(s) else default

    tg = val("tg", DEFAULT_THETA["goal"][0])
    sup = val("sup", DEFAULT_THETA["goal"][1])
    ctg = val("ctg", DEFAULT_THETA["corner"][0])
    csup = val("csup", DEFAULT_THETA["corner"][1])
    fh = val("goal_split_fh", 0.5)
    cfh = val("corner_split_fh", 0.5)
    return {"goal": (tg, sup), "corner": (ctg, csup),
            "goal_fh": (tg * fh, sup * fh), "corner_fh": (ctg * cfh, csup * cfh)}


# ------------------------------------------------------------------- driver
class OptimizerStage:
    """Runs the solve over sampled buckets and accumulates the comparison.

    Sampling is not a shortcut taken lightly: a full pass is one SLSQP solve per
    match-bucket, which over 2.5 months is millions of solves. `--optimize-every`
    keeps every Nth bucket and `--optimize-max-buckets` caps the total, so the
    stage finishes in a known time. Everything it reports is therefore an
    estimate over the sampled buckets, and the report says so.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.rows: List[dict] = []
        self.prices: List[dict] = []
        self.warm: Dict[int, Dict[str, Tuple[float, float]]] = {}
        self.n_seen = 0
        self.n_solved = 0
        self.n_failed = 0

    @property
    def full(self) -> bool:
        return len(self.rows) >= self.cfg.optimize_max_buckets

    def update(self, facts: pd.DataFrame) -> None:
        if facts is None or facts.empty or self.full:
            return
        f = facts[facts["pool_name"].isin(POOL_KIND.keys())].copy()
        if f.empty:
            return
        f["turnover_forecast"] = f[self.cfg.optimize_demand].fillna(0.0)
        if self.cfg.optimize_belief == "p_sell":
            book = f["sell_book_sum"].to_numpy(dtype="float64")
            f["p_true"] = np.where(book > 0, f["p_sell"].to_numpy(dtype="float64") / book, np.nan)

        # only buckets with money to price for; a book with no demand has a
        # degenerate objective and tells us nothing
        f = f[f.groupby(["match_id", "bucket"], observed=True)["turnover_forecast"]
              .transform("sum") > 0]
        if f.empty:
            return

        keys = f[["match_id", "bucket"]].drop_duplicates().sort_values(["match_id", "bucket"])
        keys["slot"] = keys.groupby("match_id", observed=True).cumcount()
        keys = keys[keys["slot"] % max(1, self.cfg.optimize_every) == 0]
        if keys.empty:
            return
        f = f.merge(keys[["match_id", "bucket"]], on=["match_id", "bucket"], how="inner")

        for (mid, bucket), g in f.groupby(["match_id", "bucket"], observed=True, sort=True):
            if self.full:
                return
            self.n_seen += 1
            self._one(int(mid) if pd.notna(mid) else -1, bucket, g)

    def _one(self, mid: int, bucket, g: pd.DataFrame) -> None:
        built = build_book(g, self.cfg)
        if built is None:
            return
        book, sub = built
        th_hkjc = hkjc_theta(g)
        warm = self.warm.get(mid) or {k: v for k, v in th_hkjc.items()}

        th_opt: Dict[str, Tuple[float, float]] = dict(th_hkjc)
        solved_any = False
        for domain in sorted(set(book.domain)):
            tg, sup, ok = solve_domain(book, domain, warm.get(domain, DEFAULT_THETA[domain]),
                                       n_starts=self.cfg.optimize_starts)
            th_opt[domain] = (tg, sup)
            solved_any = solved_any or ok
        self.warm[mid] = th_opt
        if solved_any:
            self.n_solved += 1
        else:
            self.n_failed += 1

        odds_opt = book.sell_odds(th_opt)
        odds_hkjc_theta = book.sell_odds(th_hkjc)
        odds_actual = book.actual_odds
        turnover = float(np.nansum(book.turnover))

        # realised money for the same rows, when the bucket is settled
        realized = float(pd.to_numeric(sub.get("realized_gm"), errors="coerce")
                         .fillna(0.0).sum()) if "realized_gm" in sub.columns else np.nan
        actual_turnover = float(pd.to_numeric(sub.get("turnover"), errors="coerce")
                                .fillna(0.0).sum()) if "turnover" in sub.columns else np.nan
        settled = bool(sub["settled"].fillna(False).any()) if "settled" in sub.columns else False

        row = {
            "match_id": mid,
            "bucket": bucket,
            "day": pd.Timestamp(bucket).floor("D"),
            "clock_bin": _mode(g.get("clock_bin")),
            "is_inplay": bool(g["is_inplay"].any()) if "is_inplay" in g.columns else False,
            "n_selections": int(book.n),
            "turnover_forecast": turnover,
            "turnover_actual": actual_turnover,
            "egm_opt": book.expected_gm(odds_opt),
            "egm_hkjc_theta": book.expected_gm(odds_hkjc_theta),
            "egm_actual_odds": book.expected_gm(odds_actual),
            "realized_gm": realized,
            "settled": settled,
        }
        for domain in ("goal", "corner"):
            if domain in set(book.domain):
                row[f"opt_tg_{domain}"] = th_opt[domain][0]
                row[f"opt_sup_{domain}"] = th_opt[domain][1]
                row[f"hkjc_tg_{domain}"] = th_hkjc[domain][0]
                row[f"hkjc_sup_{domain}"] = th_hkjc[domain][1]
        self.rows.append(row)

        # per-selection price comparison, sampled to keep the output bounded
        if len(self.prices) < self.cfg.optimize_max_prices:
            take = min(book.n, self.cfg.optimize_max_prices - len(self.prices))
            for i in range(take):
                self.prices.append({
                    "bucket": bucket,
                    "pool_name": book.pool[i],
                    "domain": book.domain[i],
                    "line": book.line[i],
                    "side": "home/over" if book.is_home[i] else "away/under",
                    "clock_bin": row["clock_bin"],
                    "turnover": float(book.turnover[i]),
                    "p_true": float(book.p_true[i]),
                    "odds_actual": float(odds_actual[i]),
                    "odds_opt": float(odds_opt[i]),
                    "odds_hkjc_theta": float(odds_hkjc_theta[i]),
                })

    # ---------------------------------------------------------------- report
    def finalize(self) -> Optional[dict]:
        return report_from_frames(
            self.cfg, pd.DataFrame(self.rows), pd.DataFrame(self.prices),
            seen=self.n_seen, solver_ok=self.n_solved,
            solver_fallback=self.n_failed,
        )


def report_from_frames(cfg: Config, rows: pd.DataFrame, prices: pd.DataFrame,
                       seen: Optional[int] = None, solver_ok: Optional[int] = None,
                       solver_fallback: Optional[int] = None) -> Optional[dict]:
    """Build the optimizer section from bucket rows and price rows.

    Split out of ``finalize`` so a window assembled from per-day caches reports
    the same way as a single streaming pass: the aggregates below are all sums
    and means over buckets, so merging days is exact rather than approximate.
    Solver counts are unavailable when reading from cache and are reported as
    None instead of being invented.
    """
    if rows is None or rows.empty:
        return None
    df = rows
    px = prices if prices is not None else pd.DataFrame()
    out: dict = {
        "coverage": {
            "buckets_seen": seen if seen is not None else int(len(df)),
            "buckets_scored": int(len(df)),
            "solver_ok": solver_ok,
            "solver_fallback": solver_fallback,
            "matches": int(df["match_id"].nunique()),
            "sampling": (f"every {cfg.optimize_every} bucket per match, "
                         f"capped at {cfg.optimize_max_buckets:,} per day"),
            "demand": cfg.optimize_demand,
            "belief": cfg.optimize_belief,
            "objective": cfg.optimize_objective,
            "scipy": HAVE_SCIPY,
            "hdc_sign": cfg.hdc_sign,
        },
        "totals": _totals(df),
        "by_slice": _by_slice(df) + _price_cuts(px),
        "daily": _daily(df),
        "theta": _theta_report(df),
        "prices": _price_report(px),
        "scatter": _scatter(df),
    }
    out["verdict"] = _verdict(out)
    return out


def _mode(s) -> str:
    if s is None or len(s) == 0:
        return "unknown"
    v = s.dropna()
    return str(v.iloc[0]) if len(v) else "unknown"


def _agg(df: pd.DataFrame) -> dict:
    to = float(df["turnover_forecast"].sum())
    settled = df[df["settled"]]
    real_to = float(settled["turnover_actual"].sum())
    d = {
        "buckets": int(len(df)),
        "turnover_forecast": to,
        "egm_opt": float(df["egm_opt"].sum()),
        "egm_hkjc_theta": float(df["egm_hkjc_theta"].sum()),
        "egm_actual_odds": float(df["egm_actual_odds"].sum()),
        "realized_gm": float(settled["realized_gm"].sum()) if len(settled) else None,
        "realized_turnover": real_to if len(settled) else None,
    }
    d["margin_opt"] = d["egm_opt"] / to if to else None
    d["margin_hkjc_theta"] = d["egm_hkjc_theta"] / to if to else None
    d["margin_actual_odds"] = d["egm_actual_odds"] / to if to else None
    d["margin_realized"] = (d["realized_gm"] / real_to
                            if d["realized_gm"] is not None and real_to else None)
    # the number the whole exercise is about
    d["lift_vs_actual"] = (d["egm_opt"] - d["egm_actual_odds"])
    d["lift_vs_actual_pct"] = (d["lift_vs_actual"] / to) if to else None
    d["lift_from_theta"] = (d["egm_opt"] - d["egm_hkjc_theta"])
    d["lift_from_pricer"] = (d["egm_hkjc_theta"] - d["egm_actual_odds"])
    d["buckets_better"] = int((df["egm_opt"] > df["egm_actual_odds"]).sum())
    d["buckets_worse"] = int((df["egm_opt"] < df["egm_actual_odds"]).sum())
    return d


def _totals(df: pd.DataFrame) -> dict:
    out = {"all": _agg(df)}
    for name, sub in (("prematch", df[~df["is_inplay"]]), ("inplay", df[df["is_inplay"]])):
        if len(sub):
            out[name] = _agg(sub)
    return out


def _by_slice(df: pd.DataFrame) -> list:
    """Lift by the cuts a trader would refuse a model on.

    Phase, clock, TG level and |SUP| come off the solved bucket. Bet type
    cannot: one solve prices every pool in the bucket together, so the pool
    cut lives on the sampled prices instead.
    """
    work = df.copy()
    if "is_inplay" in work.columns:
        work["phase"] = np.where(work["is_inplay"].fillna(False).astype(bool),
                                 "in-play", "prematch")
    if "hkjc_tg_goal" in work.columns:
        from .bins import tg_bin
        work["tg_bin"] = work["hkjc_tg_goal"].map(tg_bin)
    if "hkjc_sup_goal" in work.columns:
        work["strength"] = work["hkjc_sup_goal"].map(_strength_label)
    rows = []
    for dim in ("phase", "clock_bin", "tg_bin", "strength"):
        if dim not in work.columns:
            continue
        for val, sub in work.groupby(work[dim].astype("string").fillna("unknown"), observed=True):
            rows.append({"dim": dim, "value": str(val), **_agg(sub)})
    return rows


def _strength_label(sup) -> str:
    try:
        v = abs(float(sup))
    except (TypeError, ValueError):
        return "unknown"
    if v != v:
        return "unknown"
    if v < 0.45:
        return "even"
    if v < 1.2:
        return "mismatch"
    return "blowout"


def _daily(df: pd.DataFrame) -> list:
    out = []
    for day, sub in df.groupby("day", observed=True):
        a = _agg(sub)
        out.append({"day": str(pd.Timestamp(day).date()), **a})
    return out


def _price_cuts(px: pd.DataFrame) -> list:
    """Sampled E[GM] lift by bet type. The solver is match-level; this is the book."""
    if px is None or px.empty or "pool_name" not in px.columns:
        return []
    need = ("turnover", "p_true", "odds_opt", "odds_actual", "odds_hkjc_theta")
    if any(c not in px.columns for c in need):
        return []
    df = px.dropna(subset=list(need)).copy()
    df = df[(df["odds_actual"] > 1) & (df["odds_opt"] > 1) & (df["turnover"] > 0)]
    if df.empty:
        return []
    p = df["p_true"].to_numpy(dtype="float64")
    w = df["turnover"].to_numpy(dtype="float64")
    df["turnover_forecast"] = w
    df["egm_opt"] = w * (1.0 - df["odds_opt"].to_numpy(dtype="float64") * p)
    df["egm_actual_odds"] = w * (1.0 - df["odds_actual"].to_numpy(dtype="float64") * p)
    df["egm_hkjc_theta"] = w * (1.0 - df["odds_hkjc_theta"].to_numpy(dtype="float64") * p)
    df["settled"] = False
    df["turnover_actual"] = 0.0
    df["realized_gm"] = 0.0
    market = {
        "HILO": "goal totals", "HDC": "goal handicap",
        "CHLO": "corner totals", "CHDC": "corner handicap",
        "FHLO": "1H goal totals", "FHILO": "1H goal totals",
        "FHDC": "1H goal handicap", "FHHDC": "1H goal handicap",
    }
    df["market"] = df["pool_name"].map(lambda c: market.get(str(c), str(c)))
    rows = []
    for val, sub in df.groupby(df["market"].astype("string"), observed=True):
        rows.append({"dim": "market", "value": str(val), **_agg(sub)})
    return rows


def _theta_report(df: pd.DataFrame) -> dict:
    """How far the optimiser's theta sits from HKJC's own."""
    out = {}
    for domain in ("goal", "corner"):
        cols = [f"opt_tg_{domain}", f"hkjc_tg_{domain}",
                f"opt_sup_{domain}", f"hkjc_sup_{domain}"]
        if not all(c in df.columns for c in cols):
            continue
        sub = df.dropna(subset=cols)
        if sub.empty:
            continue
        dtg = sub[f"opt_tg_{domain}"] - sub[f"hkjc_tg_{domain}"]
        dsup = sub[f"opt_sup_{domain}"] - sub[f"hkjc_sup_{domain}"]
        out[domain] = {
            "n": int(len(sub)),
            "tg_opt_mean": float(sub[f"opt_tg_{domain}"].mean()),
            "tg_hkjc_mean": float(sub[f"hkjc_tg_{domain}"].mean()),
            "sup_opt_mean": float(sub[f"opt_sup_{domain}"].mean()),
            "sup_hkjc_mean": float(sub[f"hkjc_sup_{domain}"].mean()),
            "d_tg_mean": float(dtg.mean()), "d_tg_mae": float(dtg.abs().mean()),
            "d_sup_mean": float(dsup.mean()), "d_sup_mae": float(dsup.abs().mean()),
            "d_tg_hist": _hist(dtg, -2.0, 2.0, 40),
            "d_sup_hist": _hist(dsup, -2.0, 2.0, 40),
            "scatter": [{"x": float(a), "y": float(b)} for a, b in
                        zip(sub[f"hkjc_tg_{domain}"].head(3000),
                            sub[f"opt_tg_{domain}"].head(3000))],
        }
    return out


def _price_report(px: pd.DataFrame) -> dict:
    """Our board vs HKJC's board, and the pricer replication check."""
    if px.empty:
        return {}
    ok = px[(px["odds_actual"] > 1) & (px["odds_opt"] > 1)]
    rep = px[(px["odds_actual"] > 1) & (px["odds_hkjc_theta"] > 1)]
    out = {"n": int(len(px))}
    if len(ok):
        rel = ok["odds_opt"] / ok["odds_actual"] - 1.0
        out["opt_vs_actual"] = {
            "n": int(len(ok)),
            "mean_rel": float(rel.mean()),
            "median_rel": float(rel.median()),
            "mae_rel": float(rel.abs().mean()),
            "hist": _hist(rel, -0.30, 0.30, 40),
            "shorter_share": float((rel < 0).mean()),
        }
    if len(rep):
        rel = rep["odds_hkjc_theta"] / rep["odds_actual"] - 1.0
        out["replication"] = {
            "n": int(len(rep)),
            "mean_rel": float(rel.mean()),
            "median_rel": float(rel.median()),
            "mae_rel": float(rel.abs().mean()),
            "hist": _hist(rel, -0.30, 0.30, 40),
            "by_pool": [
                {"pool_name": str(p),
                 "n": int(len(s)),
                 "mae_rel": float((s["odds_hkjc_theta"] / s["odds_actual"] - 1).abs().mean()),
                 "mean_rel": float((s["odds_hkjc_theta"] / s["odds_actual"] - 1).mean())}
                for p, s in rep.groupby("pool_name", observed=True)],
        }
    out["scatter"] = [{"x": float(a), "y": float(b)} for a, b in
                      zip(ok["odds_actual"].head(4000), ok["odds_opt"].head(4000))]
    return out


def _scatter(df: pd.DataFrame) -> list:
    s = df.head(4000)
    return [{"x": float(a), "y": float(b)} for a, b in
            zip(s["egm_actual_odds"], s["egm_opt"])]


def _hist(series: pd.Series, lo: float, hi: float, bins: int) -> dict:
    v = pd.to_numeric(series, errors="coerce").dropna().to_numpy()
    if v.size == 0:
        return {"edges": [], "counts": []}
    edges = np.linspace(lo, hi, bins + 1)
    counts, _ = np.histogram(np.clip(v, lo, hi), bins=edges)
    return {"edges": edges.tolist(), "counts": counts.tolist()}


def _verdict(out: dict) -> List[dict]:
    v = []
    a = out["totals"]["all"]
    rep = (out.get("prices") or {}).get("replication")
    if rep:
        good = rep["mae_rel"] < 0.05
        v.append({
            "topic": "pricer reproduces HKJC's board",
            "verdict": "close" if good else "does not match",
            "detail": (f"feeding HKJC's own TG/SUP through this pricer lands "
                       f"{_pct(rep['mae_rel'])} away from the odds they actually "
                       f"opened (median {_pct(rep['median_rel'])} on "
                       f"{rep['n']:,} prices). "
                       + ("The margin and line conventions line up, so the lift "
                          "below is about theta, not about pricing bugs."
                          if good else
                          "Until this closes, treat the optimiser lift as untrusted: "
                          "the gap is in the pricer or the margin table, not in theta. "
                          "Try --hdc-sign flip and check the per-pool table.")),
        })
    if a["lift_vs_actual_pct"] is not None:
        v.append({
            "topic": "optimised theta vs HKJC's board",
            "verdict": "better" if a["lift_vs_actual"] > 0 else "no better",
            "detail": (f"expected margin {_pct(a['margin_opt'])} against "
                       f"{_pct(a['margin_actual_odds'])} on the odds HKJC opened, "
                       f"a lift of {_pct(a['lift_vs_actual_pct'])} of turnover "
                       f"({a['lift_vs_actual']:,.0f} on "
                       f"{a['turnover_forecast']:,.0f}); better in "
                       f"{a['buckets_better']:,} of {a['buckets']:,} buckets."),
        })
        if a["egm_opt"]:
            share = a["lift_from_theta"] / a["lift_vs_actual"] if a["lift_vs_actual"] else None
            v.append({
                "topic": "where the lift comes from",
                "verdict": "theta" if (share or 0) > 0.5 else "pricer",
                "detail": (f"re-pricing with HKJC's own theta already gives "
                           f"{a['lift_from_pricer']:,.0f}; moving theta adds "
                           f"{a['lift_from_theta']:,.0f}. Only the second part is a "
                           f"genuine parameter edge, the first is a difference in "
                           f"pricing or margin."),
            })
    if a["margin_realized"] is not None:
        v.append({
            "topic": "against real settled money",
            "verdict": "reference",
            "detail": (f"the same buckets realised {_pct(a['margin_realized'])} margin "
                       f"on {a['realized_turnover']:,.0f} of settled turnover. "
                       f"Expected margins are noise-free; this one is a single draw, "
                       f"so treat it as a sanity check on scale, not a target."),
        })
    return v


def _pct(x) -> str:
    return "n/a" if x is None else f"{100 * float(x):.2f}%"
