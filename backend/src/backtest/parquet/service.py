"""The desk-facing parquet backtest service.

What the API calls, and the only place that knows how the three pieces fit:

    cache.py     per-day panels, built once, extended incrementally
    adapter.py   those days -> the wide fact table the workbench slices
    jobs.py      long builds, with progress and cancel

The flow the page drives:

    1. coverage(window)          which days are already built
    2. start_build(window)       build only the missing ones, then register a run
    3. the existing /slice endpoints                   re-score any stack, instantly

Step 3 is why changing model never re-runs anything: every model already has
its own column on every row, so a different stack is a different column to
read, not a different backtest.

Change Log:
-----------
2026-09-15      Initialize (W06 parquet workbench)
2026-09-24      Register scores turnover/belief on every cached row; solver stays optional
2026-09-24      Publish optimizer totals after each solved day so the page can refresh live
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence

import pandas as pd

from src.core import config as core_config
from src.core.logging import get_logger

from src.backtest.slice import compare_cached, invalidate, slice_cached
from src.backtest.store import get_backtest_store

from . import adapter
from .cache import (
    ENGINE_VERSION, Cancelled, PanelCache, day_list, default_cache_root,
    facts_dir_guesses, ingest_flat_facts, list_spaces, resolve_cache_root,
)
from .config import Config
from .evaluate import Accumulator, score_frames
from .jobs import Job, get_job_manager
from .probe import run_probe

log = get_logger(__name__)

# Parquet runs land in the same store as the labs runs, so /api/backtest/latest
# and the slice endpoints serve them without knowing where the rows came from.
RUN_PREFIX = "pq"

# Where cached days live. Beside the sqlite file, so a desk that copies the
# data folder to another machine copies its cache with it.
CACHE_DIRNAME = "parquet_cache"

# A window this wide is almost always a mistake in a form field rather than a
# real request, and building it would tie up the only worker for hours.
MAX_DAYS = 400

# Guard on the assembled frame. Slicing happens in memory in the API process,
# so an unbounded window would eventually take the process down; when this
# trims, the report says which days were dropped rather than quietly shrinking.
DEFAULT_MAX_ROWS = 4_000_000

# Verdicts that belong to the accuracy half of the report. Live optimizer
# publishes replace only the solver lines, so these must not be wiped.
ACCURACY_TOPICS = {
    "next 5min = last 5min",
    "market-wide money per bucket",
    "true prob = 1 / true odds",
    "gross margin",
}


def cache_root(cfg: Optional[Config] = None) -> Path:
    """Shared panel cache. Same path ``3-run.bat`` writes."""
    return default_cache_root(cfg or Config())


def _legacy_cache_root() -> Path:
    return Path(core_config.DB_PATH).parent / CACHE_DIRNAME


# What the three dropdowns on the page offer. The first entry is the baseline.
TURNOVER_CHOICES = {
    "persist": "Last 5 min (baseline)",
    "ma3": "Mean of last 3 buckets",
    "ema": "EMA",
}
BELIEF_CHOICES = {
    "p_true": "1 / HKJC true odds (baseline)",
    "p_sell": "Public odds, de-margined",
}
OBJECTIVE_CHOICES = {
    "egm": "Max expected gross margin (baseline)",
}


def selection_of(cfg: Config) -> dict:
    return {
        "turnover": cfg.optimize_demand.replace("f_", ""),
        "belief": cfg.optimize_belief,
        "objective": cfg.optimize_objective,
    }


def config_from_request(req) -> Config:
    """Request -> engine Config, with the shaping knobs defaulted sensibly.

    No pools means every pool: evaluation reads the whole book even when the
    solver only prices some of it.
    """
    pools = list(req.pools) if getattr(req, "pools", None) else []
    if len(pools) == 1 and pools[0].lower() == "all":
        pools = []
    turnover = getattr(req, "turnover_model", None)
    demand = "f_{}".format(turnover) if turnover in TURNOVER_CHOICES else \
        str(getattr(req, "optimize_demand", None) or "f_persist")
    belief = getattr(req, "belief_model", None)
    belief = belief if belief in BELIEF_CHOICES else "p_true"
    objective = getattr(req, "objective", None)
    objective = objective if objective in OBJECTIVE_CHOICES else "egm"
    return Config(
        data_dir=Path(req.data_dir or Config().data_dir),
        start_date=str(req.start),
        end_date=str(req.end),
        bucket_minutes=int(getattr(req, "bucket_minutes", None) or 5),
        pools=pools,
        prematch_window_min=int(getattr(req, "prematch_window_min", None) or 360),
        full_span=bool(getattr(req, "full_span", False)),
        lookback_days=int(getattr(req, "lookback_days", None) or 2),
        true_prob_source=str(getattr(req, "true_prob_source", None) or "auto"),
        normalize_book=bool(getattr(req, "normalize_book", True)),
        optimize_every=int(getattr(req, "optimize_every", None) or 6),
        optimize_max_buckets=int(getattr(req, "optimize_max_buckets", None) or 20000),
        optimize_starts=int(getattr(req, "optimize_starts", None) or 3),
        optimize_demand=demand,
        optimize_belief=belief,
        optimize_objective=objective,
        hdc_sign=str(getattr(req, "hdc_sign", None) or "home"),
        cache_dir=Path(req.cache_dir) if getattr(req, "cache_dir", None) else None,
    )


def open_cache(cfg: Config) -> PanelCache:
    root = resolve_cache_root(cfg, extra_roots=[_legacy_cache_root()])
    cache = PanelCache(cfg, root)
    absorbed = 0
    for folder in facts_dir_guesses(cfg):
        absorbed += ingest_flat_facts(cache, folder)
    if absorbed:
        log.info("absorbed %d day(s) from a 3-run facts folder into %s",
                 absorbed, cache.root)
    return cache


def check_window(cfg: Config) -> List[pd.Timestamp]:
    days = day_list(cfg.start_date, cfg.end_date)
    if not days:
        raise ValueError("end date is before start date")
    if len(days) > MAX_DAYS:
        raise ValueError("window is {} days; the cap is {}".format(len(days), MAX_DAYS))
    return days


def coverage(cfg: Config) -> dict:
    check_window(cfg)
    cache = open_cache(cfg)
    cov = cache.coverage(cfg.start_date, cfg.end_date)
    cov["missing_opt_days"] = ["{:%Y-%m-%d}".format(d) for d in
                              cache.missing_opt_days(cfg.start_date, cfg.end_date)]
    cov["data_dir_exists"] = Path(cfg.data_dir).exists()
    cov["runs"] = runs_for_space(cov["space"])
    cov["cache_root"] = str(cache.root)
    return cov


def runs_for_space(space: str, limit: int = 8) -> List[dict]:
    """Stored runs built from the same cache space, newest first."""
    rows = get_backtest_store().list(limit=60)
    tag = "-{}".format(space)
    return [r for r in rows
            if str(r.get("run_id", "")).startswith(RUN_PREFIX)
            and tag in str(r.get("run_id", ""))][:limit]


def spaces() -> List[dict]:
    seen = set()
    out = []
    for root in (cache_root(), _legacy_cache_root()):
        for item in list_spaces(root):
            key = (item.get("space"), str(root))
            if key in seen:
                continue
            seen.add(key)
            item["cache_root"] = str(root)
            out.append(item)
    out.sort(key=lambda r: r.get("last_built") or "", reverse=True)
    return out


def probe(cfg: Config) -> dict:
    """Report on the raw folders without building anything."""
    return run_probe(cfg)


# --------------------------------------------------------------------- build
def start_build(cfg: Config, optimize: bool = False,
                rebuild: bool = False,
                calibrators: Sequence[str] = adapter.DEFAULT_CALIBRATORS,
                max_rows: int = DEFAULT_MAX_ROWS) -> Job:
    """Build the missing days of a window, then register a run over all of it."""
    check_window(cfg)
    request = {
        "start": cfg.start_date,
        "end": cfg.end_date,
        "pools": list(cfg.pools),
        "data_dir": str(cfg.data_dir),
        "optimize": bool(optimize),
        "rebuild": bool(rebuild),
        "calibrators": list(calibrators),
    }

    def work(job: Job) -> dict:
        cache = open_cache(cfg)
        if rebuild:
            n = cache.drop(day_list(cfg.start_date, cfg.end_date))
            job.note("dropped {} cached day(s) on request".format(n))

        missing = cache.missing_days(cfg.start_date, cfg.end_date)
        job.update({"stage": "plan", "total": len(missing),
                    "message": "{} day(s) to build, {} already cached".format(
                        len(missing),
                        len(day_list(cfg.start_date, cfg.end_date)) - len(missing))})
        built = {"built": 0, "rows": 0, "turnover": 0.0, "seconds": 0.0}
        if missing:
            built = cache.build(missing, progress=job.update,
                                should_cancel=lambda: job.cancelled,
                                optimize=optimize)

        opt_built = None
        if optimize:
            todo = cache.missing_opt_days(cfg.start_date, cfg.end_date)
            if todo:
                job.update({"stage": "plan", "total": len(todo),
                            "message": "solving {} cached day(s)".format(len(todo))})
                opt_built = cache.build_optimizer(
                    todo, progress=job.update,
                    should_cancel=lambda: job.cancelled)

        run = register_run(cfg, cache, calibrators=calibrators,
                           max_rows=max_rows, job=job)
        job.run_id = run["run_id"]
        return {"built": built, "optimizer": opt_built, "run_id": run["run_id"],
                "rows": run["n_rows"], "days": run["coverage"]["n_cached"]}

    return get_job_manager().start("parquet-build", request, work)


def expected_run_id(cfg: Config, cache: PanelCache) -> str:
    return "{}-{}-{}-{}-{}".format(
        RUN_PREFIX, cfg.start_date.replace("-", ""),
        cfg.end_date.replace("-", ""), cache.space, cache.combo_label)


# ---------------------------------------------------------------------- open
def _score_day(cache: PanelCache, day) -> dict:
    """One day's additive sums, from its score file or from the panel."""
    state = cache.load_score(day)
    if state is not None:
        return state
    acc = Accumulator(sample_rows=800)
    frame = cache.load_day(day)
    if frame is not None and not frame.empty:
        acc.update(day, frame)
    state = acc.to_state()
    cache.save_score(day, state)
    return state


