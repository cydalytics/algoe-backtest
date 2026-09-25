"""
Snapshot

The seven raw frames one tick needs, in exactly the shape
"5 Data Extraction - Real Time.ipynb" produces them. Every data source -
live SQL, a captured parquet dump, or the simulator - hands back this same
object, so the rest of the pipeline never learns where the numbers came
from.

Change Log:
-----------
2026-08-30      Initialize
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List

import pandas as pd

# Column contracts. Sources are validated against these on construction so a
# missing column fails at the boundary rather than three steps later inside
# a groupby.
ACTIVE_POOL_COLS = ["match_id", "pool_id"]

MATCH_INFO_COLS = [
    "match_id", "FrontendID", "LeagueId", "LeagueName", "LeagueCode",
    "VenueId", "VenueName", "Country", "Season",
    "HomeName", "HomeId", "AwayName", "AwayId", "KOTime",
    "HomeScoreFT", "AwayScoreFT", "HomeScoreHT", "AwayScoreHT",
    "HomeCornerFT", "AwayCornerFT", "HomeCornerHT", "AwayCornerHT",
    "HomeYellowFT", "AwayYellowFT", "HomeYellowHT", "AwayYellowHT",
    "HomeRedFT", "AwayRedFT", "HomeRedHT", "AwayRedHT", "IsVoid",
]

PARAM_COLS = [
    "match_id", "AlgoEventID", "EventTime", "TimeInSecond", "Minutes",
    "Seconds", "IsClockSet", "GameState",
    "Goal90BasisTG", "Goal90BasisSUP", "Goal90ModelTG", "Goal90ModelSUP",
    "Goal90CurrentTG", "Goal90CurrentSUP", "Goal90DrawFactor",
    "Goal90SecondHalfSplitFH", "Goal90SecondHalfSplitFT",
    "Corner90BasisTG", "Corner90BasisSUP", "Corner90ModelTG",
    "Corner90ModelSUP", "Corner90CurrentTG", "Corner90CurrentSUP",
    "Corner90DrawFactor", "Corner90SecondHalfSplitFH",
    "Corner90SecondHalfSplitFT", "CreateTime",
]

EVENT_COLS = [
    "match_id", "event_incident_id", "event_type", "seq_id", "stage_id",
    "provider_id", "incident_type", "incident_datetime", "game_time",
    "home_or_away", "info", "last_modified_datetime", "is_deleted",
]

ODDS_COLS = [
    "match_id", "bet_type_code", "pool_name", "pool_id", "line_id",
    "line_label", "combination_id", "combination_string", "odds",
    "effective_datetime", "true_odds",
]

MARKET_COLS = [
    "match_id", "bet_type_code", "pool_name", "pool_id", "line_id",
    "line_label", "combination_id", "combination_string", "bookmaker",
    "bookmaker_str", "odds", "effective_datetime",
]

INVESTMENT_COLS = [
    "pool_id", "pool_name", "match_id", "frontend_id", "tournament",
    "h_team", "a_team", "ht_home_score", "ht_away_score",
    "ft_home_score", "ft_away_score", "kickoff_date", "start_sell_time",
    "comb_id", "line_no", "odds_id", "turnover", "dividend", "es_dividend",
    "odds_combination", "odds", "ticket_count",
]

FRAME_COLS = {
    "active_pools": ACTIVE_POOL_COLS,
    "match_info": MATCH_INFO_COLS,
    "params": PARAM_COLS,
    "events": EVENT_COLS,
    "odds": ODDS_COLS,
    "market": MARKET_COLS,
    "investments": INVESTMENT_COLS,
}


@dataclass
class Snapshot:
    """One 5-minute tick of raw HKJC data."""

    as_of: datetime
    source: str
    active_pools: pd.DataFrame
    match_info: pd.DataFrame
    params: pd.DataFrame
    events: pd.DataFrame
    odds: pd.DataFrame
    market: pd.DataFrame
    investments: pd.DataFrame
    notes: List[str] = field(default_factory=list)

    @property
    def frames(self) -> Dict[str, pd.DataFrame]:
        return {
            "active_pools": self.active_pools,
            "match_info": self.match_info,
            "params": self.params,
            "events": self.events,
            "odds": self.odds,
            "market": self.market,
            "investments": self.investments,
        }

    @property
    def match_ids(self) -> List[int]:
        if self.active_pools.empty:
            return []
        return sorted(self.active_pools["match_id"].astype(int).unique().tolist())

    def conform(self) -> "Snapshot":
        """Add any missing contract columns as nulls and drop the extras.

        Sources drift - a captured parquet may predate a column, the live
        query may return more than we asked for. Conforming here keeps the
        preprocessor free of defensive ``.get`` calls.
        """
        for name, cols in FRAME_COLS.items():
            df = getattr(self, name)
            if df is None:
                df = pd.DataFrame(columns=cols)
            missing = [c for c in cols if c not in df.columns]
            for col in missing:
                df[col] = None
            if missing:
                self.notes.append(
                    "{}: filled {} missing column(s): {}".format(
                        name, len(missing), ", ".join(missing)
                    )
                )
            setattr(self, name, df[cols].copy())
        return self

    def summary(self) -> Dict[str, int]:
        return {name: len(df) for name, df in self.frames.items()}


def empty_snapshot(as_of: datetime, source: str = "empty") -> Snapshot:
    """A structurally valid snapshot with no matches in it."""
    return Snapshot(
        as_of=as_of,
        source=source,
        **{name: pd.DataFrame(columns=cols) for name, cols in FRAME_COLS.items()},
    )
