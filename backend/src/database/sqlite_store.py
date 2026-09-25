"""
SQLite Tick Store

Persists finished ticks so a session can be audited or replayed after the
fact. The API serves the live board out of memory - this store is the
record, not the read path.

Three tables, one row grain each:

    ticks          one per pipeline run: when, from where, what it made
    tick_matches   one per match per tick: theta before and after, E[GM]
    tick_legs      one per selection per tick: the full priced book

tick_legs is the expensive one and the only one that is optional, because
a full card is well over a thousand rows a tick and a desk running every
five minutes for an evening does not always need that much detail kept.

Change Log:
-----------
2026-08-30      Rewritten for the offline tick pipeline
2026-09-11      Restored (source file had been overwritten by sql_source)
"""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from src.core import config
from src.core.exceptions import StoreError
from src.core.logging import get_logger
from src.pricing import book as book_lib

logger = get_logger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS ticks (
    run_id        TEXT PRIMARY KEY,
    as_of         TEXT NOT NULL,
    source        TEXT,
    matches       INTEGER,
    selections    INTEGER,
    turnover      REAL,
    gm_now        REAL,
    gm_star       REAL,
    uplift        REAL,
    uplift_bps    REAL,
    seconds       REAL,
    timings       TEXT,
    warnings      TEXT
);

CREATE TABLE IF NOT EXISTS tick_matches (
    run_id        TEXT NOT NULL,
    match_id      INTEGER NOT NULL,
    home          TEXT,
    away          TEXT,
    league        TEXT,
    phase         TEXT,
    minute        REAL,
    home_score    INTEGER,
    away_score    INTEGER,
    turnover      REAL,
    gm_now        REAL,
    gm_star       REAL,
    uplift        REAL,
    uplift_bps    REAL,
    legs          INTEGER,
    skipped       INTEGER,
    solved        INTEGER,
    seconds       REAL,
    theta_now     TEXT,
    theta_star    TEXT,
    PRIMARY KEY (run_id, match_id)
);

CREATE TABLE IF NOT EXISTS tick_legs (
    run_id        TEXT NOT NULL,
    match_id      INTEGER NOT NULL,
    key           TEXT NOT NULL,
    pool_code     TEXT,
    line_label    TEXT,
    selection     TEXT,
    hkjc_odds     REAL,
    true_prob     REAL,
    true_prob_src TEXT,
    model_prob    REAL,
    sell_odds     REAL,
    sell_odds_star REAL,
    status        TEXT,
    t_hat         REAL,
    t_hat_src     TEXT,
    exp_gm_unit   REAL,
    PRIMARY KEY (run_id, key)
);

CREATE INDEX IF NOT EXISTS idx_tick_matches_match
    ON tick_matches (match_id, run_id);
CREATE INDEX IF NOT EXISTS idx_tick_legs_match
    ON tick_legs (run_id, match_id);
"""

LEG_COLUMNS = (
    "match_id", "key", "pool_code", "line_label", "selection", "hkjc_odds",
    "true_prob", "true_prob_src", "model_prob", "sell_odds", "sell_odds_star",
    "status", "t_hat", "t_hat_src", "exp_gm_unit",
)


class TickStore:
    """A SQLite file holding the history of finished ticks."""

    def __init__(self, path=None):
        self.path = Path(path or config.DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            raise StoreError("sqlite: {}".format(exc)) from exc
        finally:
            conn.close()

    def _ensure_schema(self):
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    # -----------------------------------------------------------------------

    def save(self, tick, with_legs: bool = True) -> str:
        """Write one finished tick. Re-running the same tick overwrites it."""
        totals = tick.totals
        with self.connect() as conn:
            conn.execute("DELETE FROM ticks WHERE run_id = ?", (tick.run_id,))
            conn.execute("DELETE FROM tick_matches WHERE run_id = ?", (tick.run_id,))
            conn.execute("DELETE FROM tick_legs WHERE run_id = ?", (tick.run_id,))
            conn.execute(
                "INSERT INTO ticks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    tick.run_id,
                    pd.Timestamp(tick.as_of).isoformat(),
                    tick.source,
                    totals.get("matches"),
                    totals.get("selections"),
                    totals.get("opt_turnover", totals.get("turnover")),
                    totals.get("opt_gm_now", totals.get("gm_now")),
                    totals.get("gm_star"),
                    totals.get("uplift"),
                    totals.get("uplift_bps"),
                    round(sum(tick.timings.values()), 3),
                    json.dumps(tick.timings),
                    json.dumps(tick.warnings),
                ),
            )
            conn.executemany(
                "INSERT INTO tick_matches VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                list(self._match_rows(tick)),
            )
            if with_legs:
                legs = self._leg_frame(tick)
                if not legs.empty:
                    legs.insert(0, "run_id", tick.run_id)
                    legs.to_sql("tick_legs", conn, if_exists="append", index=False)
        logger.info("stored tick %s in %s", tick.run_id, self.path.name)
        return tick.run_id

    def _match_rows(self, tick):
        for match_id, ctx in tick.contexts.items():
            result = tick.result(match_id)
            state = ctx.state
            yield (
                tick.run_id, int(match_id), ctx.home, ctx.away, ctx.league,
                state.phase, float(state.minute),
                int(state.home_score), int(state.away_score),
                float(result.turnover) if result else None,
                float(result.gm_now) if result else None,
                float(result.gm_star) if result else None,
                float(result.uplift) if result else None,
                float(result.uplift_bps) if result else None,
                result.legs if result else 0,
                result.skipped if result else 0,
                int(all(b.success for b in result.blocks.values())) if result else 0,
                float(result.seconds) if result else None,
                json.dumps({d: round(float(v), 5) for d, v in
                            (result.theta_now if result else
                             ctx.theta.get(config.THETA_LAYER, {})).items()
                            if d in book_lib.DIMS}),
                json.dumps({d: round(float(v), 5) for d, v in
                            (result.theta_star if result else {}).items()
                            if d in book_lib.DIMS}),
            )

    def _leg_frame(self, tick) -> pd.DataFrame:
        sel = tick.selections
        if sel.empty:
            return pd.DataFrame()
        out = pd.DataFrame(index=sel.index)
        for col in LEG_COLUMNS:
            out[col] = sel[col] if col in sel.columns else None
        return out

    # -----------------------------------------------------------------------

    def list_ticks(self, limit: int = 100) -> pd.DataFrame:
        with self.connect() as conn:
            return pd.read_sql_query(
                "SELECT * FROM ticks ORDER BY as_of DESC LIMIT ?",
                conn, params=(limit,),
            )

    def match_history(self, match_id: int, limit: int = 200) -> pd.DataFrame:
        """Every tick this match appeared on - the audit trail per match."""
        with self.connect() as conn:
            return pd.read_sql_query(
                "SELECT m.*, t.as_of AS tick_at FROM tick_matches m "
                "JOIN ticks t ON t.run_id = m.run_id "
                "WHERE m.match_id = ? ORDER BY t.as_of DESC LIMIT ?",
                conn, params=(int(match_id), limit),
            )

    def legs(self, run_id: str, match_id=None) -> pd.DataFrame:
        sql = "SELECT * FROM tick_legs WHERE run_id = ?"
        params = [run_id]
        if match_id is not None:
            sql += " AND match_id = ?"
            params.append(int(match_id))
        with self.connect() as conn:
            return pd.read_sql_query(sql, conn, params=params)


_store = None


def get_store(path=None) -> TickStore:
    """The process-wide store."""
    global _store
    if _store is None or path is not None:
        _store = TickStore(path)
    return _store
