# GitHub backup + offline box

Two different jobs. Do not mix them up.

| | this laptop (has internet) | the office box (no internet) |
|---|---|---|
| what lives here | the code | the parquet **and** the code |
| GitHub | yes — code only, private repo | no — it cannot clone or pull |
| how code moves | `git push`, then copy a zip to USB | unzip the USB zip |
| how data moves | never | already on `S:\` (or wherever you extracted it) |

There are two zips. Neither includes HKJC parquet, `algoe.db`, or a previous run.

| zip | what it is | on the office box |
|---|---|---|
| `dist\algoe-mvp.zip` | **the backtest platform** — FastAPI + the `/backtest` page | `start.bat`, then open http://localhost:3000/backtest |
| `dist\algoe-backtest.zip` | script only — one HTML report, no browser app | `0-selftest.bat` … `3-run.bat`, then `4-optimize.bat` |

Both write and read the **same** panel cache, next to the parquet root:

```
S:\Users\Yeung\20260520 Algo E\Raw_Data
S:\Users\Yeung\20260520 Algo E\algoe_panel_cache\<space>\panel\2026-07-01.parquet
```

`3-run.bat` filling that cache is enough for turnover and belief: open `/backtest`,
point at the same `Raw_Data`, and Open cached. It does not re-read the raw
folders. `4-optimize.bat` (or the page's Solve button) is a later sampled
step and does not change those two scores. The Optimize tab on the page
refreshes after each solved day. `report.html` still lands in
`algoe-backtest\out\` as a snapshot.

Carry **both** if you want the page. The script zip is enough if you only need `report.html`.

---

## A. This laptop — put the code on GitHub

Open PowerShell. The folder must be **this** one, not `AlgoE` at the top.
`AlgoE` has 34 GB of `Simulated_Data` and is not a git repo.

```powershell
cd "C:\Users\yeungwong\Documents\Projects\AlgoE\BiWeekly Meetings\W06_2026-09-15_backtest"

git init
git add .
git status
```

`git status` must **not** list `algoe.db`, `backtest_facts`, `parquet_cache`,
`frontend/node_modules`, or any `.parquet`. If it does, stop — the
`.gitignore` is not being picked up.

Then:

```powershell
git commit -m "W06 parquet backtest — code only, no market data."
gh repo create algoe-backtest --private --source=. --remote=origin --push
```

`--private` matters. The repo has connection-string defaults from the
notebooks even though it has no market data.

After the first push, later updates on this laptop are:

```powershell
cd "C:\Users\yeungwong\Documents\Projects\AlgoE\BiWeekly Meetings\W06_2026-09-15_backtest"
git add .
git commit -m "Describe why this change exists."
git push
```

That only updates GitHub. The office box still has the old zip until you
do section C.

---

## B. This laptop — make the USB zip

Still in the same folder:

```powershell
.\package-backtest.ps1 -Zip
.\package.ps1 -Zip
```

That writes:

```
dist\algoe-mvp\               the backtest platform (folder)
dist\algoe-mvp.zip            same thing, one file for USB
dist\algoe-backtest\          script-only engine (folder)
dist\algoe-backtest.zip       same thing, one file for USB
```

Copy the zip(s) you need onto the USB stick. Do not copy `Raw_Data`,
`algoe.db`, or `parquet_cache`.

If the office Python is missing `pandas` / `numpy` / `pyarrow` / `scipy`,
rebuild the zip **on this laptop** with wheels that match **that** Python:

```powershell
# on the office box, once, write down what it prints
python -c "import sys, sysconfig; print(sys.version); print(sysconfig.get_platform())"

