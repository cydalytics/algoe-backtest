"""Parquet loading with column aliasing and time-window pushdown.

The raw folders were written by several different extraction scripts, so column
names drift (MatchId / match_id / EventLevel2ID, EnumString / pool_name, ...).
Everything is renamed to one canonical set here so the rest of the code never
has to care.
"""
from __future__ import annotations

import warnings
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from .config import SUBDIRS, TIME_COL

# canonical name -> possible names in the raw files (checked case-insensitively)
ALIASES: Dict[str, Dict[str, List[str]]] = {
    "odds": {
        "match_id": ["match_id", "matchid", "event_id", "eventlevel2id"],
        "pool_id": ["pool_id"],
        "pool_name": ["pool_name", "enumstring", "bet_type_code_str"],
        "bet_type_code": ["bet_type_code"],
        "line_id": ["line_id", "line_no"],
        "line_label": ["line_label"],
        "combination_id": ["combination_id", "comb_id"],
        "combination_string": ["combination_string", "odds_combination"],
        "odds": ["odds"],
        "true_odds": ["true_odds", "odds_true", "true_odd"],
        "effective_datetime": ["effective_datetime"],
    },
    "market": {
        "match_id": ["match_id", "matchid", "event_id"],
        "pool_id": ["pool_id"],
        # NOTE: the historical extraction swapped these two CTE joins, so
        # bookmaker_str can hold the bet-type name and bet_type_code_str the
        # bookmaker name. resolve_market_swap() repairs it after load.
        "pool_name": ["pool_name", "bet_type_code_str", "enumstring"],
        "bookmaker": ["bookmaker"],
        "bookmaker_str": ["bookmaker_str"],
        "line_id": ["line_id"],
        "line_label": ["line_label"],
        "combination_id": ["combination_id"],
        "combination_string": ["combination_string"],
        "odds": ["odds"],
        "effective_datetime": ["effective_datetime"],
    },
    "investments": {
        "match_id": ["match_id", "eventlevel2id"],
        "pool_id": ["pool_id"],
        "pool_name": ["pool_name"],
        "pool_type": ["pool_type"],
        "line_id": ["line_no", "line_id"],
        "combination_id": ["comb_id", "combination_id"],
        "combination_string": ["odds_combination", "combination_string"],
        "odds": ["odds"],
        "turnover": ["turnover"],
        "dividend": ["dividend"],
        "es_dividend": ["es_dividend"],
        "ticket_count": ["ticket_count"],
        "start_sell_time": ["start_sell_time", "sell_time_min", "dt"],
        "kickoff_date": ["kickoff_date"],
        "tournament": ["tournament"],
    },
    "pools": {
        "pool_id": ["pool_id"],
        "match_id": ["match_id", "eventlevel2id"],
        "pool_name": ["pool_name"],
        "pool_type": ["pool_type"],
        "start_selling_time": ["start_selling_time", "start_sell_time"],
        "close_time": ["close_time"],
        "kickoff_date": ["kickoff_date"],
    },
    "matches": {
        "match_id": ["match_id", "matchid"],
        "league_code": ["leaguecode"],
        "league_name": ["leaguename"],
        "ko_time": ["kotime", "ko_time"],
        "home_name": ["homename"],
        "away_name": ["awayname"],
        "ft_home": ["homescoreft"],
        "ft_away": ["awayscoreft"],
        "is_void": ["isvoid"],
    },
    "params": {
        "match_id": ["match_id", "eventlevel2id"],
        "event_time": ["eventtime", "event_time"],
        "game_state": ["gamestate"],
        "time_in_second": ["timeinsecond"],
        "tg": ["goal90currenttg"],
        "sup": ["goal90currentsup"],
        "ctg": ["corner90currenttg"],
        "csup": ["corner90currentsup"],
        "goal_split_fh": ["goal90secondhalfsplitfh"],
        "corner_split_fh": ["corner90secondhalfsplitfh"],
    },
    "events": {
        "match_id": ["match_id", "event_id", "eventlevel2id"],
        "incident_datetime": ["incident_datetime"],
        "incident_type": ["incident_type"],
        "home_or_away": ["home_or_away"],
        "provider_id": ["provider_id"],
    },
}

