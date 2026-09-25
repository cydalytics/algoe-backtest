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