# on this laptop — example for 64-bit Windows Python 3.11
.\package-backtest.ps1 -Wheels -PythonVersion 311 -Platform win_amd64 -Zip
```

`311` means 3.11, `312` means 3.12. A 3.12 wheel will not install on 3.11.

---

## C. Office box — first install

1. Plug in the USB. Copy `algoe-backtest.zip` somewhere local, e.g.
   `D:\AlgoE\algoe-backtest.zip`.
2. Right-click → Extract All, **or**:

   ```bat
   cd /d D:\AlgoE
   tar -xf algoe-backtest.zip
   ```

   You should now have `D:\AlgoE\algoe-backtest\` with `0-selftest.bat`
   and a `backtest\` folder inside it.
3. Confirm Python can see the libraries (no internet needed if they are
   already on that machine — the same interpreter as the extraction
   notebooks usually has them):

   ```bat
   python -c "import pandas, numpy, pyarrow; print('ok')"
   ```

   If that fails **and** the zip has a `vendor` folder:

   ```bat
   cd /d D:\AlgoE\algoe-backtest
   python -m pip install --no-index --find-links vendor -r requirements.txt
   ```
4. Prove the code, without touching real data. Do this **before** copying
   an old `out\facts` folder (selftest now ignores that folder, but the
   older zip did not — it would ingest real days and print `[selftest] FAIL`).

   ```bat
   cd /d D:\AlgoE\algoe-backtest
   0-selftest.bat
   ```

   You want `[selftest] PASS`. SciPy “values outside bounds” lines are
   warnings, not a fail.
5. Point it at the parquet that already lives on this box. Default in the
   bats is `S:\Users\Yeung\20260520 Algo E\Raw_Data`. If yours is
   somewhere else, drag that folder onto the bat, or pass it:

   ```bat
   1-probe.bat     "S:\Users\Yeung\20260520 Algo E\Raw_Data"
   2-smoke.bat     "S:\Users\Yeung\20260520 Algo E\Raw_Data"
   3-run.bat       "S:\Users\Yeung\20260520 Algo E\Raw_Data"
   4-optimize.bat  "S:\Users\Yeung\20260520 Algo E\Raw_Data"
   ```

6. Open `out-smoke\report.html` after the smoke, then `out\report.html`
   after `3-run`. That file already has turnover WAPE and money ECE.
   `4-optimize` is optional and slow; it adds the TG/SUP section.

`3-run.bat` is 2026-07-01 to 2026-09-15 **without** the optimiser. It writes
into `algoe-backtest\out\` and, with `--save-facts`, `algoe-backtest\out\facts\`.
Those folders are **outputs**, not the raw feed. The raw feed stays where
it is.

### The backtest platform (`algoe-mvp.zip`)

Needs Python **and** Node 18+ already on the office box. `node_modules` is
inside the zip; `npm` is never called.

```bat
cd /d D:\AlgoE
tar -xf algoe-mvp.zip
cd algoe-mvp
start.bat -Source sim
```

Board: http://localhost:3000  
Backtest page: http://localhost:3000/backtest  

On that page: `Run` → `Real data` → set the parquet folder to the path
that already exists here → Build. Days cache next to `algoe.db` inside
`algoe-mvp\`, not inside `Raw_Data`.

---

## D. Office box — update to a new version

Do this every time you change the code on the laptop and want the office
box to match.

**On the laptop**

```powershell
cd "C:\Users\yeungwong\Documents\Projects\AlgoE\BiWeekly Meetings\W06_2026-09-15_backtest"
git add .
git commit -m "Why this change."
git push
.\package-backtest.ps1 -Zip
.\package.ps1 -Zip
```

Copy the new zip(s) to USB. Same rename-old / unzip-new pattern for
`algoe-mvp` as for `algoe-backtest`. Keep `algoe-mvp-old\algoe.db`,
`algoe-mvp-old\backtest_facts` and `algoe-mvp-old\parquet_cache` if you
want the last run to reopen without rebuilding.

**On the office box**

Do **not** delete `Raw_Data`. Do **not** unzip on top of the old folder
if a run is still writing `out\`.

```bat
cd /d D:\AlgoE

REM keep the last working copy and its reports
ren algoe-backtest algoe-backtest-old

tar -xf algoe-backtest.zip
```

Then:

```bat
cd /d D:\AlgoE\algoe-backtest
0-selftest.bat

REM after PASS: keep the last HTML / csv / facts so 3-run can absorb those days
xcopy /E /I /Y algoe-backtest-old\out algoe-backtest\out

3-run.bat "S:\Users\Yeung\20260520 Algo E\Raw_Data"
REM later, if you want TG/SUP lift:
4-optimize.bat "S:\Users\Yeung\20260520 Algo E\Raw_Data"
```

When the new run looks right, delete `algoe-backtest-old`.

A new unzip does **not** re-extract parquet. It only replaces Python
files. If you only extended the date range and the data is already there,
just run `3-run.bat` again (or edit the `--end` date). The engine reads
whatever is in `--data-dir`.

---

## What not to do

- Do not `git init` in `C:\Users\yeungwong\Documents\Projects\AlgoE`. That
  tree has `Simulated_Data` (~35 GB) and is the wrong unit.
- Do not commit or USB-copy `algoe.db`, `backtest_facts`, `parquet_cache`,
  or anything under `Raw_Data`.
- Do not run `git pull` on the office box. There is no network. The USB
  zip is the update.
- Do not install with `pip install -r requirements.txt` on the office box
  unless you also brought `vendor\` from `-Wheels`. A bare `pip install`
  will try the internet and hang.
