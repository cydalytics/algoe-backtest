"""Entry point.

    python run.py probe                     # check the data before running
    python run.py run                       # the backtest
    python run.py selftest                  # verify the code on synthetic data

Defaults target 2026-07-01 .. 2026-09-15 with the two baseline assumptions:
true probability = 1 / true odds, and next 5-min turnover = last 5-min turnover.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

from .cache import (
    PanelCache, day_list, facts_dir_guesses, ingest_flat_facts,
    resolve_cache_root,
)
from .config import Config, DEFAULT_POOLS
from .evaluate import Accumulator
from .loader import MissingData
from .probe import run_probe
from .report_html import build_html


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["probe", "run", "selftest"])
    d = Config()
    p.add_argument("--data-dir", default=str(d.data_dir),
                   help="folder holding HKJC_Odds/, Match_Investments/, ...")
    p.add_argument("--out-dir", default=str(d.out_dir),
                   help="where to write report.html (the panel cache is shared, see --cache-dir)")
    p.add_argument("--cache-dir", default="",
                   help="shared panel cache (default: <data_dir>/../algoe_panel_cache). "
                        "The web /backtest page reads this same folder.")
    p.add_argument("--start", default=d.start_date)
    p.add_argument("--end", default=d.end_date, help="inclusive")
    p.add_argument("--bucket-minutes", type=int, default=d.bucket_minutes)
    p.add_argument("--pools", default=",".join(DEFAULT_POOLS),
                   help="comma separated, or 'all'")
    p.add_argument("--prematch-window-min", type=int, default=d.prematch_window_min,
                   help="how far before kick-off a selection enters the panel")
    p.add_argument("--full-span", action="store_true",
                   help="score the entire selling window (slow, many zero buckets)")
    p.add_argument("--lookback-days", type=int, default=d.lookback_days)
    p.add_argument("--true-prob-source", default=d.true_prob_source,
                   choices=["auto", "true_odds", "poisson", "demargin"])
    p.add_argument("--no-normalize-book", action="store_true",
                   help="do not renormalise 1/true_odds to sum to 1 per line")
    p.add_argument("--save-facts", action="store_true",
                   help="also write the per-day panel to parquet")
    p.add_argument("--sample-rows", type=int, default=d.sample_rows)
    p.add_argument("--max-days", type=int, default=0,
                   help="stop after N days, for a quick smoke test")

    g = p.add_argument_group("optimizer stage")
    g.add_argument("--optimize", action="store_true",
                   help="optional later step: sample-solve TG/SUP per 5-min "
                        "bucket. Omit this for turnover + belief on every row.")
    g.add_argument("--optimize-every", type=int, default=d.optimize_every,
                   help="keep every Nth bucket per match (1 = every bucket)")
    g.add_argument("--optimize-max-buckets", type=int, default=d.optimize_max_buckets)
    g.add_argument("--optimize-starts", type=int, default=d.optimize_starts)
    g.add_argument("--optimize-demand", default=d.optimize_demand,
                   choices=["f_persist", "f_ema", "f_ma3", "turnover"],
                   help="which turnover the optimiser prices for; 'turnover' "
                        "assumes a perfect demand forecast")
    g.add_argument("--hdc-sign", default=d.hdc_sign, choices=["home", "flip"],
                   help="how to read the HDC/CHDC line_label sign")
    return p


def cfg_from_args(a) -> Config:
    pools = [] if a.pools.strip().lower() == "all" else \
        [s.strip() for s in a.pools.split(",") if s.strip()]
    return Config(
        data_dir=Path(a.data_dir),
        out_dir=Path(a.out_dir),
        cache_dir=Path(a.cache_dir) if getattr(a, "cache_dir", None) else None,
        start_date=a.start,
        end_date=a.end,
        bucket_minutes=a.bucket_minutes,
        pools=pools,
        prematch_window_min=a.prematch_window_min,
        full_span=a.full_span,
        lookback_days=a.lookback_days,
        true_prob_source=a.true_prob_source,
        normalize_book=not a.no_normalize_book,
        save_facts=a.save_facts,
        sample_rows=a.sample_rows,
        optimize=a.optimize,
        optimize_every=a.optimize_every,
        optimize_max_buckets=a.optimize_max_buckets,
        optimize_starts=a.optimize_starts,
        optimize_demand=a.optimize_demand,
        hdc_sign=a.hdc_sign,
    )


def run_backtest(cfg: Config, max_days: int = 0) -> dict:
    """Build missing days into the shared panel cache, then write the HTML report.

    The cache is the same folder the /backtest page reads. A day already there
    is not read from raw parquet again.
    """
    t0 = time.time()
    out = Path(cfg.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    root = resolve_cache_root(cfg)
    cache = PanelCache(cfg, root)
    ingested = 0
    for folder in facts_dir_guesses(cfg):
        ingested += ingest_flat_facts(cache, folder)
    if ingested:
        print(f"[cache] absorbed {ingested} day(s) from an older out\\facts folder",
              flush=True)

    print(f"[cache] {cache.root}", flush=True)
    (out / "cache_path.txt").write_text(str(cache.root) + "\n", encoding="utf-8")

    days = day_list(cfg.start_date, cfg.end_date)
    if max_days:
        days = days[:max_days]
    if not days:
        raise MissingData("empty date window")

    missing = [d for d in days if not cache.has_day(d)]
    have = len(days) - len(missing)
    print(f"[cache] {have} day(s) already built, {len(missing)} to read from parquet",
          flush=True)

    def on_progress(info: dict) -> None:
        if info.get("stage") == "static":
            print(f"[static] {info.get('message', '')}", flush=True)
        elif info.get("stage") == "load":
            print(f"[load] {info.get('message', '')}", flush=True)
        elif info.get("stage") == "day":
            opt_n = info.get("opt_rows")
            note = f" opt_buckets={opt_n:,}" if opt_n is not None else ""
            print(f"[day] {info['day']} rows={info['rows']:,} "
                  f"turnover={info.get('turnover', 0):,.0f}{note}", flush=True)
        elif info.get("stage") == "optimize":
            print(f"[opt] {info.get('day')} buckets={info.get('opt_rows', 0):,}",
                  flush=True)

    if missing:
        cache.build(missing, progress=on_progress, optimize=cfg.optimize)

    if cfg.optimize:
        todo_opt = [d for d in days if cache.has_day(d) and not cache.has_opt(d)]
        if todo_opt:
            print(f"[opt] solving {len(todo_opt)} cached day(s) not yet solved",
                  flush=True)
            cache.build_optimizer(todo_opt, progress=on_progress)

    acc = Accumulator(sample_rows=cfg.sample_rows)
    n_days = 0
    for day in days:
        facts = cache.load_day(day)
        rows = 0 if facts is None or facts.empty else len(facts)
        money = 0.0 if not rows else float(facts["turnover"].sum())
        acc.update(day, facts)
        opt_n = (cache.days.get("{:%Y-%m-%d}".format(day)) or {}).get("opt_rows")
        if missing and day in {pd.Timestamp(d).normalize() for d in missing}:
            pass  # already printed during build
        else:
            note = f" opt_buckets={int(opt_n):,}" if opt_n is not None else " (cached)"
            print(f"[day] {day:%Y-%m-%d} rows={rows:,} turnover={money:,.0f}{note}",
                  flush=True)
        if cfg.save_facts and rows:
            facts_dir = out / "facts"
            facts_dir.mkdir(exist_ok=True)
            facts.to_parquet(facts_dir / f"facts_{day:%Y%m%d}.parquet", index=False)
        n_days += 1

    rep = acc.finalize()
    if cfg.optimize:
        from .optimize import report_from_frames
        opt_rows, opt_prices = cache.load_opt_range(days[0], days[-1])
        opt_rep = report_from_frames(cfg, opt_rows, opt_prices) if not opt_rows.empty else None
        if opt_rep:
            rep["optimizer"] = opt_rep
            rep["verdict"] = list(rep.get("verdict", [])) + opt_rep["verdict"]
    rep["config"] = cfg.to_dict()
    rep["config"]["cache_dir"] = str(cache.root)
    rep["runtime_seconds"] = round(time.time() - t0, 1)
    write_outputs(cfg, rep)
    print(f"  shared cache -> {cache.root}", flush=True)
    return rep


def write_outputs(cfg: Config, rep: dict) -> None:
    out = Path(cfg.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    if cfg.write_json:
        (out / "report.json").write_text(json.dumps(rep, indent=2, default=str),
                                         encoding="utf-8")
    # flat CSVs for anyone who would rather look at this in Excel
    rows = [{"subset_model": k, **v} for k, v in rep["turnover"]["models"].items()]
    pd.DataFrame(rows).to_csv(out / "turnover_models.csv", index=False)
    pd.DataFrame(rep["turnover"]["by_slice"]).to_csv(out / "turnover_by_slice.csv", index=False)
    pd.DataFrame([{"belief": k, **v} for k, v in rep["belief"]["models"].items()]) \
        .to_csv(out / "belief_models.csv", index=False)
    cal_rows = [{"belief": m, **b} for m, pack in rep["belief"]["calibration"].items()
                for b in pack["bins"]]
    pd.DataFrame(cal_rows).to_csv(out / "calibration.csv", index=False)
    pd.DataFrame(rep["gm"]["by_slice"]).to_csv(out / "gm_by_slice.csv", index=False)
    pd.DataFrame(rep["daily"]).to_csv(out / "daily.csv", index=False)
    pd.DataFrame(rep.get("verdict", [])).to_csv(out / "verdict.csv", index=False)
    o = rep.get("optimizer")
    if o:
        pd.DataFrame([{"scope": k, **v} for k, v in o["totals"].items()]) \
            .to_csv(out / "optimizer_totals.csv", index=False)
        pd.DataFrame(o["daily"]).to_csv(out / "optimizer_daily.csv", index=False)
        pd.DataFrame(o["by_slice"]).to_csv(out / "optimizer_by_clock.csv", index=False)
        rep_pool = (o.get("prices") or {}).get("replication", {}).get("by_pool")
        if rep_pool:
            pd.DataFrame(rep_pool).to_csv(out / "pricer_replication.csv", index=False)

    if cfg.write_html:
        html = build_html(rep, rep["config"])
        (out / "report.html").write_text(html, encoding="utf-8")

    print("\n" + "=" * 74)
    for v in rep.get("verdict", []):
        print(f"  [{v['verdict']}] {v['topic']}\n      {v['detail']}")
    print("=" * 74)
    cov = rep["coverage"]
    print(f"  days={cov['days']} rows={cov['rows']:,} matches={cov['matches']:,} "
          f"turnover={cov['turnover']:,.0f} settled_rows={cov['settled_rows']:,}")
    print(f"  runtime {rep.get('runtime_seconds')}s")
    print(f"\n  open this -> {(out / 'report.html').resolve()}")


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cfg = cfg_from_args(args)

    if args.command == "probe":
        rep = run_probe(cfg)
        fails = [c for c in rep["checks"] if c["level"] == "FAIL"]
        return 1 if fails else 0

    if args.command == "selftest":
        from .selftest import run_selftest
        return run_selftest()

    try:
        run_backtest(cfg, max_days=args.max_days)
    except MissingData as exc:
        print(f"\nERROR: {exc}\n\nRun `python run.py probe --data-dir ...` to see what "
              f"is in the folders.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
