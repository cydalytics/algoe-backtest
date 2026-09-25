"""Independent true-probability sources for one day's panel.

``panel._true_prob`` picks one number per row using a precedence rule, which is
the right thing for a single headline backtest. The workbench needs the
opposite: every source side by side on the same row, so a belief can be
compared against another belief on identical money.

Three sources, none of which is derived from the others:

    hkjc_true   p = 1 / true_odds                 HKJC's own fair odds
    poisson     p from HKJC's TG/SUP via Poisson  their parameters, our pricer
    demargin    p = (1 / odds) / (1 + margin)     public odds, margin removed

Each is renormalised so the two sides of a line sum to one, because all three
pools here are exhaustive two-way books once pushes are folded in. The
un-normalised book sum is kept as a diagnostic: it is how far the source was
from coherent before we touched it.

Change Log:
-----------
2026-09-15      Initialize (W06 parquet workbench)
"""

from __future__ import annotations

from typing import Dict, Tuple

import numpy as np
import pandas as pd

from .config import Config
from .pricing import POOL_KIND, asian_handicap, over_under, parse_line, score_matrix

SOURCES = ("hkjc_true", "poisson", "demargin")

SOURCE_LABELS = {
    "hkjc_true": "1 / HKJC true odds",
    "poisson": "Poisson on HKJC TG/SUP",
    "demargin": "Public odds, margin removed",
}

LINE_GROUP = ["pool_id", "line_id", "bucket"]

# theta is rounded before it keys the price cache; 1e-3 on goals is far below
# the resolution HKJC moves parameters at, and it turns a per-row solve into a
# per-match-bucket one
THETA_DP = 3
LINE_DP = 3


def _side_is_home(f: pd.DataFrame) -> np.ndarray:
    """Which leg of the line a row is: home/over, or away/under."""
    comb = f.get("combination_string")
    text = (comb.astype("string").fillna("") if comb is not None
            else pd.Series("", index=f.index, dtype="string")).str.upper()
    cid = pd.to_numeric(f.get("combination_id"), errors="coerce")
    return (text.str.startswith("H") | ((text == "") & (cid == 1))).to_numpy(dtype=bool)


def _row_theta(f: pd.DataFrame, domain: pd.Series) -> Tuple[np.ndarray, np.ndarray]:
    """(tg, sup) per row, picked by the pool's domain.

    First-half pools are priced on the split HKJC publishes rather than on a
    separate parameter, which is what ``optimize.hkjc_theta`` does too.
    """
    tg = pd.to_numeric(f.get("tg"), errors="coerce")
    sup = pd.to_numeric(f.get("sup"), errors="coerce")
    ctg = pd.to_numeric(f.get("ctg"), errors="coerce")
    csup = pd.to_numeric(f.get("csup"), errors="coerce")
    gfh = pd.to_numeric(f.get("goal_split_fh"), errors="coerce").fillna(0.5)
    cfh = pd.to_numeric(f.get("corner_split_fh"), errors="coerce").fillna(0.5)

    out_tg = pd.Series(np.nan, index=f.index, dtype="float64")
    out_sup = pd.Series(np.nan, index=f.index, dtype="float64")
    for name, (t, s) in (("goal", (tg, sup)),
                         ("corner", (ctg, csup)),
                         ("goal_fh", (tg * gfh, sup * gfh)),
                         ("corner_fh", (ctg * cfh, csup * cfh))):
        m = domain == name
        if m.any():
            out_tg[m] = t[m]
            out_sup[m] = s[m]
    return out_tg.to_numpy(dtype="float64"), out_sup.to_numpy(dtype="float64")


