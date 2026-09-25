# Algo E — W06 Backtest

> Biweekly W06 — 2026-09-15 — Deliverable: backtest framework for the three metric packs
> Gate M2: turnover accuracy · true-odds calibration · GM days better vs worse

The live desk from W04 is still here. This fortnight adds a backtest that runs on
the **real parquet** written by `1 Data Extraction.ipynb`, and scores each lever
on its own — turnover, calibrated true-prob, optimiser, policy / demand — with
the plots and cuts the meeting gate asked for.

## Two ways to run it

One engine, `backend/src/backtest/parquet`, reached two ways. Same code, same
numbers; they differ only in what you get out.

**On the desk, for asking a series of questions.** Open `/backtest` → `Run` →
`Real data`, point it at the parquet root, pick a window, build. Days are cached
individually, so extending the window later only builds what is new, and every
model already has its own column on every row — switching turnover or belief in
the workbench re-scores instantly instead of re-running. The TG/SUP solver is a
separate, opt-in button because it costs an SLSQP fit per sampled bucket.

```bash
pip install -r backend/requirements.txt
cd backend && python -m uvicorn src.api.main:app --port 8001
cd frontend && npm run dev        # -> http://127.0.0.1:3000/backtest
```

**As a script, for the air-gapped box.** `.\package-backtest.ps1 -Zip` builds
`dist\algoe-backtest`: copy it over, run a `.bat`, open `report.html`. No Node,
no API, no internet. See `START-HERE.md` inside the bundle.

The simulated bench is still there under `Run` → `Simulated`, for exercising the
screen when the data drive is not mounted:

```bash
cd backend
python scripts/backtest.py --quick
python -m src.backtest.parquet.run selftest   # engine check, synthetic parquet
python tests/test_backtest.py
```

## The flow (what main.py runs)

```
0 INITIALIZE -> 1 DATA EXTRACTION -> 2 PREPROCESSING -> 3 TURNOVER FORECAST -> 4 TRUE ODDS
 -> 5 TG/SUP OPTIMIZER (4-step loop) -> 6 SAVE -> SQLite -> 7 UI (FastAPI + single-page dashboard)
```

Every step prints a numbered box `[PASS] / [WARN] / [FAIL]`, writes the same
event to the log and to SQLite (`pipeline_runs`, `step_logs`, `theta_output`,
`forecast`), and is quality-gated.

## Layout (mirrors the Soccer Model repo conventions)

```
W02_2026-08-18_mvp/
├── main.py                          # thin entry point -> backend/scripts/main.py
├── algoe.db                         # SQLite output store (created on first run)
├── backend/
│   ├── requirements.txt
│   ├── scripts/
│   │   ├── main.py                  # the conductor - run this, read it top-to-bottom
│   │   ├── backtest.py              # model/optimizer A/B sweep: accuracy vs ACTUAL + GM lift per combo
│   │   └── run_mvp.bat              # double-click quick demo
│   ├── notebooks/                   # experimentation notebooks (later)
│   ├── src/
│   │   ├── core/
│   │   │   ├── config.py            # ALL-CAPS knobs in one place (paths, pools, overround, theta step, triggers, decay)
│   │   │   ├── exceptions.py        # BaseError + one exception class per stage
│   │   │   └── logging.py           # setup_logging + StepLogger (numbered step boxes)
│   │   ├── data/extractor.py        # step 1  DataSource | ParquetSource (dev) | SqlSource (prod, interface ready)
│   │   ├── features/preprocessor.py # step 2  FeaturePreprocessor (underscore = internal step methods)
│   │   ├── models/
│   │   │   ├── turnover.py          # step 3  ONE TurnoverModel, strategy from config.TURNOVER_MODEL (naive_lag5 now, time series/GNN later)
│   │   │   └── true_odds.py         # step 4  ONE TrueOddsModel, layer + distribution from config; theta decay hook (config)
│   │   ├── optimizer/optimizer.py   # step 5  GM4StepOptimizer: 4-step loop (anchor -> theta' -> E[GM])
│   │   ├── triggers/trigger.py      # TriggerEvaluator: run / skip / alert on odds + theta moves (roadmap slide 10)
│   │   ├── database/sqlite_store.py # step 6  SqliteStore (write + read)
│   │   └── api/
│   │       ├── main.py              # step 7  FastAPI app entry
│   │       ├── routes/runs.py       #          /api/runs + /api/runs/latest + /api/runs/{id}
│   │       └── static/index.html    #          thin dashboard (MVP UI; Next.js comes later)
│   └── tests/test_pipeline.py       # hermetic end-to-end smoke tests
└── frontend/                        # web UI - starts from W16 (see its README)
```

## Design choices worth keeping

| Choice | Why |
|---|---|
| **DataSource interface** (`data/extractor.py`) | dev = parquet, prod = SQL Server, same interface; swap without touching the pipeline |
| **TurnoverModel interface** (`models/turnover.py`) | naive baseline now, GNN (model.py) later - same `predict()` contract |
| **TrueProb fixed, SellOdds moves** | v1 belief vs offer separation (project.md 5/7) - the optimizer only re-prices the offer |
| **theta0 is always a candidate** | E[GM]* >= E[GM]0 by construction - the "never worse" sanity check is testable |
| **run_id + step_logs** | every run is replayable; the dashboard shows the latest run only |
| **quality gates per step** | 0 < true_prob < 1, lift >= 0, theta rows present - a failed step marks the run `failed` |
| **ALL-CAPS config in `core/config.py`** | one place to find every knob; env vars override, nothing hard-coded in modules |

## Known limitations (surfaced as warnings, never silent)

1. **Synthetic Match_Investments has no bet timestamp** -> the 5-min turnover
   feature falls back to day-level totals. Real data has `bet_timestamp`
   (preprocessing_spec.md 1) -> real buckets.
2. **No HT parameter layer in Simulated_Data** -> FHLO/HHAD price off FT theta (proxy).
3. **Quarter lines averaged** (e.g. 2.5/3 -> 2.75) - no quarter-ball splitting.
4. **Synthetic theta <-> match_id overlap is partial** (many investments rows lack a
   parameters snapshot and are dropped - flagged in step 2).
5. Naively, turnover is price-*insensitive*, so the optimizer moves theta to the
   bound of the search grid - numbers are pipeline tests, not business results.

## What comes next (W04 gate, 31 Aug)

- Real 5-min turnover buckets once bet timestamps land
- GNN swap-in behind `TurnoverModel` (already trained in `model.py`)
- theta search bounds from historical percentiles (project.md 22 #5)
- Thin dashboard polish; Next.js frontend from W16