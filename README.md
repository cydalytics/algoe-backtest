# AlgoE backtest — interactive page on the offline box

No parquet in this repo. The real parquet stays on the office machine.

Download on a machine that has GitHub. Copy `algoe-mvp.zip` onto USB (or a shared drive), then into the box that has no internet. That box never clones this repo.

`algoe-backtest.zip` is a separate script that writes a static file, `out\report.html`. You do not need it. The page you open in the browser is the web app in `algoe-mvp.zip`.

## 2026-10-27 zips (W12)

`algoe-mvp.zip` and `algoe-w10.zip` of 2026-10-27 supersede the 2026-10-12 ones. They change neither the features nor the models, so nothing is retrained. Unzip each over its old folder.

- The board and the cockpit read the W10 live engine. Opening a match re-reads it from SQL and solves it again on the board of that moment (newest TG / SUP, prices, score), then every 15 s while it is open. The book names the sides Home / Away. The old "next 5 minutes = last 5 minutes" is only the `Last 5m` column.
- `algoe-update-data.bat` keeps `Raw_Data` current with the queries of `1 Data Extraction.ipynb`. It starts from the day before the newest day on disk: monthly files are extracted whole again, Match_Investments day by day. A file replaces the old one only when it is complete, and the cached days it read again are rebuilt.
- `start.bat` starts that update in its own window when the data is older than yesterday. `algoe-schedule-update.bat` registers it every morning at 06:00 (no admin rights).
- `algoe-6-probe-live.bat --match <id>` checks the live money feed on a match in play. `algoe-3-backtest.bat --only replication --days FROM:TO` checks the first-half split, with no model.
- `algoe-models.html` explains the turnover and true-probability models. Open it in a browser; it needs no network.

First run on the office box:

```bat
cd /d D:\AlgoE
tar -xf algoe-mvp.zip
tar -xf algoe-w10.zip
cd algoe-w10
algoe-update-data.bat --dry-run
algoe-update-data.bat
algoe-schedule-update.bat
cd ..\algoe-mvp
start.bat -Source sql
```

`--dry-run` prints what it would extract and changes nothing. With the newest parquet on 1 October, the first run extracts September and October again and the investment days from 30 September, then rebuilds those days' panel and features. Its timings are in `Raw_Data\_algoe_update.json` and the window.

## Office box

```bat
cd /d D:\AlgoE
tar -xf algoe-mvp.zip
cd algoe-mvp
start.bat -Source sql
```

`-Source sql` is the live board: the home page reads HKJC SQL Server. The backtest page does not.

Open http://localhost:3000/backtest. The bar at the top switches between two sources.

**Algo E model (W10)**, the default, reads the reports `algoe-3-backtest.bat` writes under `AlgoE_Work\reports` (beside `Raw_Data`). It uses the newest copy of every section, so the full run and a later `algoe-3-backtest.bat --only events` run show as one report. Views:

- **Overview**: the champion against last 5 minutes, and what the run says, line by line.
- **Baseline vs model**: last 5 min (A), the champion (B) and the champion re-tuned at the event (C) on the same rows. It shows headline deltas, every test day, every slice (phase, clock, after an event, an event inside the 5 minutes, bet type, league), the split, the rows that took no money, a match replay with goals / red cards / corners marked, and how far off each match-bucket was.
- **Leaderboard**: every pipeline, any two against each other, and the feature groups.
- **Proportions**, **Events**, and **Drift, belief, margin**.

The folder follows `ALGOE_DATA_DIR` / `ALGOE_WORK_DIR`. If `Raw_Data` is not at `S:\Users\Yeung\20260520 Algo E\Raw_Data`, set `ALGOE_DATA_DIR` before `start.bat`, or type the `AlgoE_Work` folder under **Reports** on the page.

**Workbench (W06 parquet)** is the parquet bench as before. It reads the parquet folder you type. The page says **No backtest yet**. Click **Run**.

In the drawer, **Real data** is already selected. Set **Data folder** to the parquet on this machine (default `S:\Users\Yeung\20260520 Algo E\Raw_Data`). Set **From** / **To**. Click **Check data**, then **Build**.

Build reads the raw folders for those days, writes the cache beside them, and fills the page: turnover and belief cut by phase, bet type, line, clock, TG×SUP, and quote age. The header shows WAPE and ECE. Leave the window open until Building finishes.

The next time you open the same dates, the days are already cached. Click **Open cached**. It does not re-read the raw folders.

**Solve** is optional and slow. It updates the Optimize tab after each day and does not change WAPE or ECE.

The cache sits next to the parquet root:

`S:\Users\Yeung\20260520 Algo E\algoe_panel_cache`

If that cache is already there, Open cached does not re-read the raw folders.

## W10 turnover, calibration, optimizer

`algoe-w10.zip` is code only. No parquet, no trained models. Download it on a machine that has GitHub, copy it onto USB, and extract it on the box. The box never clones this repo.

