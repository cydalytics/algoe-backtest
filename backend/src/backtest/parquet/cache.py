"""Incremental per-day panel cache.

The expensive half of a parquet backtest is building the panel: reading the
raw folders, flooring everything into 5-minute buckets, carrying odds and
TG/SUP forward with as-of joins, and deriving settlement from actual
dividends. That work depends only on the data and on a handful of shaping
knobs -- never on which turnover or belief model you want to look at.

So it is cached one day at a time:

    <root>/<space>/manifest.json                what has been built, and from what
    <root>/<space>/panel/2026-07-01.parquet
    <root>/<space>/opt/2026-07-01.parquet       optimizer buckets, opt-in
    <root>/<space>/opt/2026-07-01.prices.parquet

``space`` is a hash of the shaping knobs, so changing the bucket size or the
pool list opens a new space instead of silently mixing incompatible days.

The root is beside the parquet folder (``<data_dir>/../algoe_panel_cache``),
so ``3-run.bat`` and the ``/backtest`` page share one set of files.

Two consequences, both of which the desk asked for:

  * extending the window only builds the days that are missing
  * switching model never rebuilds anything, because the model columns are
    derived later (see adapter.py) from these same cached panels

Change Log:
-----------
2026-09-15      Initialize (W06 parquet workbench)
"""

from __future__ import annotations

import hashlib
import json
import os
import pickle
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence

import pandas as pd

from .config import Config
from .loader import load_static
from .panel import MonthCache, build_day

# Bump when a change to panel.py alters the columns or the numbers in a
# cached day. Old spaces are then ignored rather than silently reused.
#   2  event windows, per-source belief columns, ticket lag
ENGINE_VERSION = 2

# Knobs that change what a cached day contains. Anything not listed here is
# a presentation or model choice and must not fork the cache.
SPACE_FIELDS = (
    "bucket_minutes", "pools", "prematch_window_min", "full_span",
    "lookback_days", "true_prob_source", "normalize_book", "margins",
)

# Bump when evaluate.Accumulator changes what a stored day score holds.
SCORE_VERSION = 1

# The optimizer combination that owns the original opt/ folder, so days
# solved before combinations existed are still found.
BASELINE_COMBO = ("f_persist", "p_true", "egm")

Progress = Callable[[dict], None]


def opt_combo(cfg: Config) -> tuple:
    return (cfg.optimize_demand, cfg.optimize_belief, cfg.optimize_objective)


def combo_label(combo: tuple) -> str:
    demand, belief, objective = combo
    return "{}.{}.{}".format(demand.replace("f_", ""), belief, objective)


class Cancelled(Exception):
    """Raised inside a build when the caller asked it to stop."""


@dataclass
class DayState:
    day: str
    rows: int
    turnover: float
    matches: int
    built_at: str
    opt_rows: Optional[int] = None

    def to_dict(self) -> dict:
        out = {
            "day": self.day,
            "rows": self.rows,
            "turnover": self.turnover,
            "matches": self.matches,
            "built_at": self.built_at,
        }
        if self.opt_rows is not None:
            out["opt_rows"] = self.opt_rows
        return out


def default_cache_root(cfg: Config) -> Path:
    """One folder both the script and the web page read and write.

    Beside the parquet root, not beside whichever zip you launched, so
    ``3-run.bat`` and ``/backtest`` see the same days:

        S:\\...\\Algo E\\Raw_Data
        S:\\...\\Algo E\\algoe_panel_cache\\<space>\\panel\\2026-07-01.parquet

    Override with ``ALGOE_PARQUET_CACHE`` or ``cfg.cache_dir``.
    """
    explicit = getattr(cfg, "cache_dir", None)
    if explicit:
        return Path(explicit)
    env = os.environ.get("ALGOE_PARQUET_CACHE")
    if env:
        return Path(env)
    return Path(cfg.data_dir).resolve().parent / "algoe_panel_cache"


def resolve_cache_root(cfg: Config, extra_roots: Optional[Sequence] = None) -> Path:
    """Prefer a root that already holds this space; otherwise the default."""
    primary = default_cache_root(cfg)
    space = space_key(cfg)
    candidates = [primary]
    for root in extra_roots or []:
        if root is not None:
            candidates.append(Path(root))
    seen = set()
    for root in candidates:
        key = str(root.resolve()) if root.exists() else str(root)
        if key in seen:
            continue
        seen.add(key)
        if (Path(root) / space / "manifest.json").exists():
            return Path(root)
    return primary