def _selected_kpis(accuracy: dict, sel: dict) -> dict:
    turnover = (accuracy.get("turnover") or {})
    t = (turnover.get("models") or {}).get("active|{}".format(sel["turnover"])) or {}
    market = (turnover.get("bucket_level") or {}).get(sel["turnover"]) or {}
    belief = accuracy.get("belief") or {}
    b = (belief.get("models") or {}).get(sel["belief"]) or {}
    cal = (belief.get("calibration") or {}).get(sel["belief"]) or {}
    return {
        "persist_wape": t.get("wape"),
        "persist_mae": t.get("mae"),
        "market_wape": market.get("wape"),
        "money_ece": cal.get("money_ece"),
        "money_log_loss": b.get("money_log_loss"),
    }


def publish_open(cfg: Config, cache: PanelCache, acc: Accumulator,
                 calibrators: Sequence[str], started: float,
                 live: Optional[dict] = None) -> dict:
    """Store what the page shows, from the days merged so far."""
    run_id = expected_run_id(cfg, cache)
    cov = cache.coverage(cfg.start_date, cfg.end_date)
    accuracy = acc.finalize()
    opt_rows, opt_prices = cache.load_opt_range(cfg.start_date, cfg.end_date)
    optimizer = None
    if not opt_rows.empty:
        from .optimize import report_from_frames
        optimizer = report_from_frames(cfg, opt_rows, opt_prices)
    stub = pd.DataFrame(columns=["t_actual", "settled", "as_of", "match_id"])
    report = build_report(run_id, cfg, cov, {}, stub, optimizer, calibrators,
                          accuracy=accuracy)
    sel = selection_of(cfg)
    report["selection"] = sel
    report["choices"] = choices()
    report["n_rows"] = int(cov.get("rows") or 0)
    report["n_matches"] = int((accuracy.get("coverage") or {}).get("matches") or 0)
    report["headline"].update({
        "rows": int(cov.get("rows") or 0),
        "turnover": float(cov.get("turnover") or 0.0),
        "matches": report["n_matches"],
        **_selected_kpis(accuracy, sel),
    })
    report["window"]["turnover"] = float(cov.get("turnover") or 0.0)
    report["workbench"]["has_facts"] = False
    report["seconds"] = round(time.time() - started, 2)
    report["live"] = live
    if live and live.get("stage") == "optimize":
        report["optimizer_live"] = {"solving": True, "day": live.get("day"),
                                    "done": live.get("done"), "total": live.get("total")}
    get_backtest_store().save(report)
    return report


