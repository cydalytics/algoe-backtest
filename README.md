# AlgoE backtest — copy these two zips to the offline box

No parquet in this repo. Data stays on the office machine.

| file | what it is |
|---|---|
| `algoe-backtest.zip` | script: `0-selftest` … `3-run.bat` → `out\report.html` |
| `algoe-mvp.zip` | web platform: `start.bat` → http://localhost:3000/backtest |

Both read and write the same cache next to the parquet root:

`S:\Users\Yeung\20260520 Algo E\algoe_panel_cache`

## Office box

```bat
cd /d D:\AlgoE
tar -xf algoe-backtest.zip
tar -xf algoe-mvp.zip
```

**Script** (no path argument — default is already `Raw_Data`):

```bat
cd /d D:\AlgoE\algoe-backtest
0-selftest.bat
1-probe.bat
3-run.bat
```

`0-selftest` uses synthetic data in a temp folder. It does **not** read `out\facts`. SciPy “values outside bounds” lines are warnings; you want `[selftest] PASS`.

Then open `out\report.html`.

**Web** (does not wait for `3-run` if the cache is already filled):

```bat
cd /d D:\AlgoE\algoe-mvp
start.bat -Source sim
```

Open http://localhost:3000/backtest → Real data → same `Raw_Data` folder → **Open cached**.

`-Source sim` is only the live board. Backtest uses the parquet path you type.

If you already ran an older `3-run`, copy `algoe-backtest-old\out\facts` into the new `algoe-backtest\out\facts` **after** `0-selftest` and **before** `3-run`, so those days are absorbed and not rebuilt. Do not copy facts just to run selftest.