```bat
cd /d D:\AlgoE
tar -xf algoe-w10.zip
cd algoe-w10
algoe-0-selftest.bat
algoe-1-check-data.bat
algoe-2-train.bat --start 2024-06-01 --end 2026-09-14 --workers 3 --set window.val_start=2025-07-01 --set window.test_start=2026-07-01
```

Drag the `Raw_Data` folder onto `algoe-1-check-data.bat` if it is not at `S:\Users\Yeung\20260520 Algo E\Raw_Data`.

Take `--end` from the `window end` line of the data check. `--workers N` builds N months at once; each needs one month of raw data in memory, so set N by RAM (`START-HERE.md`, section 2a).

A newer zip that only changes features or models keeps the panel cache already built: `algoe-2-train.bat` skips the cached panel days and rebuilds the features and models. Feature version 9 is still current: the same command keeps the panel, rebuilds the features (15+ hours with `--workers 2`), then trains both models (25 turnover pipelines) and writes a new backtest.

The 2026-10-12 zips (`algoe-w10.zip` and `algoe-mvp.zip`) supersede the 2026-10-11 one. The events pass now also writes the baseline-vs-model data the desk's Backtest tab draws, and scores accuracy on every row as the leaderboard does, so its totals equal the leaderboard's. If you ran `--only events` with the 2026-10-11 zip, run it again with this one.

These zips change neither the features nor the models, so nothing is rebuilt or retrained. It adds the proportion and event study to the backtest (sections 7, 9, 10 and 11: how well the split across bet types, pools, selections and sides is forecast; the WAPE split into rows with and without money; goals, red cards and corners before and after, by favourite, whose event, clock and score; buckets with an event inside them, with the forecast re-tuned at the event's minute; the fitted event shift). The live engine now keeps a match on 15-second refreshes after a goal, red card or corner, and re-tunes the rest of the bucket with the fitted shift. Unzip it over the old folder, then:

```bat
algoe-0-selftest.bat
algoe-3-backtest.bat --only events
```

The selftest should end `12 PASS`. The backtest reads 45 validation days and the 87 test days, writes a new `AlgoE_Work\reports\backtest_*\report.html` with sections 7, 9, 10 and 11, and saves `AlgoE_Work\models\turnover\EVENT_SHIFT.json` for the live engine. The earlier report keeps sections 1 to 8.

Model libraries for Python 3.11, 64-bit Windows are in `offline-wheels-cp311`. GitHub rejects a file over 100 MB, so torch, xgboost and catboost are split and the rest are in two zips. On the offline box, in the conda env that already imports sklearn:

```bat
cd /d D:\AlgoE\offline-wheels-cp311
install-offline-wheels.bat
```

It rejoins the wheels, checks every SHA256, and installs lightgbm, xgboost, catboost and the CPU build of torch without using the network. It then copies the Visual C++ DLLs from `vc_runtime` into `torch\lib` and over the older copies in the Anaconda prefix. Torch 2.14's `c10.dll` aborts with WinError 1114 when Python has already loaded Anaconda's older runtime, or Anaconda's `dbghelp.dll`. The old Anaconda files are kept in `vc-runtime-backup`. `cuda False` is the correct result only after `import torch` prints a version. scikit-learn is already on the box; its wheel is in the zips only as a fallback.

If lightgbm, xgboost and catboost are already installed and torch still fails at `torch\__init__.py` line 148, the box is still importing the old torch. Copy the updated `install-offline-wheels.ps1`, `fix-torch-runtime.bat` and `diagnose-torch-dlls.py` into the same folder. `fix-torch-runtime.bat` moves that old torch aside and installs `torch 2.14.1+cpu` from the wheel already extracted there. In the same conda env:

```bat
cd /d D:\AlgoE\offline-wheels-cp311
fix-torch-runtime.bat
```

If that copy is denied, run `vc_redist.x64.exe` in this folder as administrator, open a new prompt, and run `fix-torch-runtime.bat` again.

To rerun one part of the backtest, for example the optimizer: `algoe-3-backtest.bat --only optimizer` (any of `turnover,groups,trueprob,gm,replication,optimizer,drift,events,mix,elasticity`; `events` and `mix` run the same pass).

You want `12 PASS` from the selftest, no `FAIL` from the data check, then the newest `AlgoE_Work\reports\backtest_*\report.html` next to `Raw_Data`.

Before going live:

```bat
algoe-parity.bat --at "2026-09-10 21:30" --at "2026-09-11 00:05"
algoe-4-live.bat --source replay --at "2026-09-10 20:00" --speed 10
algoe-4-live.bat
```

`Identical` from parity means the live engine builds the same inputs the model was trained on. `algoe-4-live.bat` with no arguments reads the SQL feed. Ctrl+C stops it.

Models, reports, and logs are written beside the parquet, under `AlgoE_Work`. Replacing this folder with a newer zip does not delete them.