# Columns we insist on; a missing one is a hard error with a clear message.
REQUIRED = {
    "odds": ["match_id", "pool_id", "line_id", "combination_id", "odds", "effective_datetime"],
    "investments": ["match_id", "pool_id", "line_id", "combination_id", "turnover", "start_sell_time"],
    "pools": ["pool_id", "match_id", "start_selling_time"],
    "matches": ["match_id"],
    "params": ["match_id", "event_time"],
    "events": ["match_id", "incident_datetime", "incident_type"],
    "market": ["match_id", "pool_id", "odds", "effective_datetime"],
}


class MissingData(RuntimeError):
    pass


def folder_for(data_dir: Path, name: str) -> Path:
    return Path(data_dir) / SUBDIRS[name]


def files_for(data_dir: Path, name: str) -> List[Path]:
    p = folder_for(data_dir, name)
    if not p.exists():
        return []
    return sorted(p.glob("*.parquet"))


def _schema_names(path: Path) -> List[str]:
    return list(pq.read_schema(path).names)


def _resolve(name: str, present: Sequence[str]) -> Dict[str, str]:
    """Map raw column name -> canonical name for one table."""
    lower = {c.lower(): c for c in present}
    out: Dict[str, str] = {}
    for canon, cands in ALIASES[name].items():
        for cand in cands:
            raw = lower.get(cand)
            if raw is not None and raw not in out:
                out[raw] = canon
                break
    return out


def _file_time_bounds(path: Path, raw_time_col: str):
    """(min, max) of the time column from parquet statistics, or None."""
    try:
        md = pq.ParquetFile(path).metadata
        idx = [i for i, n in enumerate(md.schema.names) if n == raw_time_col]
        if not idx:
            return None
        j = idx[0]
        lo, hi = None, None
        for rg in range(md.num_row_groups):
            st = md.row_group(rg).column(j).statistics
            if st is None or not st.has_min_max:
                return None
            lo = st.min if lo is None else min(lo, st.min)
            hi = st.max if hi is None else max(hi, st.max)
        if lo is None:
            return None
        return pd.Timestamp(lo), pd.Timestamp(hi)
    except Exception:
        return None


def load_window(
    data_dir: Path,
    name: str,
    start: Optional[pd.Timestamp] = None,
    end: Optional[pd.Timestamp] = None,
    pools: Optional[Iterable[str]] = None,
    extra_filter=None,
) -> pd.DataFrame:
    """Load one logical table restricted to [start, end).

    Files whose parquet statistics prove they are outside the window are skipped
    without being read. Rows are re-checked in pandas afterwards.
    """
    paths = files_for(data_dir, name)
    if not paths:
        raise MissingData(
            f"No parquet files under {folder_for(data_dir, name)} . "
            f"Check --data-dir, or run `python run.py probe` first."
        )

    tcol = TIME_COL[name]
    pool_set = set(pools) if pools else None
    frames: List[pd.DataFrame] = []

    for path in paths:
        try:
            present = _schema_names(path)
        except Exception as exc:  # unreadable file, keep going
            warnings.warn(f"skip {path.name}: {exc}")
            continue
        rename = _resolve(name, present)
        canon = set(rename.values())
        raw_time = next((r for r, c in rename.items() if c == tcol), None)

        # cheap file-level skip using row-group statistics
        if raw_time and (start is not None or end is not None):
            bounds = _file_time_bounds(path, raw_time)
            if bounds is not None:
                lo, hi = bounds
                if end is not None and lo >= end:
                    continue
                if start is not None and hi < start:
                    continue

        cols = [r for r in rename if r in present]
        try:
            df = pd.read_parquet(path, columns=cols)
        except Exception as exc:
            warnings.warn(f"skip {path.name}: {exc}")
            continue
        df = df.rename(columns=rename)
        # a file may repeat a canonical name twice (e.g. line_no and line_id)
        df = df.loc[:, ~df.columns.duplicated()]

        if tcol in df.columns:
            df[tcol] = pd.to_datetime(df[tcol], errors="coerce")
            if start is not None:
                df = df[df[tcol] >= start]
            if end is not None:
                df = df[df[tcol] < end]
        if pool_set and "pool_name" in df.columns:
            df = df[df["pool_name"].isin(pool_set)]
        if extra_filter is not None:
            df = extra_filter(df)
        if not df.empty:
            frames.append(df)

    if not frames:
        return pd.DataFrame(columns=sorted(set(ALIASES[name].keys())))

    out = pd.concat(frames, ignore_index=True)
    missing = [c for c in REQUIRED.get(name, []) if c not in out.columns]
    if missing:
        raise MissingData(
            f"{SUBDIRS[name]} is missing required column(s) {missing}. "
            f"Columns found: {sorted(out.columns)}. Add an alias in loader.ALIASES."
        )
    return normalise(name, out)


