"""Pre-flight: report what is actually in the parquet folders.

Run this FIRST on the offline machine. It reads only parquet metadata plus a
small sample, so it finishes in seconds and tells you whether the real backtest
can run and what it will be able to measure.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from .config import SUBDIRS, TIME_COL, Config
from .loader import _file_time_bounds, _resolve, _schema_names, files_for, folder_for


def probe(cfg: Config) -> dict:
    start = pd.Timestamp(cfg.start_date)
    end = pd.Timestamp(cfg.end_date) + pd.Timedelta(days=1)
    report = {"data_dir": str(cfg.data_dir), "window": [str(start), str(end)], "tables": {}}

    for name in SUBDIRS:
        paths = files_for(cfg.data_dir, name)
        info = {
            "folder": str(folder_for(cfg.data_dir, name)),
            "exists": folder_for(cfg.data_dir, name).exists(),
            "n_files": len(paths),
            "rows_total": 0,
            "rows_in_window": None,
            "columns_raw": [],
            "canonical": {},
            "missing_canonical": [],
            "min_time": None,
            "max_time": None,
            "files_in_window": 0,
            "sample": [],
        }
        if paths:
            rename = _resolve(name, _schema_names(paths[0]))
            info["columns_raw"] = _schema_names(paths[0])
            info["canonical"] = rename
            info["missing_canonical"] = sorted(
                set(_all_canonical(name)) - set(rename.values())
            )
            tcol = TIME_COL[name]
            raw_time = next((r for r, c in rename.items() if c == tcol), None)
            lo_all, hi_all = None, None
            for path in paths:
                try:
                    md = pq.ParquetFile(path).metadata
                    info["rows_total"] += md.num_rows
                except Exception:
                    continue
                if raw_time:
                    b = _file_time_bounds(path, raw_time)
                    if b:
                        lo, hi = b
                        lo_all = lo if lo_all is None else min(lo_all, lo)
                        hi_all = hi if hi_all is None else max(hi_all, hi)
                        if hi >= start and lo < end:
                            info["files_in_window"] += 1
            info["min_time"] = str(lo_all) if lo_all is not None else None
            info["max_time"] = str(hi_all) if hi_all is not None else None
            try:
                sample = pd.read_parquet(paths[-1]).head(3)
                info["sample"] = json.loads(sample.to_json(orient="records", date_format="iso"))
            except Exception:
                pass
        report["tables"][name] = info

    report["checks"] = _checks(report)
    return report


def _all_canonical(name: str):
    from .loader import ALIASES
    return list(ALIASES[name].keys())


def _checks(report: dict) -> list:
    out = []

    def add(level, what, detail):
        out.append({"level": level, "check": what, "detail": detail})

    t = report["tables"]
    win_start = pd.Timestamp(report["window"][0])
    win_end = pd.Timestamp(report["window"][1])

    for name in ("odds", "investments", "pools", "matches"):
        info = t[name]
        if not info["exists"] or info["n_files"] == 0:
            add("FAIL", f"{name} present", f"no parquet under {info['folder']}")
        elif info["files_in_window"] == 0 and info["max_time"]:
            add("FAIL", f"{name} covers window",
                f"data ends {info['max_time']}, need up to {win_end}")
        else:
            add("PASS", f"{name} present", f"{info['n_files']} files, {info['rows_total']:,} rows")

    odds = t["odds"]
    if "true_odds" in (odds.get("canonical") or {}).values():
        add("PASS", "true_odds available",
            "p_true = 1/true_odds can be computed directly (this is the assumption you asked for)")
    else:
        add("WARN", "true_odds available",
            "HKJC_Odds has no true_odds column. This folder was written by the historical "
            "extraction (pool_odds_change_c) which does not join pool_odds_true_change_h. "
            "The backtest will fall back to Poisson fair prob from HKJC_Parameters TG/SUP, "
            "then to de-marginalised public odds. Re-extract with the query from "
            "'5 Data Extraction - Real Time.ipynb' to get true_odds.")

    inv = t["investments"]
    for col, why in (("dividend", "realised GM = turnover - dividend"),
                     ("es_dividend", "estimated GM before settlement")):
        if col in (inv.get("canonical") or {}).values():
            add("PASS", f"{col} available", why)
        else:
            add("WARN", f"{col} available", f"cannot compute {why}")

    par = t["params"]
    for col in ("tg", "sup", "game_state", "time_in_second"):
        if col not in (par.get("canonical") or {}).values():
            add("WARN", f"params.{col}", "missing; match clock / Poisson fallback degraded")

    for name in ("odds", "investments"):
        info = t[name]
        if info["min_time"] and pd.Timestamp(info["min_time"]) > win_start:
            add("WARN", f"{name} start coverage",
                f"earliest row {info['min_time']} is after requested start {win_start}")
    return out


def render_text(report: dict) -> str:
    L = []
    L.append(f"data_dir : {report['data_dir']}")
    L.append(f"window   : {report['window'][0]} .. {report['window'][1]}")
    L.append("")
    L.append(f"{'table':<13}{'files':>7}{'in-win':>8}{'rows':>15}  time coverage")
    L.append("-" * 78)
    for name, info in report["tables"].items():
        cov = f"{info['min_time']} .. {info['max_time']}" if info["min_time"] else "(no time stats)"
        L.append(f"{name:<13}{info['n_files']:>7}{info['files_in_window']:>8}"
                 f"{info['rows_total']:>15,}  {cov}")
    L.append("")
    L.append("checks")
    L.append("-" * 78)
    order = {"FAIL": 0, "WARN": 1, "PASS": 2}
    for c in sorted(report["checks"], key=lambda x: order.get(x["level"], 3)):
        L.append(f"[{c['level']:<4}] {c['check']}")
        L.append(f"        {c['detail']}")
    L.append("")
    L.append("columns per table (raw -> canonical)")
    L.append("-" * 78)
    for name, info in report["tables"].items():
        if not info["canonical"]:
            continue
        pairs = ", ".join(f"{r}->{c}" for r, c in info["canonical"].items())
        L.append(f"{name}: {pairs}")
        if info["missing_canonical"]:
            L.append(f"   not found: {info['missing_canonical']}")
    return "\n".join(L)


def run_probe(cfg: Config) -> dict:
    rep = probe(cfg)
    out = Path(cfg.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "probe.json").write_text(json.dumps(rep, indent=2), encoding="utf-8")
    text = render_text(rep)
    (out / "probe.txt").write_text(text, encoding="utf-8")
    print(text)
    print(f"\nwritten: {out / 'probe.txt'}  and  probe.json")
    return rep