def choices() -> dict:
    return {
        "turnover": [{"id": k, "label": v} for k, v in TURNOVER_CHOICES.items()],
        "belief": [{"id": k, "label": v} for k, v in BELIEF_CHOICES.items()],
        "objective": [{"id": k, "label": v} for k, v in OBJECTIVE_CHOICES.items()],
    }


def start_open(cfg: Config,
               calibrators: Sequence[str] = adapter.DEFAULT_CALIBRATORS,
               solve: bool = True) -> Job:
    """Show a window for one turnover x belief x objective choice.

    Stored day scores and solved days are read straight back. Anything
    missing is produced here, and the page is republished after each new
    day so numbers appear while the rest is still running.
    """
    check_window(cfg)
    request = {"start": cfg.start_date, "end": cfg.end_date,
               "pools": list(cfg.pools), "data_dir": str(cfg.data_dir),
               **selection_of(cfg)}

    def work(job: Job) -> dict:
        started = time.time()
        cache = open_cache(cfg)
        job.run_id = expected_run_id(cfg, cache)
        acc = Accumulator()
        days = day_list(cfg.start_date, cfg.end_date)

        def publish(stage: str, **extra) -> None:
            live = {"stage": stage, **extra}
            publish_open(cfg, cache, acc, calibrators, started, live=live)
            job.update({"stage": stage, "published": True, **extra})

        have = [d for d in days if cache.has_day(d)]
        fresh = [d for d in have if not cache.has_score(d)]
        job.note("{} cached day(s), {} already scored".format(len(have), len(have) - len(fresh)))
        for d in have:
            if cache.has_score(d):
                acc.absorb(_score_day(cache, d))
        if len(have) > len(fresh):
            publish("score", done=len(have) - len(fresh), total=len(days))
        for i, d in enumerate(fresh, start=1):
            if job.cancelled:
                raise Cancelled()
            acc.absorb(_score_day(cache, d))
            publish("score", day="{:%Y-%m-%d}".format(d),
                    done=len(have) - len(fresh) + i, total=len(days))

        missing = cache.missing_days(cfg.start_date, cfg.end_date)
        if missing:
            job.note("building {} day(s) from raw parquet".format(len(missing)))
            built = [0]

            def on_build(event: dict) -> None:
                if event.get("stage") != "day":
                    job.update(event)
                    return
                built[0] += 1
                acc.absorb(_score_day(cache, event["day"]))
                publish("build", day=event["day"], done=len(have) + built[0], total=len(days))

            cache.build(missing, progress=on_build, should_cancel=lambda: job.cancelled)

        todo = cache.missing_opt_days(cfg.start_date, cfg.end_date) if solve else []
        publish("optimize" if todo else "done", done=0, total=len(todo))
        if todo:
            job.note("solving {} day(s) for {}".format(len(todo), cache.combo_label))

            def on_opt(event: dict) -> None:
                publish("optimize", day=event.get("day"), done=event.get("done"),
                        total=event.get("total"))

            cache.build_optimizer(todo, progress=on_opt, should_cancel=lambda: job.cancelled)
            publish("done")
        return {"run_id": job.run_id, "days": len(days), "solved": len(todo)}

    return get_job_manager().start("parquet-open", request, work)


