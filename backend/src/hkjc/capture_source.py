"""
Capture Source

Replays a snapshot previously written by ``scripts/capture.py`` inside the
HKJC environment. This is how real data gets onto a machine that cannot
reach the SQL Servers: run the capture once where the database lives, copy
the folder, then develop against it here.

A capture is a directory named for its as-of stamp holding one parquet per
frame plus a meta.json:

    captures/20260901T1435/
        active_pools.parquet  match_info.parquet  params.parquet
        events.parquet        odds.parquet        market.parquet
        investments.parquet   meta.json

Change Log:
-----------
2026-08-30      Initialize
"""

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.core import config
from src.core.exceptions import ExtractionError
from src.core.logging import get_logger
from src.hkjc.snapshot import FRAME_COLS, Snapshot

log = get_logger(__name__)

STAMP_FMT = "%Y%m%dT%H%M"


def capture_dirs(root=None):
    """Every capture on disk, newest first."""
    root = Path(root or config.CAPTURE_DIR)
    if not root.exists():
        return []
    out = []
    for path in root.iterdir():
        if not path.is_dir():
            continue
        try:
            stamp = datetime.strptime(path.name, STAMP_FMT)
        except ValueError:
            continue
        out.append((stamp, path))
    return [p for _, p in sorted(out, key=lambda x: x[0], reverse=True)]


def write_capture(snapshot: Snapshot, root=None) -> Path:
    """Persist a snapshot so it can be replayed elsewhere."""
    root = Path(root or config.CAPTURE_DIR)
    target = root / snapshot.as_of.strftime(STAMP_FMT)
    target.mkdir(parents=True, exist_ok=True)
    for name, frame in snapshot.frames.items():
        frame.to_parquet(target / "{}.parquet".format(name), index=False)
    (target / "meta.json").write_text(
        json.dumps(
            {
                "as_of": snapshot.as_of.isoformat(),
                "source": snapshot.source,
                "rows": snapshot.summary(),
                "notes": snapshot.notes,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return target


class CaptureSource:
    """Reads a captured snapshot back off disk."""

    name = "capture"

    def __init__(self, path=None):
        self.path = Path(path) if path else None

    def describe(self) -> str:
        target = self.path or (capture_dirs()[0] if capture_dirs() else None)
        return "capture replay ({})".format(target.name if target else "none found")

    def _resolve(self, as_of):
        if self.path:
            return self.path
        available = capture_dirs()
        if not available:
            raise ExtractionError(
                "no captures under {} - run scripts/capture.py in the HKJC "
                "environment first, or use ALGOE_SOURCE=sim".format(config.CAPTURE_DIR)
            )
        if as_of is None:
            return available[0]
        # Nearest capture at or before the requested time.
        for path in available:
            if datetime.strptime(path.name, STAMP_FMT) <= as_of:
                return path
        return available[-1]

    def fetch(self, as_of: datetime = None, match_ids=None) -> Snapshot:
        target = self._resolve(as_of)
        frames = {}
        for name in FRAME_COLS:
            file = target / "{}.parquet".format(name)
            frames[name] = (
                pd.read_parquet(file) if file.exists()
                else pd.DataFrame(columns=FRAME_COLS[name])
            )

        meta_file = target / "meta.json"
        meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
        stamp = (
            datetime.fromisoformat(meta["as_of"]) if meta.get("as_of")
            else datetime.strptime(target.name, STAMP_FMT)
        )

        if match_ids:
            wanted = {int(i) for i in match_ids}
            for name, frame in frames.items():
                if "match_id" in frame.columns and not frame.empty:
                    frames[name] = frame[frame["match_id"].astype(int).isin(wanted)]

        snap = Snapshot(as_of=stamp, source=self.name, **frames).conform()
        snap.notes.append("replayed from {}".format(target.name))
        log.info("capture snapshot %s: %s", stamp, snap.summary())
        return snap
