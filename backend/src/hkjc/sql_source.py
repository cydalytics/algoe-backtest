"""
Live SQL Source

Runs the notebook's seven queries against the HKJC SQL Servers over pyodbc
and returns a Snapshot. This is the production path inside the offline
environment; pyodbc is imported lazily so the rest of the stack still runs
on a machine that has never had a SQL driver installed.

Change Log:
-----------
2026-08-30      Initialize
"""

from datetime import datetime

import pandas as pd

from src.core import config
from src.core.exceptions import ExtractionError
from src.core.logging import get_logger
from src.hkjc import queries
from src.hkjc.snapshot import Snapshot, empty_snapshot

log = get_logger(__name__)

# The params database is renamed when HKJC refreshes it. The login that
# worked yesterday is rejected on the old name (ODBC 4068).
_BIS_DATABASES = ("bis1_fbuser_db", "bis2_fbuser_db")


def _database_name(dsn: str) -> str:
    for part in dsn.split(";"):
        if part.upper().startswith("DATABASE="):
            return part.split("=", 1)[1]
    return ""


def _with_database(dsn: str, database: str) -> str:
    parts = []
    replaced = False
    for part in dsn.split(";"):
        if part.upper().startswith("DATABASE="):
            parts.append("DATABASE={}".format(database))
            replaced = True
        elif part:
            parts.append(part)
    if not replaced:
        parts.append("DATABASE={}".format(database))
    return ";".join(parts)


def bis_candidates(dsn: str) -> list:
    """Configured BIS database first, then the other fbuser name."""
    current = _database_name(dsn)
    names = [current] if current else []
    for name in _BIS_DATABASES:
        if name not in names:
            names.append(name)
    out = []
    for name in names:
        alt = _with_database(dsn, name) if name else dsn
        if alt not in out:
            out.append(alt)
    return out


def _database_unavailable(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "cannot open database" in text or "4068" in text


class SqlSource:
    """The HKJC SQL Servers, read exactly the way the notebook reads them."""

    name = "sql"

    def __init__(self, conn_qfm=None, conn_bis=None, conn_bis2=None):
        self._dsn = {
            "qfm": conn_qfm or config.SQL_CONN_QFM,
            "bis": conn_bis or config.SQL_CONN_BIS,
            "bis2": conn_bis2 or config.SQL_CONN_BIS2,
        }
        self._conn = {}

    def describe(self) -> str:
        return "HKJC SQL Server ({})".format(
            self._dsn["qfm"].split("SERVER=")[-1].split(";")[0]
        )

    # -- connection ----------------------------------------------------------

    def _connect(self, key):
        if key in self._conn:
            return self._conn[key]
        try:
            import pyodbc
        except ImportError as exc:
            raise ExtractionError(
                "pyodbc is not installed - the sql source only runs inside "
                "the HKJC environment. Use ALGOE_SOURCE=sim or =capture here."
            ) from exc
        candidates = bis_candidates(self._dsn[key]) if key == "bis" else [self._dsn[key]]
        errors = []
        for dsn in candidates:
            try:
                conn = pyodbc.connect(dsn)
            except Exception as exc:
                errors.append((dsn, exc))
                if key == "bis" and _database_unavailable(exc) and dsn != candidates[-1]:
                    log.warning("BIS database unavailable (%s); trying the other name", exc)
                    continue
                break
            self._conn[key] = conn
            self._dsn[key] = dsn
            if len(candidates) > 1 and dsn != candidates[0]:
                log.warning("using BIS database %s", _database_name(dsn))
            return conn
        tried = "; ".join("{}: {}".format(_database_name(dsn) or dsn, exc) for dsn, exc in errors)
        raise ExtractionError("cannot reach {} ({})".format(key, tried)) from errors[-1][1]

    def _read(self, key, sql, parse_dates=None) -> pd.DataFrame:
        try:
            return pd.read_sql(sql, self._connect(key), parse_dates=parse_dates)
        except ExtractionError:
            raise
        except Exception as exc:
            raise ExtractionError("query against {} failed: {}".format(key, exc)) from exc

    def close(self):
        for conn in self._conn.values():
            try:
                conn.close()
            except Exception:
                pass
        self._conn.clear()

    # -- extraction ----------------------------------------------------------

    def fetch(self, as_of: datetime = None, match_ids=None) -> Snapshot:
        """Pull one tick. ``as_of`` only stamps the snapshot; SQL reads now."""
        as_of = as_of or datetime.now()

        active = self._read("qfm", queries.ACTIVE_POOLS)
        active["match_id"] = (
            pd.to_numeric(active["match_id"], errors="coerce").fillna(0).astype(int)
        )
        if match_ids:
            active = active[active["match_id"].isin([int(i) for i in match_ids])]

        ids = sorted(active["match_id"].unique().tolist())
        if not ids:
            log.warning("no active pools at %s", as_of)
            return empty_snapshot(as_of, self.name).conform()

        match_info = self._read(
            "qfm", queries.match_info(ids), parse_dates=["KOTime"]
        ).rename(columns={"MatchId": "match_id"})

        raw_params = self._read(
            "bis", queries.algo_params(ids), parse_dates=["EventTime", "CreateTime"]
        )
        # Keep the newest parameter row per match, as the notebook does.
        params = (
            raw_params.sort_values("EventTime")
            .groupby("EventLevel2ID", as_index=False)
            .last()
            .rename(columns={"EventLevel2ID": "match_id"})
        )

        events = self._read(
            "qfm",
            queries.incidents(ids),
            parse_dates=["incident_datetime", "last_modified_datetime"],
        ).rename(columns={"event_id": "match_id"})

        odds = self._read(
            "qfm", queries.hkjc_odds(ids), parse_dates=["effective_datetime"]
        ).rename(columns={"EnumString": "pool_name"})
        odds = odds[~odds["odds"].isna()]

        market = self._read(
            "qfm", queries.market_odds(ids), parse_dates=["effective_datetime"]
        ).rename(columns={"bet_type_code_str": "pool_name"})
        market = market[~market["odds"].isna()]

        investments = self._read(
            "bis2", queries.investments(ids), parse_dates=["start_sell_time"]
        )

        snap = Snapshot(
            as_of=as_of,
            source=self.name,
            active_pools=active,
            match_info=match_info,
            params=params,
            events=events,
            odds=odds,
            market=market,
            investments=investments,
        ).conform()
        log.info("sql snapshot %s: %s", as_of, snap.summary())
        return snap