def start_optimizer(cfg: Config, rebuild: bool = False,
                    calibrators: Sequence[str] = adapter.DEFAULT_CALIBRATORS,
                    max_rows: int = DEFAULT_MAX_ROWS) -> Job:
    """Solve TG/SUP for a window whose panels are already cached.

    Each finished day is published onto the stored run so the Optimize tab
    can refresh while the solver is still working. Accuracy numbers are
    left alone.
    """
    check_window(cfg)
    request = {"start": cfg.start_date, "end": cfg.end_date,
               "pools": list(cfg.pools), "data_dir": str(cfg.data_dir),
               "optimize": True, "rebuild": bool(rebuild)}

    def work(job: Job) -> dict:
        cache = open_cache(cfg)
        days = day_list(cfg.start_date, cfg.end_date)
        todo = [d for d in days if cache.has_day(d)] if rebuild else \
            cache.missing_opt_days(cfg.start_date, cfg.end_date)
        run_id = expected_run_id(cfg, cache)
        job.run_id = run_id
        store = get_backtest_store()
        if store.get(run_id) is None:
            job.note("registering the window so the dashboard can update as days solve")
            run = register_run(cfg, cache, calibrators=calibrators,
                               max_rows=max_rows, job=job)
            job.run_id = run["run_id"]

        def publish(live: bool) -> None:
            try:
                publish_optimizer(cfg, cache, job=job, live=live)
            except Exception as exc:                              # noqa: BLE001
                log.warning("live optimizer publish failed (%s)", exc)
                job.note("dashboard update failed: {}".format(exc))

        publish(live=True)
        if not todo:
            job.note("every cached day in this window is already solved")
        else:
            job.update({"stage": "plan", "total": len(todo),
                        "message": "solving {} day(s)".format(len(todo))})

            def on_progress(event: dict) -> None:
                job.update(event)
                if event.get("stage") == "optimize":
                    publish(live=True)

            cache.build_optimizer(todo, progress=on_progress,
                                  should_cancel=lambda: job.cancelled)
        publish(live=False)
        return {"run_id": job.run_id, "solved_days": len(todo)}

    return get_job_manager().start("parquet-optimize", request, work)


