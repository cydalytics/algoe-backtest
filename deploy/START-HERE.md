# Algo E — offline install

Everything here runs with no internet. Nothing calls out, nothing needs npm
or pip to reach a registry, and no font, map or CDN is fetched at page load.

You need two things already on the machine:

- **Python 3.9+** with `pandas`, `numpy`, `scipy` and `pyodbc` — the same
  interpreter that runs `5 Data Extraction - Real Time.ipynb` already has all
  four. Only `fastapi` and `uvicorn` are likely to be missing.
- **Node 18+** — used only to serve the built page. Nothing is compiled here
  and `node_modules` is already inside `web/`, so `npm` is never invoked.

---

## 1. Check the machine

```powershell
cd backend
python scripts\preflight.py --skip-tick
```

This tells you what is missing before anything else has a chance to fail in a
more confusing way. Read the `FAIL` lines; `WARN` lines are usually fine.

Near the top it prints a **wheel tag**, like `cp311-win_amd64`, and checks
the bundled `vendor` folder against it. Install what is missing:

```powershell
pip install --no-index --find-links ..\vendor -r requirements.txt
```

### If a wheel refuses to install

Compiled packages — `pydantic_core`, `numpy`, `scipy`, `pandas`, `pyarrow`,
`pyodbc` — are built per interpreter version *and* per architecture, and pip's
error names a filename rather than the reason. There is exactly one tag that
works on this machine, and preflight prints it. To read it directly:

```powershell
python -c "import sys, sysconfig; print(sys.version); print(sysconfig.get_platform())"
```

`3.11` plus `win-amd64` means every compiled wheel must say `cp311` and
`win_amd64`. Not `cp312`, not `win32` (that is 32-bit Python), not
`win_arm64` (that is Windows on ARM). Pure-Python wheels say `py3-none-any`
and work anywhere.

If the tag does not match what is in `vendor`, rebuild the bundle on the
connected machine for this target — no need to have that Python installed
there:

```powershell
.\package.ps1 -Wheels -PythonVersion 311 -Platform win_amd64 -Zip
```

`vendor\TARGET.txt` records what the folder was built for.

---

## 2. Prove it works without touching the database

```bat
start.bat -Source sim
```

Use the `.bat`, not the `.ps1`. From cmd, Explorer or Git Bash a `.ps1` is
just a file, and Windows asks how you want to open it. The `.bat` hands it
to PowerShell. Double-clicking `start.bat` is the same as `start.bat` with
no arguments (simulator, unless you set `ALGOE_SOURCE`).

This runs the whole pipeline on the built-in simulator: no SQL, no network.
The board opens at <http://localhost:3000>. Twelve fake matches, a full book,
a working optimizer. The backtest page is at <http://localhost:3000/backtest>
— `Run` → `Real data`, point it at the parquet root that already lives on
this machine (nothing is downloaded).

**If this works, the software is fine** and anything that goes wrong next is
about database access, not about the code.

---

## 3. Point it at the real database

The three connection strings come straight from the notebook and are the
defaults, so normally there is nothing to set:

| Variable            | Default                                                          |
| ------------------- | ---------------------------------------------------------------- |
| `ALGOE_CONN_QFM`    | `DRIVER={SQL Server};SERVER=QFMWDB1ST12,40000;DATABASE=qfm_outbound_db` |
| `ALGOE_CONN_BIS`    | `DRIVER={SQL Server};SERVER=QFMBDB2LSR3A,40000;DATABASE=bis1_fbuser_db` |
| `ALGOE_CONN_BIS2`   | `DRIVER={SQL Server};SERVER=QFMBDB2LSR3A,40000;DATABASE=QFM`      |

Check they open, and that the queries return rows:

```powershell
cd backend
python scripts\preflight.py --source sql
```

Then run for real:

```bat
start.bat -Source sql
```

If a connection string differs on this machine, set it before starting:

```powershell
$env:ALGOE_CONN_QFM = "DRIVER={ODBC Driver 17 for SQL Server};SERVER=...;DATABASE=..."
```

---

## What you are looking at

**The board** (`/`) is one row per live match, sorted by how much expected
margin the optimizer thinks is being left on the table. The `Uplift` column
is that number: expected GM at the recommended parameters minus expected GM
at the current ones, over the next five minutes of turnover.