def ingest_flat_facts(cache: "PanelCache", facts_dir) -> int:
    """Absorb ``facts_YYYYMMDD.parquet`` written by an older 3-run into the cache.

    Those files are the same panel frame. Copying them in means a run that
    already finished (or is mid-flight in ``out\\facts``) does not get rebuilt.
    """
    folder = Path(facts_dir)
    if not folder.is_dir():
        return 0
    n = 0
    for path in sorted(folder.glob("facts_*.parquet")):
        token = path.stem.replace("facts_", "")
        try:
            day = pd.to_datetime(token, format="%Y%m%d" if token.isdigit() else None)
        except (ValueError, TypeError):
            continue
        if cache.has_day(day):
            continue
        try:
            frame = pd.read_parquet(path)
        except Exception:                                         # noqa: BLE001
            continue
        cache.put_day(day, frame)
        n += 1
    return n


def facts_dir_guesses(cfg: Config) -> List[Path]:
    """Places an older 3-run may have left per-day panels.

    Only ``cfg.out_dir/facts`` (and ``ALGOE_BACKTEST_OUT``) are considered.
    Searching ``cwd/out/facts`` poisoned selftest: a copied real-data folder
    next to ``0-selftest.bat`` was absorbed into the synthetic run.
    """
    found: List[Path] = []
    env_out = os.environ.get("ALGOE_BACKTEST_OUT")
    candidates = [Path(cfg.out_dir) / "facts"]
    if env_out:
        candidates.insert(0, Path(env_out) / "facts")
    seen = set()
    for path in candidates:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.is_dir():
            found.append(path)
    return found


def space_key(cfg: Config) -> str:
    """Short stable hash of the knobs that shape a cached day."""
    payload = {"engine": ENGINE_VERSION, "data_dir": str(cfg.data_dir)}
    for name in SPACE_FIELDS:
        value = getattr(cfg, name)
        if isinstance(value, (list, tuple)):
            value = sorted(str(v) for v in value)
        elif isinstance(value, dict):
            value = {str(k): value[k] for k in sorted(value)}
        payload[name] = value
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def day_list(start, end) -> List[pd.Timestamp]:
    """Inclusive list of calendar days."""
    lo = pd.Timestamp(start).normalize()
    hi = pd.Timestamp(end).normalize()
    if hi < lo:
        return []
    return list(pd.date_range(lo, hi, freq="D"))


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    tmp = path.with_name(path.name + ".tmp")
    frame.to_parquet(tmp, index=False)
    tmp.replace(path)


