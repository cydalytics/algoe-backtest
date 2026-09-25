# W08 · 2026-09-29 · Turnover

**Meeting:** Tuesday 29 Sep 2026  
**Block:** Phase 3a — Turnover prediction (first half)  
**Window:** 14–28 Sep  
**Delivery:** 12 Oct — model beats naive “5 mins before”  
**Prev:** [W06 Backtest](../W06_2026-09-15_backtest/work-log.md) · **Next:** [W10 Turnover](../W10_2026-10-13_turnover/work-log.md)

## This fortnight (roadmap)

Add to the existing pipeline — do not rebuild it.

| Addition | Why |
|----------|-----|
| Event features | goals, corners, cards — demand shocks |
| All-up banker — pre-match | SGA multi-leg adds volume |
| All-up banker — in-play | ignore (no all-up in-play) |
| Time-series memory | recent turnover trend |
| Day / match / concurrent | weekday, popularity, split attention |
| Best line shift | use line value (actual − expected), not line identity |

Levels: day · match · pool · selection.

## Work log

| Date | What | Status |
|------|------|--------|
| 2026-09-16 | | |
| 2026-09-17 | | |
| 2026-09-18 | | |
| 2026-09-21 | | |
| 2026-09-22 | | |
| 2026-09-23 | | |
| 2026-09-24 | | |
| 2026-09-25 | CPU turnover fit (`forecast.py`): hit probability × size, blended toward persistence. Features on every offered selection-bucket (no label leak), best line, shares, walk-forward history. Fit / inner / hold-out days split. No GPU, no data export. | packaged via `package-turnover.ps1` |
| 2026-09-25 | Walk-forward `hurdle` turnover column in the workbench (day N fitted on days before N, 14-day warm-up, weekly refit). A vs B: day-level paired test (Diebold-Mariano, Newey-West lag 1), daily WAPE, level reliability, WAPE/bias by cut, `Last 5m` cut. | in `algoe-mvp.zip` |
| 2026-09-28 | | |
| 2026-09-29 | Biweekly #5 | |

## Target shape

Dynamic (new bet types / leagues) · self-learning (investment pattern drift) · self-adapting (in-play and across days).