def poisson_prob(cfg: Config, f: pd.DataFrame) -> pd.Series:
    """Fair probability from HKJC's TG/SUP, priced with our own Poisson book.

    Vectorised by construction: one score matrix per distinct (tg, sup) and one
    price per distinct (kind, line) off that matrix, instead of a solve per row.
    """
    out = pd.Series(np.nan, index=f.index, dtype="float64")
    if "pool_name" not in f.columns or "line_label" not in f.columns:
        return out

    pool = f["pool_name"].astype("string").fillna("")
    spec = pool.map(lambda p: POOL_KIND.get(str(p)))
    known = spec.notna()
    if not known.any():
        return out

    domain = spec.map(lambda s: s[0] if s else None)
    kind = spec.map(lambda s: s[1] if s else None)
    tg, sup = _row_theta(f, domain)

    line = f["line_label"].map(parse_line).to_numpy(dtype="float64")
    # a handicap label is read as the home handicap; the sign convention is a
    # config switch because the extraction notebooks do not document it
    if cfg.hdc_sign == "flip":
        line = np.where(kind.to_numpy() == "ah", -line, line)

    is_home = _side_is_home(f)
    ok = (known.to_numpy() & np.isfinite(tg) & np.isfinite(sup)
          & np.isfinite(line) & (tg > 0) & (tg >= np.abs(sup)))
    if not ok.any():
        return out

    kind_v = kind.to_numpy()
    tg_r = np.round(tg, THETA_DP)
    sup_r = np.round(sup, THETA_DP)
    line_r = np.round(line, LINE_DP)

    mats: Dict[Tuple[float, float], np.ndarray] = {}
    prices: Dict[Tuple[str, float, float, float], Tuple[float, float]] = {}
    idx = np.flatnonzero(ok)
    values = np.full(len(idx), np.nan)
    for n, i in enumerate(idx):
        key = (str(kind_v[i]), float(tg_r[i]), float(sup_r[i]), float(line_r[i]))
        pair = prices.get(key)
        if pair is None:
            mkey = (key[1], key[2])
            mat = mats.get(mkey)
            if mat is None:
                mat = score_matrix(key[1], key[2])
                mats[mkey] = mat
            pair = (asian_handicap(mat, key[3]) if key[0] == "ah"
                    else over_under(mat, key[3]))
            prices[key] = pair
        odd = pair[0] if is_home[i] else pair[1]
        if odd and odd > 1.0:
            values[n] = 1.0 / odd
    out.iloc[idx] = values
    return out


def demargin_prob(cfg: Config, f: pd.DataFrame) -> pd.Series:
    """Public odds with the pool's book margin taken back out."""
    odds = pd.to_numeric(f.get("odds"), errors="coerce")
    margin = f["pool_name"].map(cfg.margin_for).astype("float64") \
        if "pool_name" in f.columns else pd.Series(np.nan, index=f.index)
    margin = margin.fillna(cfg.margin_for("_default"))
    p = (1.0 / odds) / (1.0 + margin)
    return p.where(odds > 1.0)


def hkjc_true_prob(f: pd.DataFrame) -> pd.Series:
    true_odds = pd.to_numeric(f.get("true_odds"), errors="coerce")
    return (1.0 / true_odds).where(true_odds > 1.0)


def _normalise(f: pd.DataFrame, p: pd.Series) -> Tuple[pd.Series, pd.Series]:
    """Scale a line's two legs onto 1, and report the sum before scaling."""
    keys = [c for c in LINE_GROUP if c in f.columns]
    if not keys:
        return p, pd.Series(np.nan, index=f.index, dtype="float64")
    book = p.groupby([f[c] for c in keys], observed=True).transform("sum")
    scaled = p / book.where(book > 0)
    return scaled, book


def attach(cfg: Config, f: pd.DataFrame) -> pd.DataFrame:
    """Write ``p__<source>`` and ``book__<source>`` for every source.

    ``p__<source>`` is the number a belief model would state. ``book__<source>``
    is the raw book sum before scaling, which is a coherence check on the two
    odds-derived sources: needing a large correction to reach one means a leg
    was mispriced relative to its partner. It is close to vacuous for
    ``poisson``, where both legs come from the same score matrix and so agree
    by construction except on quarter lines.
    """
    raw = {
        "hkjc_true": hkjc_true_prob(f),
        "poisson": poisson_prob(cfg, f),
        "demargin": demargin_prob(cfg, f),
    }
    for name, p in raw.items():
        scaled, book = _normalise(f, p)
        f["p__{}".format(name)] = (scaled if cfg.normalize_book else p) \
            .clip(lower=1e-6, upper=1.0 - 1e-6)
        f["book__{}".format(name)] = book
    f["p_available"] = sum(f["p__{}".format(s)].notna().astype("int8")
                           for s in SOURCES)
    return f
