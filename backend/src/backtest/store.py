"""
Backtest Result Store

One SQLite table: the full report JSON plus a handful of headline columns
so the API can list runs without parsing the blob.

Change Log:
-----------
2026-09-11      Initialize (W06 backtest)
"""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from src.core import config
from src.core.exceptions import StoreError
from src.core.logging import get_logger

log = get_logger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id          TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    seed            INTEGER,
    n_ticks         INTEGER,
    n_matches       INTEGER,
    n_rows          INTEGER,
    n_combos        INTEGER,
    optimized       INTEGER,
    best_turnover   TEXT,
    best_true_odds  TEXT,
    days_better     INTEGER,
    days_worse      INTEGER,
    headline        TEXT,
    report          TEXT NOT NULL
);
"""


class BacktestStore:
    """Persists finished backtest reports next to the tick store."""

    def __init__(self, path=None):
        self.path = Path(path or config.DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.facts_dir = self.path.parent / "backtest_facts"
        self.facts_dir.mkdir(parents=True, exist_ok=True)
        self._facts_cache = {}
        with self.connect() as conn:
            conn.executescript(SCHEMA)

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

    def save(self, report: dict) -> str:
        cfg = report.get("config", {})
        head = report.get("headline", {})
        with self.connect() as conn:
            conn.execute("DELETE FROM backtest_runs WHERE run_id = ?", (report["run_id"],))
            conn.execute(
                "INSERT INTO backtest_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    report["run_id"],
                    report.get("created_at"),
                    cfg.get("seed"),
                    cfg.get("n_ticks"),
                    cfg.get("n_matches"),
                    report.get("n_rows"),
                    len(report.get("combos") or []) or sum(
                        len((report.get("labs") or {}).get(k, {}).get("candidates") or [])
                        for k in ("turnover", "true_prob", "optimizer", "policy")
                    ),
                    int(bool(cfg.get("optimize"))),
                    head.get("best_turnover"),
                    head.get("best_true_odds"),
                    head.get("days_better"),
                    head.get("days_worse"),
                    json.dumps(head),
                    json.dumps(report),
                ),
            )
        log.info("stored backtest %s", report["run_id"])
        return report["run_id"]

    def get(self, run_id: str):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT report FROM backtest_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return json.loads(row["report"]) if row else None

    def latest(self):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT report FROM backtest_runs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return json.loads(row["report"]) if row else None

    def facts_path(self, run_id: str) -> Path:
        safe = "".join(c for c in run_id if c.isalnum() or c in "-_")
        return self.facts_dir / "{}.parquet".format(safe)

    def save_facts(self, run_id: str, frame: pd.DataFrame, persist=True) -> None:
        self._facts_cache[run_id] = frame
        if not persist or frame is None:
            return
        path = self.facts_path(run_id)
        try:
            frame.to_parquet(path, index=False)
            log.info("stored facts %s (%d rows)", run_id, len(frame))
        except Exception as exc:                              # noqa: BLE001
            log.warning("facts parquet failed (%s); keeping memory cache", exc)

    def load_facts(self, run_id: str):
        if run_id in self._facts_cache:
            return self._facts_cache[run_id]
        path = self.facts_path(run_id)
        if not path.exists():
            return None
        frame = pd.read_parquet(path)
        self._facts_cache[run_id] = frame
        return frame

    def latest_facts(self):
        report = self.latest()
        if report is None:
            return None, None
        return report, self.load_facts(report["run_id"])

    def list(self, limit=20):
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT run_id, created_at, seed, n_ticks, n_matches, n_rows, "
                "n_combos, optimized, best_turnover, best_true_odds, "
                "days_better, days_worse FROM backtest_runs "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


_store = None


def get_backtest_store(path=None) -> BacktestStore:
    global _store
    if _store is None or path is not None:
        _store = BacktestStore(path)
    return _store
