# AlgoE backtest — interactive page on the offline box

No parquet in this repo. The real parquet stays on the office machine.

Download on a machine that has GitHub. Copy `algoe-mvp.zip` onto USB (or a shared drive), then into the box that has no internet. That box never clones this repo.

`algoe-backtest.zip` is a separate script that writes a static file, `out\report.html`. You do not need it. The page you open in the browser is the web app in `algoe-mvp.zip`.

## Office box

```bat
cd /d D:\AlgoE
tar -xf algoe-mvp.zip
cd algoe-mvp
start.bat -Source sql
```

`-Source sql` is the live board: the home page reads HKJC SQL Server. The backtest page does not. It reads the parquet folder you type.

Open http://localhost:3000/backtest. The page says **No backtest yet**. Click **Run**.

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

A newer zip that only changes features or models keeps the panel cache already built: `algoe-2-train.bat` skips the cached panel days and rebuilds the features and models. The 2026-10-06 zip is feature version 8: the same command keeps the panel, rebuilds the features (about 15 hours with `--workers 2`), then trains both models and writes a new backtest.

Model libraries (`START-HERE.md`, section 0): `lightgbm`, `scikit-learn` and `torch` (CPU) let every pipeline compete; without one, the pipelines it runs are skipped and logged. `python scripts\preflight.py --skip-tick` from `backend` lists what the box has and the wheel tag to download for it.

To rerun one part of the backtest, for example the optimizer: `algoe-3-backtest.bat --only optimizer` (any of `turnover,groups,trueprob,gm,replication,optimizer`).

You want `11 PASS` from the selftest, no `FAIL` from the data check, then the newest `AlgoE_Work\reports\backtest_*\report.html` next to `Raw_Data`.

Before going live:

```bat
algoe-parity.bat --at "2026-09-10 21:30" --at "2026-09-11 00:05"
algoe-4-live.bat --source replay --at "2026-09-10 20:00" --speed 10
algoe-4-live.bat
```

`Identical` from parity means the live engine builds the same inputs the model was trained on. `algoe-4-live.bat` with no arguments reads the SQL feed. Ctrl+C stops it.

Models, reports, and logs are written beside the parquet, under `AlgoE_Work`. Replacing this folder with a newer zip does not delete them.
