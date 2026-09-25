# AlgoE backtest on the raw parquet (offline, standalone)

Goes from the raw HKJC parquet folders to one HTML file you open by
double-clicking it. The first three questions are accuracy on every row
and finish with `3-run.bat`. The fourth is a later, sampled TG/SUP solve
(`4-optimize.bat`) and does not change the first three scores.

| # | question | how it is scored |
|---|---|---|
| 1 | next 5-min turnover = last 5-min turnover? | `forecast(t) = actual turnover(t-5min)` per selection, against three benchmarks including do-nothing |
| 2 | true probability = 1 / true odds? | `p_true = 1/true_odds`, renormalised per line, checked against what actually settled |
| 3 | does that belief price the book? | expected margin under the belief vs realised `turnover - dividend` |
| 4 | **what TG/SUP should we have opened?** | solve theta per 5-min bucket, price every line from it, compare with the odds HKJC actually opened |

Designed for a machine with **no internet**. Nothing is downloaded at any point:
the report is inline SVG, so there is no CDN, no npm, no fonts to fetch, and no
web server or API needs to be running.

## One engine, two ways to run it

This same package also backs the Backtest page of the desk app, so a fix lands in
both and the two agree on every number. The difference is what you get out:

| | this bundle | the desk page |
|---|---|---|
| output | `out\report.html` | the workbench, with filters and A-vs-B |
| panel files | **the same** `<data_dir>\..\algoe_panel_cache\` | **the same** |
| model choice | fixed at run time by flags | switched afterwards, no re-run |
| repeat runs | only missing days read raw parquet | same cache; Build just opens it |
| optimiser | `4-optimize.bat` / `--optimize` after the accuracy run | its own tab and button, same `opt\` files |

Reach for the bundle when you want a file you can email, and the page when you
want to ask a series of questions of the same window. They share one cache:
a day `3-run.bat` already wrote does not get rebuilt on the page.

## Requirements

Python 3.9+ with `pandas`, `numpy`, `pyarrow`. `scipy` is needed only for the
theta solve in section 4; without it that stage falls back to a coarser grid
search and says so in the report. `poisson_betting.py` and `inverse_poisson.py`
ship alongside, copied from `hkjc_code`.

## Running it

Copy the bundle over, then run it **from the bundle root**, the folder holding
`backtest\`. It starts as a module, not as a file: the engine uses relative
imports so that the web app and this bundle share one copy of it, and
`python backtest\run.py` would fail on the first import.

```bash
# 0. verify the code on synthetic data - touches no real data
#    (uses a temp folder; a copied out\facts next to the bat is ignored)
python -m backtest.run selftest

# 1. check the data BEFORE spending time on a full run (seconds)
python -m backtest.run probe --data-dir "S:\Users\Yeung\20260520 Algo E\Raw_Data"

# 2. smoke test on two days first
python -m backtest.run run --data-dir "S:\Users\Yeung\20260520 Algo E\Raw_Data" \
                  --out-dir ".\out-smoke" --max-days 2 --optimize \
                  --optimize-every 12 --optimize-max-buckets 300

# 3. accuracy on every row (turnover + calibrated prob). No solver.
python -m backtest.run run --data-dir "S:\Users\Yeung\20260520 Algo E\Raw_Data" \
                  --out-dir ".\out"

# 4. later: sample-solve TG/SUP on the same cache
python -m backtest.run run --data-dir "S:\Users\Yeung\20260520 Algo E\Raw_Data" \
                  --out-dir ".\out" --optimize
