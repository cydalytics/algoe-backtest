"""Configuration for the offline parquet backtest.

Everything here can be overridden from the command line, see run.py --help.
Defaults match the layout described in "1 Data Extraction.ipynb".
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional

# Folder layout produced by "1 Data Extraction.ipynb" / "5 Data Extraction - Real Time.ipynb".
SUBDIRS = {
    "investments": "Match_Investments",
    "pools": "Pool_Details",
    "matches": "Match_Information",
    "params": "HKJC_Parameters",
    "events": "Match_Events",
    "odds": "HKJC_Odds",
    "market": "Market_Odds",
}

# Timestamp column used to slice each table by date (canonical name after aliasing).
TIME_COL = {
    "investments": "start_sell_time",
    "pools": "start_selling_time",
    "matches": "ko_time",
    "params": "event_time",
    "events": "incident_datetime",
    "odds": "effective_datetime",
    "market": "effective_datetime",
}

# Pools the desk actually optimises (same short-list as notebook 2's POOLS dict).
DEFAULT_POOLS = ["HILO", "HDC", "CHLO", "CHDC"]

# Bookmaker margin per pool family, used only when we have to de-marginalise
# public odds because true_odds is missing.
DEFAULT_MARGINS = {
    "HILO": 0.075,
    "HDC": 0.075,
    "CHLO": 0.10,
    "CHDC": 0.10,
    "FHILO": 0.085,
    "FHDC": 0.085,
    "HAD": 0.115,
    "FHAD": 0.115,
    "_default": 0.09,
}


@dataclass
class Config:
    # --- where the data is -------------------------------------------------
    data_dir: Path = Path(r"S:\Users\Yeung\20260520 Algo E\Raw_Data")
    out_dir: Path = Path("./backtest_out")
    # Shared panel cache. Empty means "beside data_dir, see cache.default_cache_root".
    cache_dir: Optional[Path] = None

    # --- what window to score ---------------------------------------------
    start_date: str = "2026-07-01"
    end_date: str = "2026-09-15"
    bucket_minutes: int = 5

    # --- what to score ----------------------------------------------------
    pools: List[str] = field(default_factory=lambda: list(DEFAULT_POOLS))
    # How far before kick-off a selection enters the panel. Prematch pools can be
    # open for days; scoring every 5-min bucket of that is mostly zeros and blows
    # up row counts, so we default to the last 6 hours. Buckets that actually
    # took money are ALWAYS kept, whatever this value is, so no turnover is lost.
    prematch_window_min: int = 360
    # Keep the whole selling window instead (slow, large).
    full_span: bool = False

    # --- loader knobs -----------------------------------------------------
    # Odds/params must be carried forward from before the day being scored.
    lookback_days: int = 2
    # Cap on rows sampled for scatter plots.
    sample_rows: int = 6000

    # --- outputs ----------------------------------------------------------
    save_facts: bool = False        # write the per-day panel to parquet
    write_html: bool = True
    write_json: bool = True

    # --- assumptions ------------------------------------------------------
    # Precedence for the "true probability" belief:
    #   1. true_odds column  -> p = 1/true_odds        (what the user asked for)
    #   2. TG/SUP + Poisson  -> p = poisson fair prob  (needs poisson_betting.py)
    #   3. de-marginalised public odds
    true_prob_source: str = "auto"  # auto | true_odds | poisson | demargin
    # Renormalise p_true so each (pool, line) book sums to 1.
    normalize_book: bool = True
    margins: dict = field(default_factory=lambda: dict(DEFAULT_MARGINS))

    # --- optimizer stage --------------------------------------------------
    # Solve TG/SUP per 5-min bucket, price the book, compare with HKJC's board.
    optimize: bool = False
    # One SLSQP solve per match-bucket is far too many over 2.5 months, so the
    # stage samples: keep every Nth bucket of each match, and stop at a cap.
    optimize_every: int = 6            # every 6th bucket = every 30 minutes
    optimize_max_buckets: int = 20000
    optimize_max_prices: int = 60000
    optimize_starts: int = 3           # multi-start count per domain
    # Which turnover the optimiser prices for. The baseline assumption is that
    # the next 5 min looks like the last 5 min, so f_persist is the honest
    # choice; 'turnover' uses the realised amount and is an upper bound that
    # assumes perfect demand forecasting.
    optimize_demand: str = "f_persist"
    # Which belief values the payout, and what the solver maximises. Each
    # (demand, belief, objective) triple caches its own optimizer days.
    optimize_belief: str = "p_true"
    optimize_objective: str = "egm"
    # HKJC's HDC/CHDC line_label sign convention; see optimize._home_handicap.
    hdc_sign: str = "home"             # home | flip

    def to_dict(self) -> dict:
        d = asdict(self)
        d["data_dir"] = str(self.data_dir)
        d["out_dir"] = str(self.out_dir)
        d["cache_dir"] = str(self.cache_dir) if self.cache_dir else None
        return d

    def margin_for(self, pool_name: str) -> float:
        return self.margins.get(pool_name, self.margins.get("_default", 0.09))