def _score_publishing(cfg: Config, cache: PanelCache, cov: dict,
                     calibrators: Sequence[str], run_id: str,
                     job: Optional[Job], started: float) -> dict:
    """Score cached days one at a time and store the page after each day."""
    days = [d for d in day_list(cfg.start_date, cfg.end_date) if cache.has_day(d)]
    acc = Accumulator()
    store = get_backtest_store()
    stub = pd.DataFrame(columns=["t_actual", "settled", "as_of", "match_id"])
    if job is not None:
        job.note("scoring turnover and belief, {} day(s)".format(len(days)))
    for i, day in enumerate(days, start=1):
        frame = cache.load_day(day)
        acc.update(day, frame if frame is not None else pd.DataFrame())
        accuracy = acc.finalize()
        if job is None:
            continue
        report = build_report(run_id, cfg, cov, {}, stub, None, calibrators, accuracy=accuracy)
        report["seconds"] = round(time.time() - started, 2)
        report["headline"]["rows"] = cov.get("rows") or 0
        report["headline"]["turnover"] = cov.get("turnover") or 0.0
        report["scoring_live"] = {"day": "{:%Y-%m-%d}".format(day), "done": i, "total": len(days)}
        store.save(report)
        job.run_id = run_id
        job.update({"stage": "score", "day": "{:%Y-%m-%d}".format(day),
                    "done": i, "total": len(days)})
    return acc.finalize()