class PanelCache:
    """Per-day panel parquet plus a manifest describing what is in it."""

    def __init__(self, cfg: Config, root):
        self.cfg = cfg
        self.space = space_key(cfg)
        self.root = Path(root) / self.space
        self.panel_dir = self.root / "panel"
        self.combo = opt_combo(cfg)
        self.combo_label = combo_label(self.combo)
        if self.combo == BASELINE_COMBO:
            self.opt_dir = self.root / "opt"
            self._opt_key = "opt_rows"
        else:
            self.opt_dir = self.root / "opt" / self.combo_label
            self._opt_key = "opt_rows@" + self.combo_label
        self.score_dir = self.root / "scores" / "v{}".format(SCORE_VERSION)
        self.panel_dir.mkdir(parents=True, exist_ok=True)
        self.opt_dir.mkdir(parents=True, exist_ok=True)
        self.score_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_path = self.root / "manifest.json"
        self._manifest = self._read_manifest()

    # ------------------------------------------------------------- manifest
    def _read_manifest(self) -> dict:
        blank = {"space": self.space, "engine": ENGINE_VERSION,
                 "config": self._space_config(), "days": {}}
        if not self._manifest_path.exists():
            return blank
        try:
            man = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return blank
        man.setdefault("days", {})
        man["space"] = self.space
        man["engine"] = ENGINE_VERSION
        man["config"] = self._space_config()
        return man

    def _space_config(self) -> dict:
        out = {"data_dir": str(self.cfg.data_dir)}
        for name in SPACE_FIELDS:
            value = getattr(self.cfg, name)
            out[name] = list(value) if isinstance(value, (list, tuple)) else value
        return out

    def _write_manifest(self) -> None:
        tmp = self._manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._manifest, indent=2, default=str),
                       encoding="utf-8")
        tmp.replace(self._manifest_path)

    @property
    def days(self) -> dict:
        return self._manifest["days"]

    # ------------------------------------------------------------ inventory
    def panel_path(self, day) -> Path:
        return self.panel_dir / "{:%Y-%m-%d}.parquet".format(pd.Timestamp(day))

    def opt_path(self, day) -> Path:
        return self.opt_dir / "{:%Y-%m-%d}.parquet".format(pd.Timestamp(day))

    def opt_price_path(self, day) -> Path:
        return self.opt_dir / "{:%Y-%m-%d}.prices.parquet".format(pd.Timestamp(day))

    def has_day(self, day) -> bool:
        """A day counts as built once the manifest records it.

        An empty day writes no parquet on purpose: the absence of money is
        itself a fact, and rebuilding it every time would be wasted work.
        """
        key = "{:%Y-%m-%d}".format(pd.Timestamp(day))
        entry = self.days.get(key)
        if entry is None:
            return False
        return int(entry.get("rows") or 0) == 0 or self.panel_path(day).exists()

    def has_opt(self, day) -> bool:
        key = "{:%Y-%m-%d}".format(pd.Timestamp(day))
        entry = self.days.get(key) or {}
        if entry.get(self._opt_key) is None:
            return False
        return int(entry[self._opt_key]) == 0 or self.opt_path(day).exists()

    def score_path(self, day) -> Path:
        return self.score_dir / "{:%Y-%m-%d}.pkl".format(pd.Timestamp(day))

    def has_score(self, day) -> bool:
        return self.score_path(day).exists()

    def load_score(self, day) -> Optional[dict]:
        path = self.score_path(day)
        if not path.exists():
            return None
        try:
            with path.open("rb") as fh:
                return pickle.load(fh)
        except Exception:                                       # noqa: BLE001
            return None

    def save_score(self, day, state: dict) -> None:
        path = self.score_path(day)
        tmp = path.with_name(path.name + ".tmp")
        with tmp.open("wb") as fh:
            pickle.dump(state, fh, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(path)

    def missing_days(self, start, end) -> List[pd.Timestamp]:
        return [d for d in day_list(start, end) if not self.has_day(d)]

    def missing_opt_days(self, start, end) -> List[pd.Timestamp]:
        """Days whose panel exists but whose optimizer rows do not."""
        return [d for d in day_list(start, end)
                if self.has_day(d) and not self.has_opt(d)]

    def coverage(self, start, end) -> dict:
        """What the UI draws as the coverage bar."""
        rows = []
        for day in day_list(start, end):
            key = "{:%Y-%m-%d}".format(day)
            cached = self.has_day(day)
            entry = self.days.get(key) or {}
            rows.append({
                "day": key,
                "state": "cached" if cached else "missing",
                "rows": int(entry.get("rows") or 0) if cached else 0,
                "turnover": float(entry.get("turnover") or 0.0) if cached else 0.0,
                "matches": int(entry.get("matches") or 0) if cached else 0,
                "optimized": self.has_opt(day),
                "opt_rows": int(entry.get(self._opt_key) or 0) if self.has_opt(day) else 0,
                "scored": self.has_score(day),
                "built_at": entry.get("built_at") if cached else None,
            })
        n_cached = sum(1 for r in rows if r["state"] == "cached")
        return {
            "space": self.space,
            "engine": ENGINE_VERSION,
            "start": "{:%Y-%m-%d}".format(pd.Timestamp(start)),
            "end": "{:%Y-%m-%d}".format(pd.Timestamp(end)),
            "days": rows,
            "n_days": len(rows),
            "n_cached": n_cached,
            "n_missing": len(rows) - n_cached,
            "n_optimized": sum(1 for r in rows if r["optimized"]),
            "rows": sum(r["rows"] for r in rows),
            "turnover": sum(r["turnover"] for r in rows),
            "config": self._space_config(),
        }

    def drop(self, days: Optional[Iterable] = None) -> int:
        """Forget cached days. ``None`` clears the whole space."""
        if days is None:
            shutil.rmtree(self.root, ignore_errors=True)
            self.panel_dir.mkdir(parents=True, exist_ok=True)
            self.opt_dir.mkdir(parents=True, exist_ok=True)
            self._manifest = self._read_manifest()
            self._write_manifest()
            return 0
        n = 0
        for day in days:
            key = "{:%Y-%m-%d}".format(pd.Timestamp(day))
            self.panel_path(day).unlink(missing_ok=True)
            self.score_path(day).unlink(missing_ok=True)
            self.opt_path(day).unlink(missing_ok=True)
            self.opt_price_path(day).unlink(missing_ok=True)
            if self.days.pop(key, None) is not None:
                n += 1
        self._write_manifest()
        return n

    # ---------------------------------------------------------------- build
    def build(self, days: Sequence, progress: Optional[Progress] = None,
              should_cancel: Optional[Callable[[], bool]] = None,
              optimize: bool = False) -> dict:
        """Build the given days, one calendar month of raw data at a time.

        Reading raw parquet dominates the cost, so days are grouped by month
        and each month is read once even when only a few of its days are
        wanted -- which is what makes extending a window cheap.
        """
        days = [pd.Timestamp(d).normalize() for d in days]
        if not days:
            return {"built": 0, "rows": 0, "turnover": 0.0, "seconds": 0.0}

        def emit(**kw):
            if progress is not None:
                progress(kw)

        def check():
            if should_cancel is not None and should_cancel():
                raise Cancelled()

        started = pd.Timestamp.utcnow()
        lo, hi = min(days), max(days)
        emit(stage="static", message="loading Pool_Details + Match_Information")
        check()
        pools_df, matches_df = load_static(
            self.cfg.data_dir, lo, hi + pd.Timedelta(days=1))
        emit(stage="static", message="pools={:,} matches={:,}".format(
            len(pools_df), len(matches_df)))

        stage_cls = None
        if optimize:
            from .optimize import OptimizerStage
            stage_cls = OptimizerStage

        by_month: Dict[pd.Timestamp, List[pd.Timestamp]] = {}
        for day in sorted(days):
            by_month.setdefault(day.to_period("M").start_time, []).append(day)

        built = 0
        total_rows = 0
        total_money = 0.0
        done = 0
        for m_start, want in by_month.items():
            check()
            m_end = m_start + pd.offsets.MonthBegin(1)
            emit(stage="load", month="{:%Y-%m}".format(m_start),
                 message="reading raw parquet for {:%Y-%m}".format(m_start))
            cache = MonthCache(self.cfg, min(want), m_end)
            emit(stage="load", month="{:%Y-%m}".format(m_start),
                 message="odds={:,} inv={:,} params={:,} keys={:,}".format(
                     len(cache.odds), len(cache.inv),
                     len(cache.params), len(cache.keys)))
            for day in want:
                check()
                facts = build_day(self.cfg, day, cache, pools_df, matches_df)
                rows = 0 if facts is None or facts.empty else len(facts)
                money = 0.0 if not rows else float(facts["turnover"].sum())
                matches = 0 if not rows else int(facts["match_id"].nunique())
                self._write_day(day, facts, rows)
                opt_rows = None
                if stage_cls is not None and rows:
                    opt_rows = self._write_opt(day, facts, stage_cls)
                entry = DayState(
                    day="{:%Y-%m-%d}".format(day), rows=rows, turnover=money,
                    matches=matches,
                    built_at=pd.Timestamp.utcnow().isoformat(timespec="seconds"),
                    opt_rows=None,
                ).to_dict()
                if opt_rows is not None:
                    entry[self._opt_key] = opt_rows
                self.days["{:%Y-%m-%d}".format(day)] = entry
                self._write_manifest()
                built += 1
                done += 1
                total_rows += rows
                total_money += money
                emit(stage="day", day="{:%Y-%m-%d}".format(day), rows=rows,
                     turnover=money, opt_rows=opt_rows,
                     done=done, total=len(days))
            del cache

        return {
            "built": built,
            "rows": total_rows,
            "turnover": total_money,
            "seconds": round((pd.Timestamp.utcnow() - started).total_seconds(), 1),
        }

    def put_day(self, day, facts, opt_rows: Optional[int] = None) -> None:
        """Write one panel day and update the manifest. Used by ingest and tests."""
        day = pd.Timestamp(day).normalize()
        rows = 0 if facts is None or getattr(facts, "empty", True) else len(facts)
        money = 0.0 if not rows else float(facts["turnover"].sum()) if "turnover" in facts.columns else 0.0
        matches = 0 if not rows else (
            int(facts["match_id"].nunique()) if "match_id" in facts.columns else 0)
        self._write_day(day, facts, rows)
        key = "{:%Y-%m-%d}".format(day)
        prev = self.days.get(key) or {}
        kept = opt_rows if opt_rows is not None else prev.get("opt_rows")
        self.days[key] = DayState(
            day=key, rows=rows, turnover=money, matches=matches,
            built_at=pd.Timestamp.utcnow().isoformat(timespec="seconds"),
            opt_rows=kept,
        ).to_dict()
        self._write_manifest()

    def _write_day(self, day, facts, rows: int) -> None:
        self.score_path(day).unlink(missing_ok=True)
        path = self.panel_path(day)
        if not rows:
            path.unlink(missing_ok=True)
            return
        _atomic_parquet(facts, path)

    def _write_opt(self, day, facts, stage_cls) -> int:
        """Solve one day's sampled buckets and store rows plus prices.

        Bucket rows carry the E[GM] comparison; price rows carry the
        per-selection board, so the replication check survives caching.
        """
        stage = stage_cls(self.cfg)
        stage.update(facts)
        rows = list(getattr(stage, "rows", None) or [])
        prices = list(getattr(stage, "prices", None) or [])
        self.opt_path(day).unlink(missing_ok=True)
        self.opt_price_path(day).unlink(missing_ok=True)
        if not rows:
            return 0
        _atomic_parquet(pd.DataFrame(rows), self.opt_path(day))
        if prices:
            _atomic_parquet(pd.DataFrame(prices), self.opt_price_path(day))
        return len(rows)

    def build_optimizer(self, days: Sequence, progress: Optional[Progress] = None,
                        should_cancel: Optional[Callable[[], bool]] = None) -> dict:
        """Optimizer-only pass over days whose panel is already cached."""
        from .optimize import OptimizerStage

        days = [pd.Timestamp(d).normalize() for d in days if self.has_day(d)]
        if not days:
            return {"built": 0, "rows": 0, "seconds": 0.0}
        started = pd.Timestamp.utcnow()
        total = 0
        for i, day in enumerate(days, start=1):
            if should_cancel is not None and should_cancel():
                raise Cancelled()
            facts = self.load_day(day)
            key = "{:%Y-%m-%d}".format(day)
            n = 0 if facts is None or facts.empty else \
                self._write_opt(day, facts, OptimizerStage)
            entry = self.days.get(key) or {}
            entry[self._opt_key] = n
            self.days[key] = entry
            self._write_manifest()
            total += n
            if progress is not None:
                progress({"stage": "optimize", "day": key, "opt_rows": n,
                          "done": i, "total": len(days)})
        return {"built": len(days), "rows": total,
                "seconds": round((pd.Timestamp.utcnow() - started).total_seconds(), 1)}

    # ----------------------------------------------------------------- read
    def load_day(self, day, columns: Optional[Sequence[str]] = None):
        path = self.panel_path(day)
        if not path.exists():
            return None
        return pd.read_parquet(path, columns=list(columns) if columns else None)

    def iter_range(self, start, end, columns: Optional[Sequence[str]] = None):
        for day in day_list(start, end):
            frame = self.load_day(day, columns)
            if frame is not None and not frame.empty:
                yield day, frame

    def load_opt_range(self, start, end):
        """Cached optimizer buckets and prices for a window."""
        rows, prices = [], []
        for day in day_list(start, end):
            path = self.opt_path(day)
            if path.exists():
                frame = pd.read_parquet(path)
                if not frame.empty:
                    rows.append(frame)
            ppath = self.opt_price_path(day)
            if ppath.exists():
                frame = pd.read_parquet(ppath)
                if not frame.empty:
                    prices.append(frame)
        return (pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(),
                pd.concat(prices, ignore_index=True) if prices else pd.DataFrame())


def open_cache(cfg: Config, root) -> PanelCache:
    return PanelCache(cfg, root)


def list_spaces(root) -> List[dict]:
    """Every cache space under ``root``, newest build first."""
    base = Path(root)
    if not base.exists():
        return []
    out = []
    for child in sorted(base.iterdir()):
        man_path = child / "manifest.json"
        if not man_path.exists():
            continue
        try:
            man = json.loads(man_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        days = man.get("days") or {}
        built = [d.get("built_at") for d in days.values() if d.get("built_at")]
        out.append({
            "space": child.name,
            "engine": man.get("engine"),
            "config": man.get("config") or {},
            "n_days": len(days),
            "rows": sum(int(d.get("rows") or 0) for d in days.values()),
            "turnover": sum(float(d.get("turnover") or 0.0) for d in days.values()),
            "first_day": min(days) if days else None,
            "last_day": max(days) if days else None,
            "last_built": max(built) if built else None,
        })
    out.sort(key=lambda r: r.get("last_built") or "", reverse=True)
    return out