def normalise(name: str, df: pd.DataFrame) -> pd.DataFrame:
    """Canonical dtypes so joins do not silently fail on int-vs-str keys."""
    for col in ("match_id", "pool_id", "line_id", "combination_id", "provider_id",
                "incident_type", "home_or_away", "bookmaker", "ticket_count"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    for col in ("odds", "true_odds", "turnover", "dividend", "es_dividend",
                "tg", "sup", "ctg", "csup", "goal_split_fh", "corner_split_fh",
                "time_in_second"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    for col in ("effective_datetime", "start_sell_time", "start_selling_time",
                "close_time", "event_time", "incident_datetime", "ko_time",
                "kickoff_date"):
        if col in df.columns:
            # parquet round-trips as us; the panel is built in ns. Mixing the two
            # makes merge_asof refuse the join, so pin everything to ns here.
            df[col] = pd.to_datetime(df[col], errors="coerce").astype("datetime64[ns]")
    for col in ("pool_name", "pool_type", "line_label", "combination_string",
                "game_state", "league_code", "league_name"):
        if col in df.columns:
            df[col] = df[col].astype("string").str.strip()
    if name == "market":
        df = resolve_market_swap(df)
    return df


def resolve_market_swap(df: pd.DataFrame) -> pd.DataFrame:
    """Undo the swapped bookmaker / bet-type CTE join in the market extraction.

    In "1 Data Extraction.ipynb" the aliases ed/bn are crossed, so bookmaker_str
    can contain bet-type names ('HILO'...) and bet_type_code_str bookmaker names
    ('Pinnacle'...). Detect by looking at which column matches the bet-type
    vocabulary and swap if needed.
    """
    if "bookmaker_str" not in df.columns or "pool_name" not in df.columns:
        return df
    vocab = {"HAD", "HILO", "HDC", "CHLO", "CHDC", "FHAD", "FHILO", "FHDC", "CRS", "TTG"}
    a = set(df["pool_name"].dropna().unique()[:50])
    b = set(df["bookmaker_str"].dropna().unique()[:50])
    if not (a & vocab) and (b & vocab):
        df = df.rename(columns={"pool_name": "_bm", "bookmaker_str": "pool_name"})
        df = df.rename(columns={"_bm": "bookmaker_str"})
    return df


def load_static(data_dir: Path, start: pd.Timestamp, end: pd.Timestamp):
    """Small reference tables loaded once: pool selling windows + match info."""
    pools = load_window(
        data_dir, "pools",
        extra_filter=lambda d: d,  # filter after, needs close_time overlap
    )
    if not pools.empty:
        keep = pd.Series(True, index=pools.index)
        if "close_time" in pools.columns:
            keep &= pools["close_time"].isna() | (pools["close_time"] >= start)
        keep &= pools["start_selling_time"].isna() | (pools["start_selling_time"] < end)
        pools = pools[keep]
        agg = {"start_selling_time": "min"}
        if "close_time" in pools.columns:
            agg["close_time"] = "max"
        for c in ("pool_name", "pool_type", "match_id"):
            if c in pools.columns:
                agg[c] = "first"
        pools = pools.groupby("pool_id", as_index=False).agg(agg)

    matches = load_window(data_dir, "matches")
    if not matches.empty:
        matches = matches.drop_duplicates("match_id")
    return pools, matches


def has_true_odds(data_dir: Path) -> bool:
    for path in files_for(data_dir, "odds")[:5]:
        try:
            rename = _resolve("odds", _schema_names(path))
        except Exception:
            continue
        if "true_odds" in rename.values():
            return True
    return False