def start_register(cfg: Config,
                   calibrators: Sequence[str] = adapter.DEFAULT_CALIBRATORS,
                   max_rows: int = DEFAULT_MAX_ROWS) -> Job:
    """Score a cached window in the background.

    Open cached used to do this inside the web request. On a real 77-day
    cache that request outlives the page proxy and the browser reports
    socket hang up, with nothing on screen.
    """
    check_window(cfg)
    request = {"start": cfg.start_date, "end": cfg.end_date,
               "pools": list(cfg.pools), "data_dir": str(cfg.data_dir)}

    def work(job: Job) -> dict:
        cache = open_cache(cfg)
        run = register_run(cfg, cache, calibrators=calibrators,
                           max_rows=max_rows, job=job)
        job.run_id = run["run_id"]
        return {"run_id": run["run_id"], "rows": run["n_rows"]}

    return get_job_manager().start("parquet-register", request, work)


# ------------------------------------------------------------------ register
def register_run(cfg: Config, cache: PanelCache,
                 calibrators: Sequence[str] = adapter.DEFAULT_CALIBRATORS,
                 max_rows: int = DEFAULT_MAX_ROWS,
                 job: Optional[Job] = None) -> dict:
    """Assemble a window into facts and store it as a run the UI can open."""
    started = time.time()

    def note(message: str) -> None:
        if job is not None:
            job.note(message)

    cov = cache.coverage(cfg.start_date, cfg.end_date)
    run_id = expected_run_id(cfg, cache)
    accuracy = _score_publishing(cfg, cache, cov, calibrators, run_id, job, started)

    note("assembling the fact table")
    facts, meta = adapter.assemble(
        cache, cfg.start_date, cfg.end_date, calibrators=calibrators,
        max_rows=max_rows, progress=job.update if job else None)
    if facts.empty:
        raise ValueError(
            "no cached rows in {} .. {}. Build the window first, and check "
            "`probe` if the folders look empty.".format(cfg.start_date, cfg.end_date))

    opt_rows, opt_prices = cache.load_opt_range(cfg.start_date, cfg.end_date)
    optimizer = None
    if not opt_rows.empty:
        from .optimize import report_from_frames
        note("summarising {:,} solved bucket(s)".format(len(opt_rows)))
        optimizer = report_from_frames(cfg, opt_rows, opt_prices)

    report = build_report(run_id, cfg, cov, meta, facts, optimizer, calibrators,
                          accuracy=accuracy)
    report["seconds"] = round(time.time() - started, 2)

    store = get_backtest_store()
    store.save_facts(run_id, facts)
    store.save(report)
    note("stored run {} with {:,} rows".format(run_id, len(facts)))

    # Rebuilding the same window replaces the rows, so any answer we cached
    # against this run_id is stale. Then pay for the opening view once, here,
    # instead of making the first page load wait for it.
    invalidate(run_id)
    note("warming the opening view")
    warm_default(run_id, facts, report)
    return report


def score_accuracy(cache: PanelCache, start: str, end: str,
                   sample_rows: int = 6000) -> dict:
    """Turnover WAPE and belief ECE on the full cached window.

    Walks the panel files, not the assembled fact table, so a memory cap on
    the workbench does not shrink these two scores. The solver is a later,
    sampled step and is not run here.
    """
    pairs = ((day, cache.load_day(day)) for day in day_list(start, end))
    return score_frames(pairs, sample_rows=sample_rows)