```

Then open `out\report.html`. In the packaged bundle those steps are
`0-selftest.bat`, `1-probe.bat`, `2-smoke.bat`, `3-run.bat` (accuracy) and
`4-optimize.bat` (sampled TG/SUP). Each takes the data folder as an optional
first argument.

`sample_report\report.html` is a real run against synthetic data, so you can see
the output format before your own run finishes, and spot a broken run by
comparison.

Useful flags: `--pools all`, `--prematch-window-min 720`, `--full-span`,
`--true-prob-source true_odds|poisson|demargin`, `--no-normalize-book`,
`--save-facts` (writes the per-day panel to parquet for your own digging),
`--start` / `--end`.

## Section 4: the optimiser stage (`--optimize`)

For each sampled (match, 5-min bucket) it solves

```
minimise  sum over live selections of  turnover x sell_odds(theta) x p_true
so that   total_goals >= |supremacy|   and   sell_odds >= 1.001
```

which is the objective from `optimizer_v2b.py`. Turnover does not depend on
theta, so minimising expected payout maximises expected gross margin.
`sell_odds = poisson_fair_odds(theta, line) / (1 + pool margin)`.

Two things are done differently from `optimizer_v2b.py`, both deliberate:

- **Goal and corner pools are solved separately.** A corner line's price does not
  depend on goal expectancy, so the 4-parameter problem is really two independent
  2-parameter problems. Faster, and the solver does not stall on a flat 4-D
  surface.
- **The pricer is a rewrite.** `poisson_betting.py` rebuilds the whole score
  matrix for every line, which dominates the cost inside a solver loop.
  `pricing.py` builds it once per theta and prices every line off it.
  `run selftest` asserts the two agree to 1e-9 across a grid of totals,
  supremacies and lines, quarter lines included, so this is a speed change and
  not a maths change.

### Read the pricer replication check first

Section 4 reports what happens when **HKJC's own TG/SUP** is fed through this
pricer: it should reproduce the odds they actually opened. If that error is
large, everything below it is measuring a pricing bug, not a parameter edge, and
the report says so in the verdict. The usual causes are the margin table in
`config.DEFAULT_MARGINS` and the handicap sign convention, so try
`--hdc-sign flip` and look at the per-pool replication table.

The sign convention is genuinely undocumented in the extraction notebooks:
`line_label` is one value shared by both sides of a line, and nothing says
whether it is the home handicap or the favourite's. The default reads it as the
home handicap. The replication check is how you settle the question empirically
rather than by guessing.

### Expected margin is decomposed, not quoted as one number

The report walks four books in order, so a lift cannot hide a pricing
difference:

1. HKJC's board as they opened it, valued at `p_true` — the reference
2. the same theta through **our** pricer — the gap here is pricing and margin
3. **optimised** theta — the gap here is the genuine parameter edge
4. realised `turnover - dividend` on settled money — a single draw, not an expectation

### Sampling is not optional

A full pass is one solve per match-bucket, which is millions over 2.5 months.
`--optimize-every N` keeps every Nth bucket of each match (default 6, so every
30 minutes) and `--optimize-max-buckets` caps the total. Everything section 4
reports is an estimate over that sample and the report states the sampling used.

`--optimize-demand` chooses the turnover the optimiser prices for. The default
`f_persist` is the honest one because it is the forecast a live system would
actually have. `turnover` uses the realised amount, which assumes a perfect
demand forecast and is therefore an upper bound rather than an achievable result.

## Read the probe output first

The probe prints a `PASS` / `WARN` / `FAIL` list. Two lines matter most:

- **`true_odds available`** — if this is a WARN, `HKJC_Odds` came from the
  historical extraction in `1 Data Extraction.ipynb`, which reads
  `pool_odds_change_c` and never joins `pool_odds_true_change_h`. The assumption
  you actually want to test then has no data behind it. Fix it by re-extracting
  odds with the `outer apply ... pool_odds_true_change_h` block from
  `5 Data Extraction - Real Time.ipynb`, which is where `true_odds` comes from.
  Failing that, `--true-prob-source poisson` prices from HKJC's own
  `Goal90CurrentTG` / `Goal90CurrentSUP` instead.
- **`dividend available`** — `dividend` is the actual payout, so realised gross
  margin is `turnover - dividend`, real money rather than a simulation. Without
  it, section 2 and 3 of the report are empty and only turnover forecasting is
  measured.

`FAIL` on any of odds / investments / pools / matches means the run cannot
proceed; the message names the folder it looked in.

## What gets built

One row per `(5-min bucket, pool_id, line_id, combination_id)`. Each row holds
what was known at the **start** of the bucket (last posted `odds` and
`true_odds`, `TG`/`SUP`, game state, match clock) and what happened **inside**
it (`turnover`, `dividend`). Joins follow the notebooks: investments key on
`pool_id` + `line_no` + `comb_id`, odds on `pool_id` + `line_id` +
`combination_id`, params on `EventLevel2ID`.

Outcomes come from money, not from re-deriving settlement from scores:
`y_frac = dividend / (turnover x odds_at_bet)`, clipped to `[0, 1]`. That gives
1.0 for a win, 0.0 for a loss, and about 0.5 for a quarter-line half-win or a
push refund, without needing separate rules per pool type.

By default a selection enters the panel 6 hours before kick-off
(`--prematch-window-min`). Prematch pools open days early and scoring all of
that is mostly zeros; the row count grows about linearly with this number. Any
bucket that actually took money is kept regardless, so no turnover is ever
excluded from the totals.

Loading is a month at a time, expansion a day at a time, and every statistic is
an additive sum, so memory stays flat no matter how long the window is.

## Reading the report

Three sections, one question each, with a verdict line at the top.

`WAPE` is total absolute error divided by total turnover, so "off by 30% of the
money". Judge the persistence forecast against the **`zero` row** in the
scoreboard, which is the do-nothing benchmark: forecasting zero always scores
WAPE 100%, and anything that cannot beat it is worthless. The `active` subset
(money now or in the previous bucket) is the honest one to quote; `all` includes
every dormant bucket and flatters any model that predicts nothing.

Two numbers are reported at both levels for a reason. Per-selection WAPE is what
the optimiser needs to allocate across lines; **market-wide WAPE**, which sums
across all live selections in a bucket first, is what matters for total exposure
and is much smaller because errors cancel.

### Sample size, before you over-read anything

Every 5-min bucket of one selection shares a single settled outcome. So millions
of scored rows can still carry only a few thousand independent outcomes, and
calibration error and the realised-vs-expected margin gap both move by roughly
`1/sqrt(settled selections)` on noise alone. The report prints that noise floor
next to both numbers, and the verdict thresholds scale with it. A margin gap
smaller than the floor is agreement, not signal.

Realised gross margin is also high variance by nature: a winning selection
returns roughly -100% of the turnover taken on it and a loser +100%, so the
average needs many settled selections to mean anything.

### Sanity check to look at every time

**True-odds book sum per line** should pile up on 1.00. Mass away from 1.00
means `true_odds` is stale relative to the bucket, or carries its own margin, and
the renormalisation is doing real work rather than nothing. Compare `p_true`
against `p_sell` (the public price, normalised the same way) in the belief
scoreboard: if `p_true` does not beat it, the true-odds feed adds nothing over
the price already on the board.

## Outputs

`report.html` is the deliverable. Alongside it: `report.json` (every number, for
feeding the desk UI later), and `turnover_models.csv`, `turnover_by_slice.csv`,
`belief_models.csv`, `calibration.csv`, `gm_by_slice.csv`, `daily.csv`,
`verdict.csv` for Excel. With `--optimize` you also get `optimizer_totals.csv`,
`optimizer_daily.csv`, `optimizer_by_clock.csv` and `pricer_replication.csv`.
`--save-facts` adds `facts\facts_YYYYMMDD.parquet`, one row per
(bucket, pool, line, combination) with every input and output column.

## Known limits

- Settlement is read from `dividend` on the investment rows, so a selection that
  took no bets has no outcome and is skipped by the belief and margin sections.
  It still counts in turnover forecasting.
- Section 3's expected GM uses **actual** bucket turnover with the belief price,
  so it answers "was the price right" rather than "what would we have opened".
  Section 4 is the one that opens a board.
- The optimiser has no inventory or risk limit, and no elasticity: demand is
  taken as given and does not react to the price it would have been offered. A
  shorter price in reality takes less money, so the section 4 lift is an upper
  bound on what the same theta would earn live.
- Section 4 scores full-time goal and corner pools. First-half pools are wired
  through `pricing.POOL_KIND` but are not in the default `--pools` list.
- Scores are taken from `HKJC_Parameters` (`GameState`, `TimeInSecond`) rather
  than rebuilt from `Match_Events` provider-by-provider. Events are loaded but
  only used as a fallback.
- Market odds are loaded but not scored. The extraction in
  `1 Data Extraction.ipynb` crosses the bookmaker and bet-type CTE aliases, so
  `bookmaker_str` can hold bet-type names; `loader.resolve_market_swap()` detects
  and repairs that, but the column is not used in any metric yet.
