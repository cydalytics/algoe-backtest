"""
Backtest unit + smoke checks.

Run from the backend folder, no pytest required:

    python tests/test_backtest.py

Change Log:
-----------
2026-09-11      Initialize (W06 backtest)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.backtest.labels import attach_labels
from src.backtest.metrics import gm_pack, true_odds_pack, turnover_pack
from src.backtest.predictors import make_true_prob, make_turnover
from src.backtest.runner import run_backtest
from src.backtest.slice import compare_facts, slice_facts
from src.backtest.settle import outcome_y, payout_unit, payout_unit_vec, settle
from src.backtest.store import get_backtest_store
from src.backtest.world import build_world, label_key, phase_at
from src.features.preprocessor import Preprocessor
from src.pricing.pools import resolve

FAILURES = []


def check(name, ok, detail=""):
    print("  {:<62} {}".format(name, "ok" if ok else "FAILED"))
    if not ok:
        FAILURES.append("{}{}".format(name, ": " + detail if detail else ""))


def test_settle():
    had = resolve("HAD")
    hilo = resolve("HILO")
    hdc = resolve("HDC")
    fhlo = resolve("FHLO")
    ooe = resolve("OOE")
    crs = resolve("CRS")
    hft = resolve("HFT")

    check("HAD home win", settle(had, "H", None, (2, 1), (1, 0), (5, 4), (2, 2)) == 1.0)
    check("HAD draw lose", settle(had, "D", None, (2, 1), (1, 0), (5, 4), (2, 2)) == -1.0)
    check("HILO 2.5 over wins on 3",
          settle(hilo, "H", 2.5, (2, 1), (1, 0), (5, 4), (2, 2)) == 1.0)
    check("HILO 3.5 under wins on 3",
          settle(hilo, "L", 3.5, (2, 1), (1, 0), (5, 4), (2, 2)) == 1.0)
    check("HILO 3.0 push",
          settle(hilo, "H", 3.0, (2, 1), (1, 0), (5, 4), (2, 2)) == 0.0)
    # 2.25 = 2.0 / 2.5: total 3 is a full win on both halves of the over.
    check("HILO 2.25 over full win",
          settle(hilo, "H", 2.25, (2, 1), (1, 0), (5, 4), (2, 2)) == 1.0)
    # 2.75 = 2.5 / 3.0: total 3 wins 2.5 and pushes 3.0 -> half win.
    check("HILO 2.75 over half-win",
          settle(hilo, "H", 2.75, (2, 1), (1, 0), (5, 4), (2, 2)) == 0.5)
    check("HDC 0 home covers 2-1",
          settle(hdc, "H", 0.0, (2, 1), (1, 0), (5, 4), (2, 2)) == 1.0)
    check("FHLO 1.5 over on HT 1-0",
          settle(fhlo, "H", 1.5, (2, 1), (1, 0), (5, 4), (2, 2)) == -1.0)
    check("OOE odd on 3 goals",
          settle(ooe, "O", None, (2, 1), (1, 0), (5, 4), (2, 2)) == 1.0)
    check("CRS exact 2:1",
          settle(crs, "2:1", None, (2, 1), (1, 0), (5, 4), (2, 2)) == 1.0)
    check("HFT HD (HT home, FT draw) loses 2-1",
          settle(hft, "HD", None, (2, 1), (1, 0), (5, 4), (2, 2)) == -1.0)
    check("HFT HA wins",
          settle(hft, "HA", None, (2, 1), (1, 0), (5, 4), (2, 2)) == -1.0)
    check("HFT HH wins on HT 1-0 FT 2-1",
          settle(hft, "HH", None, (2, 1), (1, 0), (5, 4), (2, 2)) == 1.0)

    check("payout full win", abs(payout_unit(2.10, 1.0) - 2.10) < 1e-9)
    check("payout push", abs(payout_unit(2.10, 0.0) - 1.0) < 1e-9)
    check("payout half-win", abs(payout_unit(2.0, 0.5) - 1.5) < 1e-9)
    check("outcome_y push is None", outcome_y(0.0) is None)
    check("outcome_y win is 1", outcome_y(1.0) == 1.0)

    # payout_unit is the readable ladder; payout_unit_vec is what the metric
    # packs actually call. They must not drift.
    import numpy as np
    rng = np.random.default_rng(3)
    odds = rng.uniform(1.01, 40.0, 500)
    results = rng.choice([1.0, 0.5, 0.0, -0.5, -1.0, np.nan], 500)
    scalar = np.array([payout_unit(o, r) if pd.notna(r) else np.nan
                       for o, r in zip(odds, results)])
    check("payout_unit_vec matches the scalar ladder",
          np.allclose(scalar, payout_unit_vec(odds, results), equal_nan=True))


def test_metrics():
    frame = pd.DataFrame({
        "t_hat": [100.0, 200.0, 50.0],
        "t_actual": [80.0, 200.0, 70.0],
        "true_prob": [0.4, 0.5, 0.8],
        "result": [1.0, -1.0, 1.0],
        "sell_odds": [2.0, 2.0, 1.2],
        "sell_odds_star": [1.8, 2.2, 1.1],
        "phase": ["prematch", "first_half", "first_half"],
        "pool_code": ["HAD", "HAD", "HILO"],
        "family": ["Result", "Result", "Totals"],
        "as_of": ["2026-09-11T19:00:00"] * 3,
        "day": ["2026-09-11"] * 3,
    })
    t = turnover_pack(frame)
    check("turnover n", t["n"] == 3)
    check("turnover MAE", abs(t["mae"] - (20 + 0 + 20) / 3) < 1e-3)
    check("turnover MSE", abs(t["mse"] - (400 + 0 + 400) / 3) < 1e-3)
    check("turnover mean_diff", abs(t["mean_diff"] - (20 + 0 - 20) / 3) < 1e-6)
    check("turnover has share_mae", "share_mae" in t)
    check("turnover plots present", bool(t.get("plots", {}).get("scatter")))

    o = true_odds_pack(frame)
    check("true-odds n", o["n"] == 3)
    check("true-odds logloss finite", o["logloss"] > 0)
    check("calibration has bins", len(o["calibration"]) == 10)
    check("true-odds has ECE", "ece" in o)
    check("true-odds has accuracy", 0.0 <= o["accuracy"] <= 1.0)
    check("true-odds plots present", bool(o.get("plots", {}).get("reliability")))

    # Hand E[GM] on first row: 100 * (1 - 0.4 * 2.0) = 20 now,
    # 100 * (1 - 0.4 * 1.8) = 28 star.
    g = gm_pack(frame)
    check("gm settled", g["n_settled"] == 3)
    check("gm days counted", g["days_better"] + g["days_worse"] + g["days_tie"] == 1)


def test_metrics_wide_panel():
    """A real 5-minute panel has thousands of buckets, not a handful of ticks.

    The per-bucket stats must stay exact at full resolution while the plotted
    series is folded down to something a chart can draw.
    """
    import numpy as np
    from src.backtest.metrics import PLOT_POINTS, _quantile_bins

    n_buckets = PLOT_POINTS * 3 + 7
    rng = np.random.default_rng(5)
    stamps = pd.date_range("2026-07-01 12:00", periods=n_buckets, freq="5min")
    frame = pd.DataFrame({
        "as_of": stamps,
        "day": stamps.strftime("%Y-%m-%d"),
        "t_hat": rng.uniform(50, 500, n_buckets),
        "t_actual": rng.uniform(50, 500, n_buckets),
        "t_hat_star": rng.uniform(50, 500, n_buckets),
        "t_actual_star": rng.uniform(50, 500, n_buckets),
        "true_prob": rng.uniform(0.05, 0.9, n_buckets),
        "sell_odds": rng.uniform(1.1, 20.0, n_buckets),
        "sell_odds_star": rng.uniform(1.1, 20.0, n_buckets),
        "result": rng.choice([1.0, 0.5, 0.0, -0.5, -1.0], n_buckets),
        "phase": "first_half",
        "pool_code": "HILO",
        "family": "Totals",
    })
    g = gm_pack(frame)
    decided = g["ticks_better"] + g["ticks_worse"] + g["ticks_tie"]
    check("per-bucket counts stay at full resolution", decided == n_buckets,
          "{} != {}".format(decided, n_buckets))

    plotted = g["plots"]["lift_by_tick"]
    check("plotted series is capped", len(plotted) <= PLOT_POINTS,
          "{} points".format(len(plotted)))
    check("folding preserves total lift",
          abs(sum(p["value"] for p in plotted) - g["realized_lift"]) < 1.0,
          "{} vs {}".format(sum(p["value"] for p in plotted), g["realized_lift"]))

    # equal-mass bins: counts within one, and lo/hi are real bin edges
    vals = rng.uniform(0.0, 1.0, 1000)
    agg = _quantile_bins(vals, (vals > 0.5).astype(float), 10)
    check("quantile bins are equal mass", int(agg["n"].max() - agg["n"].min()) <= 1)
    check("quantile bin edges bracket the mean",
          bool(((agg["lo"] <= agg["v_hat"]) & (agg["v_hat"] <= agg["hi"])).all()))
    check("quantile bins cover every row", int(agg["n"].sum()) == len(vals))


def test_slice_cache():
    """Scoring the same stack twice must replay, not recompute."""
    from src.backtest import slice as slice_mod

    facts = pd.DataFrame({
        "t_hat__persistence": [100.0, 200.0, 50.0],
        "t_actual": [80.0, 200.0, 70.0],
        "p__hkjc_true__raw": [0.4, 0.5, 0.8],
        "result": [1.0, -1.0, 1.0],
        "sell_odds": [2.0, 2.0, 1.2],
        "phase": ["prematch", "first_half", "first_half"],
        "period": ["prematch", "inplay", "inplay"],
        "clock_bin": ["pre", "00-15", "00-15"],
        "pool_code": ["HAD", "HAD", "HILO"],
        "family": ["Result", "Result", "Totals"],
        "as_of": ["2026-09-11T19:00:00"] * 3,
        "day": ["2026-09-11"] * 3,
    })
    slice_mod.invalidate()
    before = slice_mod.cache_stats()["entries"]
    a = slice_mod.slice_cached("run-a", facts, group_by="clock_bin", filters={})
    grew = slice_mod.cache_stats()["entries"] - before
    b = slice_mod.slice_cached("run-a", facts, group_by="clock_bin", filters={})
    check("first score is cached", grew == 1, "grew by {}".format(grew))
    check("second score replays the same object", a is b)

    # a different request must not collide with the first
    c = slice_mod.slice_cached("run-a", facts, group_by="pool_code", filters={})
    check("a different group_by is its own entry", c is not a)
    # nor may a different run reuse another run's answer
    d = slice_mod.slice_cached("run-b", facts, group_by="clock_bin", filters={})
    check("a different run is its own entry", d is not a)

    dropped = slice_mod.invalidate("run-a")
    check("invalidate drops only that run", dropped == 2, "dropped {}".format(dropped))
    e = slice_mod.slice_cached("run-a", facts, group_by="clock_bin", filters={})
    check("after invalidate the answer is rebuilt", e is not a)
    slice_mod.invalidate()


def test_shared_panel_cache():
    """3-run and the web page must land on the same cache folder and files."""
    import tempfile
    from pathlib import Path
    from src.backtest.parquet.cache import (
        PanelCache, default_cache_root, ingest_flat_facts, resolve_cache_root,
    )
    from src.backtest.parquet.config import Config

    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "Raw_Data"
        data.mkdir()
        cfg = Config(data_dir=data, out_dir=Path(tmp) / "out")
        root = default_cache_root(cfg)
        check("cache sits beside the parquet root",
              root == data.parent / "algoe_panel_cache",
              "{} vs {}".format(root, data.parent / "algoe_panel_cache"))

        frame = pd.DataFrame({
            "turnover": [10.0, 20.0],
            "match_id": [1, 1],
        })
        facts_dir = Path(tmp) / "out" / "facts"
        facts_dir.mkdir(parents=True)
        frame.to_parquet(facts_dir / "facts_20260704.parquet", index=False)

        cache = PanelCache(cfg, resolve_cache_root(cfg))
        n = ingest_flat_facts(cache, facts_dir)
        check("older 3-run facts are absorbed", n == 1, "ingested {}".format(n))
        check("absorbed day is cached", cache.has_day("2026-07-04"))
        again = ingest_flat_facts(cache, facts_dir)
        check("second ingest is a no-op", again == 0)
        loaded = cache.load_day("2026-07-04")
        check("cached panel matches the facts file",
              loaded is not None and float(loaded["turnover"].sum()) == 30.0)

        from src.backtest.parquet.cache import facts_dir_guesses
        other = Config(data_dir=data, out_dir=Path(tmp) / "somewhere-else")
        guessed = facts_dir_guesses(other)
        check("selftest-style out_dir does not pick up cwd/out/facts",
              facts_dir.resolve() not in [p.resolve() for p in guessed])


def test_parquet_accuracy_headline():
    """Build + register must expose WAPE/ECE without running the solver."""
    import numpy as np
    from src.backtest.parquet.config import Config
    from src.backtest.parquet.evaluate import score_frames
    from src.backtest.parquet.service import build_report

    n = 40
    panel = pd.DataFrame({
        "match_id": [1] * n,
        "pool_name": ["HILO"] * n,
        "pool_id": [21] * n,
        "line_id": [1] * n,
        "combination_id": list(range(n)),
        "turnover": np.linspace(100.0, 400.0, n),
        "lag1": np.linspace(90.0, 380.0, n),
        "f_persist": np.linspace(90.0, 380.0, n),
        "f_ma3": np.linspace(95.0, 385.0, n),
        "f_ema": np.linspace(92.0, 382.0, n),
        "f_zero": np.zeros(n),
        "settled": [True] * n,
        "y_frac": np.where(np.arange(n) % 2 == 0, 1.0, 0.0),
        "p_true": np.where(np.arange(n) % 2 == 0, 0.55, 0.45),
        "p_sell": np.where(np.arange(n) % 2 == 0, 0.52, 0.48),
        "sell_book_sum": np.ones(n),
        "book_sum": np.ones(n),
        "realized_gm": np.full(n, 10.0),
        "expected_gm": np.full(n, 12.0),
        "dividend": np.full(n, 90.0),
        "bucket": pd.date_range("2026-07-01 19:00", periods=n, freq="5min"),
        "clock_bin": ["T-30"] * (n // 2) + ["1H"] * (n - n // 2),
        "odds_bin": ["2-3"] * n,
        "league_code": ["EPL"] * n,
        "p_source": ["true_odds"] * n,
        "is_inplay": [False] * (n // 2) + [True] * (n - n // 2),
        "game_state": ["Prematch"] * (n // 2) + ["FirstHalf"] * (n - n // 2),
        "match_minute": [np.nan] * (n // 2) + [20.0] * (n - n // 2),
        "combination_string": ["H"] * n,
        "line_label": ["2.5"] * n,
        "tg": np.full(n, 2.4),
        "sup": np.where(np.arange(n) % 3 == 0, 0.1, 1.4),
        "odds_age_min": np.where(np.arange(n) % 2 == 0, 1.0, 20.0),
        "event_window": ["prematch"] * (n // 2) + ["quiet"] * (n - n // 2),
    })
    accuracy = score_frames([(pd.Timestamp("2026-07-01"), panel)])
    import json
    json.dumps(accuracy)
    check("accuracy payload is JSON-serializable", True)
    persist = accuracy["turnover"]["models"]["active|persist"]
    check("accuracy persist WAPE is a fraction",
          persist["wape"] is not None and 0 <= persist["wape"] < 1,
          str(persist.get("wape")))
    check("accuracy persist beats forecast-zero",
          persist["mae"] < accuracy["turnover"]["models"]["active|zero"]["mae"])
    check("accuracy money ECE computed",
          accuracy["belief"]["calibration"]["p_true"]["money_ece"] is not None)
    check("accuracy verdict has turnover",
          any("5min" in v["topic"] for v in accuracy["verdict"]))
    phases = [r for r in accuracy["turnover"]["by_slice"]
              if r["dim"] == "phase" and r["model"] == "persist"]
    check("turnover is cut prematch and 1H",
          {r["value"] for r in phases} >= {"prematch", "1H"})
    check("turnover is cut by contract",
          any(r["dim"] == "kind" and r["value"] == "totals"
              for r in accuracy["turnover"]["by_slice"]))
    check("turnover crosses phase and contract",
          any(r["dim"] == "book" and r["value"] == "1H · totals"
              for r in accuracy["turnover"]["by_slice"]))
    check("belief is cut by TG x SUP",
          any(r["dim"] == "matchup" for r in accuracy["belief"]["by_slice"]))
    check("belief is cut by quote age",
          any(r["dim"] == "quote_age" and r["value"] == "stale"
              for r in accuracy["belief"]["by_slice"]))
    check("accuracy verdict has no solver line",
          not any("theta" in v["topic"].lower() or "pricer" in v["topic"].lower()
                  for v in accuracy["verdict"]))

    facts = pd.DataFrame({
        "settled": [True] * n,
        "t_actual": panel["turnover"],
        "match_id": [1] * n,
        "as_of": panel["bucket"],
    })
    cov = {"n_cached": 1, "n_optimized": 0, "space": "test"}
    meta = {"days": ["2026-07-01"], "empty_days": [], "trimmed_days": [],
            "models": {"turnover": ["persistence"], "beliefs": ["hkjc_true/raw"],
                       "algos": ["hold"]}}
    report = build_report(
        "pq-test", Config(), cov, meta, facts, None, ["raw"], accuracy=accuracy)
    check("headline persist WAPE set before solver",
          report["headline"]["persist_wape"] == persist["wape"])
    check("headline money ECE set before solver",
          report["headline"]["money_ece"] is not None)
    check("headline E[GM] lift stays empty",
          report["headline"]["opt_lift_pct"] is None)
    check("report carries accuracy block",
          report.get("accuracy", {}).get("turnover") is not None)
    check("merged verdict starts with accuracy",
          "5min" in report["verdict"][0]["topic"])


def test_live_optimizer_patch_keeps_accuracy():
    """Each solved day rewrites only the solver half of a stored report."""
    from src.backtest.parquet.service import apply_optimizer

    report = {
        "headline": {"persist_wape": 0.31, "money_ece": 0.04, "opt_lift_pct": None},
        "accuracy": {"verdict": [
            {"topic": "next 5min = last 5min", "verdict": "usable", "detail": "ok"},
        ]},
        "verdict": [
            {"topic": "next 5min = last 5min", "verdict": "usable", "detail": "ok"},
        ],
        "optimizer": None,
    }
    optimizer = {
        "totals": {"all": {
            "lift_vs_actual_pct": 0.048,
            "margin_opt": 0.12,
            "margin_actual_odds": 0.07,
            "buckets_better": 10,
            "buckets_worse": 1,
        }},
        "verdict": [
            {"topic": "optimised theta vs HKJC's board", "verdict": "better", "detail": "lift"},
        ],
    }
    cov = {"n_optimized": 3, "n_cached": 10}
    apply_optimizer(report, optimizer, cov, live={"solving": True, "done": 3, "total": 10})
    check("live patch keeps persist WAPE", report["headline"]["persist_wape"] == 0.31)
    check("live patch writes E[GM] lift", report["headline"]["opt_lift_pct"] == 0.048)
    check("live patch writes solved days", report["headline"]["optimized_days"] == 3)
    check("accuracy verdict survives",
          report["verdict"][0]["topic"] == "next 5min = last 5min")
    check("solver verdict is appended",
          report["verdict"][-1]["topic"].startswith("optimised"))
    check("live flag is on while solving", report["optimizer_live"]["solving"] is True)
    apply_optimizer(report, optimizer, cov, live=None)
    check("live flag clears when the job finishes", report["optimizer_live"] is None)


def test_publish_optimizer_roundtrip():
    """The Solve job must be able to rewrite the stored run mid-flight."""
    import tempfile

    from src.backtest.parquet.config import Config
    from src.backtest.parquet import service as pqsvc
    from src.backtest.store import BacktestStore

    tmp = Path(tempfile.mkdtemp())
    store = BacktestStore(tmp / "bt.sqlite")
    run_id = "pq-20260701-20260702-test"
    store.save({
        "run_id": run_id,
        "created_at": "2026-09-24T00:00:00",
        "headline": {"persist_wape": 0.2, "money_ece": 0.01, "opt_lift_pct": None},
        "accuracy": {"verdict": [
            {"topic": "next 5min = last 5min", "verdict": "usable", "detail": "ok"},
        ]},
        "verdict": [
            {"topic": "next 5min = last 5min", "verdict": "usable", "detail": "ok"},
        ],
        "optimizer": None,
        "n_rows": 1,
        "config": {},
    })

    class Cache:
        space = "test"

        def coverage(self, start, end):
            return {"n_optimized": 0, "n_cached": 1, "space": "test"}

        def load_opt_range(self, start, end):
            return pd.DataFrame(), pd.DataFrame()

    orig = pqsvc.get_backtest_store
    pqsvc.get_backtest_store = lambda path=None: store
    try:
        out = pqsvc.publish_optimizer(
            Config(start_date="2026-07-01", end_date="2026-07-02"),
            Cache(), live=True)
        check("publish returns the stored run", out is not None and out["run_id"] == run_id)
        check("publish marks the run live", out["optimizer_live"]["solving"] is True)
        check("publish keeps persist WAPE", out["headline"]["persist_wape"] == 0.2)
        again = store.get(run_id)
        check("the next page load sees the live flag",
              again["optimizer_live"]["solving"] is True)
    finally:
        pqsvc.get_backtest_store = orig


def test_world_and_labels():
    world = build_world(seed=7, n_matches=2, n_ticks=3)
    check("timeline length", len(world.timeline) == 3)
    check("matches generated", len(world.matches) == 2)

    t0, t1 = world.timeline[0], world.timeline[1]
    snap0 = world.snapshot(t0)
    snap1 = world.snapshot(t1)
    check("snapshot has match_info", not snap0.match_info.empty)
    ids0 = set(int(x) for x in snap0.match_info["match_id"])
    check("first snapshot has matches", len(ids0) > 0)
    check("second snapshot built", not snap1.match_info.empty)

    pre = Preprocessor()
    frame = pre.build(snap0)
    attach_labels(frame, world)
    sel = frame.selections
    check("labels attached", "t_actual" in sel.columns and "result" in sel.columns)
    check("slice dimensions", "period" in sel.columns and "clock_bin" in sel.columns)
    check("competition tagged", sel["competition"].isin(("league", "cup", "friendly")).all())
    check("some next-bucket money", float(sel["t_actual"].sum()) > 0)
    check("some settled rows", sel["result"].notna().any())

    # t_actual for t0 should equal investments in (t0, t1].
    labels = world.labels(t0)
    if not labels.empty and not sel.empty:
        joined = sel.merge(labels[["label_key", "t_actual"]], on="label_key",
                           how="inner", suffixes=("", "_lab"))
        if not joined.empty:
            check("label join agrees",
                  abs(float(joined["t_actual"].sum()) - float(joined["t_actual_lab"].sum())) < 1e-6)

    phase, minute = phase_at(world.matches[0].kickoff, t0)
    check("phase_at returns a known phase",
          phase in ("prematch", "first_half", "half_time", "second_half", "full_time"))
    check("label_key stable",
          label_key(1, "HILO", 2.5, "H") == label_key(1, "HILO", 2.5, "H"))


def test_predictors():
    for name in ("persistence", "trailing_mean", "gametime", "blend",
                 "momentum", "match_share", "oracle"):
        check("turnover " + name, make_turnover(name).model == name)
    for name in ("hkjc_true", "market", "model"):
        check("true-prob " + name, make_true_prob(name).source in ("hkjc_true", "market", "model"))
    basis = make_true_prob("model_basis")
    check("model_basis uses Basis layer", basis.layer == "Basis" and basis.source == "model")
    check("true-prob is strict (no fallback)", make_true_prob("hkjc_true").fallback is False)


def test_calibrators():
    from src.backtest.calibrators import (
        IsotonicCalibrator, ShrinkCalibrator, TemperatureCalibrator, _pav,
    )
    import numpy as np
    p = np.array([0.2, 0.8, 0.2, 0.8])
    y = np.array([0.0, 1.0, 0.0, 1.0])
    shrink = ShrinkCalibrator(weight=0.5).fit(p, y)
    out = shrink.transform(np.array([0.2]))
    check("shrink moves toward base rate", abs(out[0] - 0.5 * 0.2 - 0.5 * 0.5) < 1e-6)
    raw_t = TemperatureCalibrator()
    check("unfitted temperature is identity", abs(raw_t.transform(np.array([0.3]))[0] - 0.3) < 1e-9)
    fitted = _pav(np.array([0.1, 0.4, 0.8]), np.array([0.0, 0.9, 0.2]))
    check("PAV is nondecreasing", fitted[1] <= fitted[2] + 1e-9)
    iso = IsotonicCalibrator().fit(np.linspace(0.1, 0.9, 40),
                                   (np.linspace(0.1, 0.9, 40) > 0.5).astype(float))
    q = iso.transform(np.array([0.2, 0.8]))
    check("isotonic fitted", q[0] <= q[1] + 1e-9)


def test_runner_quick():
    report = run_backtest(
        seed=11, n_matches=2, n_ticks=3,
        turnover=["persistence", "trailing_mean"],
        true_prob=["hkjc_true", "model"],
        calibrators=["raw", "shrink"],
        algos=["hold"],
        policies=["always"],
        elasticities=[0.0],
        optimize="none",
        persist=False,
    )
    labs = report["labs"]
    check("mode is labs", report.get("mode") == "labs")
    check("turnover lab has 2", len(labs["turnover"]["candidates"]) == 2)
    check("prob lab has 4", len(labs["true_prob"]["candidates"]) == 4)
    check("optimizer lab is hold-only",
          [c["algo"] for c in labs["optimizer"]["candidates"]] == ["hold"])
    check("report has labelled rows", report["n_rows"] > 0)
    check("leaderboard turnover ranked",
          report["leaderboard"]["turnover"][0]["value"] >= 0)
    persist = next(c for c in labs["turnover"]["candidates"] if c["id"] == "persistence")
    check("MVP turnover pack populated", persist["turnover"]["n"] > 0)
    check("turnover plots on MVP", bool(persist["turnover"]["plots"].get("residual_hist")))
    belief = next(c for c in labs["true_prob"]["candidates"] if c["id"] == "hkjc_true/raw")
    check("MVP true-odds pack populated", belief["true_odds"]["n"] > 0)
    check("ECE plotted", "ece" in belief["true_odds"])
    check("attribution has steps", len(report["attribution"]) >= 3)
    check("headline names a turnover leader", bool(report["headline"]["best_turnover"]))
    check("headline names a belief leader", bool(report["headline"]["best_true_odds"]))
    check("workbench catalog present", bool((report.get("workbench") or {}).get("has_facts")))

    facts = get_backtest_store().load_facts(report["run_id"])
    check("facts cached", facts is not None and len(facts) > 0)
    check("facts have persistence", "t_hat__persistence" in facts.columns)
    check("facts have hkjc_true/raw", "p__hkjc_true__raw" in facts.columns)
    check("facts have clock_bin", "clock_bin" in facts.columns)

    full = slice_facts(facts)
    pre = slice_facts(facts, filters={"period": ["prematch"]})
    check("slice has mse", "mse" in full["turnover"])
    check("slice has accuracy", "accuracy" in full["true_odds"])
    check("slice filter shrinks or equals", pre["n_rows"] <= full["n_rows"])
    check("compare lists turnover", len(full["compare"]["turnover"]) >= 2)
    check("gallery has clock mae", bool(full["gallery"].get("mae_by", {}).get("clock_bin") is not None))
    check("breakdown rows", len(full["breakdown"]) >= 1)
    persist = next(c for c in full["compare"]["turnover"] if c["id"] == "persistence")
    check("MVP flagged in compare", persist.get("baseline") is True)

    duo = compare_facts(
        facts,
        left={"turnover": "persistence", "true_prob": "hkjc_true", "calibrator": "raw"},
        right={"turnover": "trailing_mean", "true_prob": "hkjc_true", "calibrator": "raw"},
    )
    check("A/B same row count", duo["n_rows"] == full["n_rows"])
    check("A/B has residual overlay", bool(duo["dists"].get("residual")))
    check("A/B has scorecard", len(duo["delta"]) >= 8)
    mae_row = next(r for r in duo["delta"] if r["key"] == "mae")
    check("A/B MAE sides differ or tie", mae_row["a"] >= 0 and mae_row["b"] >= 0)
    check("A/B cut table", len(duo["cuts"]) >= 1)
    check("A/B left id", "persistence" in duo["left"]["stack"]["id"])


def main():
    print("\nbacktest settlement")
    test_settle()
    print("\nbacktest metrics")
    test_metrics()
    print("\nbacktest metrics on a wide 5-minute panel")
    test_metrics_wide_panel()
    print("\nbacktest slice cache")
    test_slice_cache()
    print("\nshared panel cache (3-run and the web page)")
    test_shared_panel_cache()
    print("\nparquet accuracy without the solver")
    test_parquet_accuracy_headline()
    print("\nlive optimizer dashboard patch")
    test_live_optimizer_patch_keeps_accuracy()
    test_publish_optimizer_roundtrip()
    print("\nbacktest world / labels")
    test_world_and_labels()
    print("\nbacktest predictors")
    test_predictors()
    print("\nbacktest calibrators")
    test_calibrators()
    print("\nbacktest runner (labs, hold only)")
    test_runner_quick()

    print()
    if FAILURES:
        print("{} FAILED".format(len(FAILURES)))
        for item in FAILURES:
            print("  -", item)
        return 1
    print("all backtest checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