def apply_optimizer(report: dict, optimizer: Optional[dict], cov: dict,
                    live: Optional[dict] = None) -> dict:
    """Patch solver numbers onto a stored run without touching accuracy."""
    acc = list((report.get("accuracy") or {}).get("verdict") or [])
    if not acc:
        acc = [v for v in (report.get("verdict") or [])
               if v.get("topic") in ACCURACY_TOPICS]
    opt_verdict = list((optimizer or {}).get("verdict") or [])
    opt_all = ((optimizer or {}).get("totals") or {}).get("all") or {}
    report["coverage"] = cov
    report["optimizer"] = optimizer
    report["verdict"] = acc + opt_verdict
    report["headline"] = {
        **(report.get("headline") or {}),
        "optimized_days": cov.get("n_optimized"),
        "opt_lift_pct": opt_all.get("lift_vs_actual_pct"),
        "opt_margin": opt_all.get("margin_opt"),
        "board_margin": opt_all.get("margin_actual_odds"),
        "days_better": opt_all.get("buckets_better") or 0,
        "days_worse": opt_all.get("buckets_worse") or 0,
    }
    report["optimizer_live"] = live
    return report


def publish_optimizer(cfg: Config, cache: PanelCache,
                      job: Optional[Job] = None, live: bool = False) -> Optional[dict]:
    """Rewrite the stored run's optimizer section from days solved so far."""
    run_id = expected_run_id(cfg, cache)
    store = get_backtest_store()
    report = store.get(run_id)
    if report is None:
        return None
    cov = cache.coverage(cfg.start_date, cfg.end_date)
    opt_rows, opt_prices = cache.load_opt_range(cfg.start_date, cfg.end_date)
    optimizer = None
    if not opt_rows.empty:
        from .optimize import report_from_frames
        optimizer = report_from_frames(cfg, opt_rows, opt_prices)
    progress = (job.progress if job is not None else None) or {}
    live_state = {
        "solving": bool(live),
        "day": progress.get("day"),
        "done": progress.get("done") or 0,
        "total": progress.get("total") or 0,
        "opt_rows": progress.get("opt_rows"),
    }
    apply_optimizer(report, optimizer, cov, live=live_state if live else None)
    store.save(report)
    if job is not None:
        job.run_id = run_id
        job.update({
            "n_optimized": cov.get("n_optimized"),
            "opt_lift_pct": (report.get("headline") or {}).get("opt_lift_pct"),
            "published": True,
        })
    return report


def warm_default(run_id: str, facts: pd.DataFrame, report: dict) -> None:
    """Score the views the page opens on, so the first load is a cache hit.

    Mirrors the opening state of workbench.tsx and compare.tsx. A miss here only
    costs the page its old wait, so a failure is logged and swallowed.
    """
    wb = report.get("workbench") or {}
    base = wb.get("baselines") or {}
    left = {
        "turnover": base.get("turnover", "persistence"),
        "true_prob": base.get("true_prob", "hkjc_true"),
        "calibrator": base.get("calibrator", "raw"),
        "algo": base.get("algo", "hold"),
    }
    try:
        slice_cached(run_id, facts, group_by="clock_bin", filters={}, **left)
    except Exception as exc:                                      # noqa: BLE001
        log.warning("warming the slice failed (%s)", exc)
        return

    # B opens on the run's own winner, else on any other turnover it carries.
    head = report.get("headline") or {}
    have = list(wb.get("turnover") or [])
    best_t = head.get("best_turnover")
    other = next((t for t in have if t != left["turnover"]), None)
    token = head.get("best_true_odds") or ""
    source, _, cal = token.partition("/")
    right = {
        "turnover": (best_t if best_t and best_t != left["turnover"]
                     else (other or best_t or left["turnover"])),
        "true_prob": source or "hkjc_true",
        "calibrator": cal or "raw",
        "algo": left["algo"],
    }
    try:
        compare_cached(run_id, facts, left=left, right=right,
                       group_by="clock_bin", filters={})
    except Exception as exc:                                      # noqa: BLE001
        log.warning("warming the compare failed (%s)", exc)