The board appears within a couple of seconds of a tick, fully priced, and
the recommendations fill in behind it. The solver works down the card
richest first, so the matches worth the most money are answered first; a
row still in the queue says `queued to solve` and the strip at the top
counts the progress. Opening a match jumps it to the front of that queue.

Only matches a trader can still act on are listed. A match drops off the
board when the feed publishes a full-time result, when it is voided, or when
nothing on it has been priced for thirty minutes. The pool query the
notebook uses asks only whether a pool has *started* selling, with no
stop-sell condition, so without this the card fills up with second halves in
the ninety-third minute. The tick's notes say how many were dropped and why.
Set `ALGOE_SHOW_FINISHED=1` to see them anyway.

**The cockpit** (click any row) is one match in full:

- **left** — the eight parameters. Current value, recommended value, and a
  slider. Drag one and the whole book re-prices against it.
- **middle** — every selection, grouped the way the pools are grouped. Our
  price beside HKJC's, beside the external market's.
- **right** — why. The solver's own account of itself, the response curves,
  which pools the uplift comes from, the external quotes, the money flow, and
  the match tape.

Every number on both screens comes from one backend tick. The frontend does
no pricing of its own, so what the screen shows and what the optimizer
decided cannot drift apart.

---

## The two MVP simplifications

Both are labelled in the UI rather than hidden, because they are the two
things a trader must know before trusting a number here:

1. **True probability = 1 / HKJC true odds.** Taken from the feed, not
   modelled. Where the feed has no true odds, the fallback used is shown per
   selection.
2. **Next five minutes of turnover = the last five minutes.** A flat carry
   forward, no trend, no seasonality.

Everything downstream — expected margin, the recommendation, the uplift — is
only as good as those two.

---

## Running the pieces separately

```powershell
# API only
cd backend
python -m uvicorn src.api.main:app --port 8001

# board only, against an API already running
cd web
$env:ALGOE_API_URL = "http://127.0.0.1:8001"
node server.js
```

One tick to the console, no servers at all:

```powershell
cd backend
python scripts\main.py --source sql
```

Record a live snapshot, then replay it later without the database — useful
for reproducing something odd that happened at 20:15 last night:

```powershell
python scripts\capture.py                 # writes captures\<timestamp>\
start.bat -Source capture
```

---

## When something looks wrong

**The board says it cannot reach the API.** The web server proxies `/api` to
`ALGOE_API_URL`. If you started the API on a different port, `start.ps1`
handles it; if you started them by hand, set that variable.

**The board is empty but the API is up.** No live matches, or the queries
returned nothing. `python scripts\preflight.py --source sql` says which.

**`pyodbc` imports but no connection opens.** Almost always a missing ODBC
driver rather than credentials. Preflight lists the registered drivers; if
`SQL Server` is not among them, the connection string's `DRIVER={...}` needs
to name one that is.

**The numbers look wrong.** Check the source chip in the top-right first. If
it says `sim` you are looking at generated data, not the real book.

**A match is flagged `incoherent from the feed`.** `algo_param` sent a
supremacy larger than the total it is a difference of, or a first half
larger than the whole match. No score grid can be built from that, so the
values are clamped before pricing — the Solve tab shows exactly what changed.
Worth chasing upstream: the recommendation is sound but the input was not.

**A match says `no solve`.** The solver ran and could not converge. That is
different from `queued to solve`, which just means it has not got there yet.

**Everything is slow on a very large card.** The solve is capped at
`ALGOE_OPT_BUDGET` seconds per tick (240 by default); matches not reached
keep their prices and simply get no recommendation that tick. Raise it, or
lower `ALGOE_OPT_MAXITER`, if the trade-off is wrong for this desk.

**Time the solver on the real card**, without the UI in the way:

```powershell
cd backend
python tests\smoke_solve.py sql
```

One line per match — turnover, GM now, GM at the recommendation, and how
many milliseconds it took — plus any complaint scipy made. This is the
place to look if a recommendation seems off, because it shows the solve on
its own rather than through three layers of plumbing.

**Check the whole thing still hangs together:**

```powershell
cd backend
python tests\test_optimizer.py    # the solver, against known-bad inputs
python tests\test_tradable.py     # which matches reach the board
python tests\contract.py          # every field the UI reads, on every endpoint
```

All three run on generated data and need no database.