def build_report(run_id: str, cfg: Config, cov: dict, meta: dict,
                 facts: pd.DataFrame, optimizer: Optional[dict],
                 calibrators: Sequence[str],
                 accuracy: Optional[dict] = None) -> dict:
    """The payload the page reads. Same contract as a labs run, minus labs."""
    settled = int(facts["settled"].sum()) if "settled" in facts.columns else 0
    turnover = float(facts["t_actual"].sum()) if "t_actual" in facts.columns else 0.0
    models = meta.get("models") or adapter.models_present(facts)

    acc_verdict = list((accuracy or {}).get("verdict") or [])
    opt_verdict = list((optimizer or {}).get("verdict") or [])
    return {
        "run_id": run_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "parquet",
        "config": {
            **cfg.to_dict(),
            "calibrators": list(calibrators),
            "engine": ENGINE_VERSION,
            "space": cov["space"],
            "bucket_minutes": cfg.bucket_minutes,
        },
        "n_rows": int(len(facts)),
        "n_fact_rows": int(len(facts)),
        "n_ticks": int(facts["as_of"].nunique()) if "as_of" in facts.columns else 0,
        "n_matches": int(facts["match_id"].nunique()) if "match_id" in facts.columns else 0,
        "n_days": cov["n_cached"],
        "coverage": cov,
        "window": {
            "start": cfg.start_date,
            "end": cfg.end_date,
            "days_scored": meta.get("days") or [],
            "days_empty": meta.get("empty_days") or [],
            "days_trimmed": meta.get("trimmed_days") or [],
            "settled_rows": settled,
            "turnover": turnover,
            "memory_mb": meta.get("memory_mb"),
        },
        "headline": headline(facts, cov, turnover, settled, optimizer, accuracy),
        "catalog": adapter.catalog(calibrators),
        "accuracy": accuracy,
        "optimizer": optimizer,
        "verdict": acc_verdict + opt_verdict,
        "workbench": {
            "has_facts": True,
            "baselines": dict(adapter.MVP_STACK),
            "turnover": models["turnover"],
            "beliefs": models["beliefs"],
            "algos": models["algos"],
            "filters": {},
        },
    }


def _accuracy_kpis(accuracy: Optional[dict]) -> dict:
    persist = ((accuracy or {}).get("turnover") or {}).get("models", {}).get("active|persist") or {}
    market = ((accuracy or {}).get("turnover") or {}).get("bucket_level", {}).get("persist") or {}
    ptrue = ((accuracy or {}).get("belief") or {}).get("models", {}).get("p_true") or {}
    cal = ((accuracy or {}).get("belief") or {}).get("calibration", {}).get("p_true") or {}
    gm = ((accuracy or {}).get("gm") or {}).get("overall") or {}
    return {
        "persist_wape": persist.get("wape"),
        "persist_mae": persist.get("mae"),
        "market_wape": market.get("wape"),
        "money_ece": cal.get("money_ece"),
        "money_log_loss": ptrue.get("money_log_loss"),
        "realized_margin": gm.get("realized_margin"),
        "expected_margin": gm.get("expected_margin"),
    }


def headline(facts: pd.DataFrame, cov: dict, turnover: float, settled: int,
             optimizer: Optional[dict],
             accuracy: Optional[dict] = None) -> dict:
    """The numbers at the top. WAPE and ECE land as soon as panels exist."""
    opt_all = ((optimizer or {}).get("totals") or {}).get("all") or {}
    return {
        "mode": "parquet",
        "days": cov["n_cached"],
        "rows": int(len(facts)),
        "settled_rows": settled,
        "turnover": turnover,
        "matches": int(facts["match_id"].nunique()) if "match_id" in facts.columns else 0,
        "optimized_days": cov["n_optimized"],
        "opt_lift_pct": opt_all.get("lift_vs_actual_pct"),
        "opt_margin": opt_all.get("margin_opt"),
        "board_margin": opt_all.get("margin_actual_odds"),
        **_accuracy_kpis(accuracy),
        # the labs headline fields the shared header still reads
        "best_turnover": None,
        "best_true_odds": None,
        "days_better": opt_all.get("buckets_better") or 0,
        "days_worse": opt_all.get("buckets_worse") or 0,
        "ticks_better": 0,
        "ticks_worse": 0,
    }
