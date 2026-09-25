"""End-to-end self test on synthetic parquet.

Writes fake data using the RAW column names from the extraction notebooks
(EnumString, line_no, comb_id, EventLevel2ID, MatchId, ...) so the aliasing in
loader.py is exercised too, then runs the whole backtest and checks the outputs.

    python run.py selftest

This proves the code runs before you copy it to the offline machine. It says
nothing about the real data; use `python run.py probe` for that.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from .config import Config
from .run import run_backtest

POOL_SPECS = [
    ("HILO", 21, ["2.5", "3", "3/3.5"], ["H", "L"]),
    ("HDC", 24, ["-0.5", "-1/-1.5"], ["H", "A"]),
    ("CHLO", 22, ["9.5", "10/10.5"], ["H", "L"]),
]
# small so the pricer replication check has something to detect but still passes
ODDS_DRIFT = 0.01


def _fixture_odds(pool_name, label, tg, sup, ctg, csup):
    """Fair odds for one line, generated the same way the pricer reads them.

    The fixture is deliberately a self-consistent world: odds come from the same
    Poisson model and the same theta that get written into HKJC_Parameters. That
    makes the pricer replication check a real end-to-end assertion - it will fail
    loudly if the line parsing, the HDC sign convention, the margin table or the
    panel join is wrong, instead of quietly reporting a large error.
    """
    from .config import DEFAULT_MARGINS
    from . import pricing

    domain, kind = pricing.POOL_KIND[pool_name]
    theta = (ctg, csup) if domain.startswith("corner") else (tg, sup)
    m = pricing.score_matrix(*theta)
    line = pricing.parse_line(label)
    fair = (pricing.asian_handicap(m, line) if kind == "ah"
            else pricing.over_under(m, line))
    margin = DEFAULT_MARGINS.get(pool_name, DEFAULT_MARGINS["_default"])
    sell = tuple(o / (1.0 + margin) if o > 0 else 0.0 for o in fair)
    return fair, sell


def make_fixture(root: Path, days: int = 3, matches_per_day: int = 6,
                 start: str = "2026-07-01", seed: int = 11) -> None:
    rng = np.random.default_rng(seed)
    for sub in ("HKJC_Odds", "Match_Investments", "Pool_Details", "Match_Information",
                "HKJC_Parameters", "Match_Events", "Market_Odds"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    odds_rows, inv_rows, pool_rows, match_rows = [], [], [], []
    param_rows, event_rows, market_rows = [], [], []
    pool_id = 500_000
    match_id = 90_000
    day0 = pd.Timestamp(start)

    for d in range(days):
        day = day0 + pd.Timedelta(days=d)
        for m in range(matches_per_day):
            match_id += 1
            ko = day + pd.Timedelta(hours=19, minutes=30 * (m % 4))
            match_rows.append({
                "MatchId": match_id, "LeagueCode": ["EPL", "SPA", "GER"][m % 3],
                "LeagueName": ["Premier", "LaLiga", "Bundes"][m % 3],
                "KOTime": ko, "HomeName": f"H{m}", "AwayName": f"A{m}",
                "HomeScoreFT": int(rng.integers(0, 4)),
                "AwayScoreFT": int(rng.integers(0, 4)), "IsVoid": 0,
            })
            tg = float(2.4 + 0.4 * rng.standard_normal())
            sup = float(0.3 * rng.standard_normal())
            tg = max(tg, abs(sup) + 0.4)
            ctg = float(9.6 + 0.6 * rng.standard_normal())
            csup = float(0.3 * rng.standard_normal())
            # params tick every 5 minutes across the live window
            for t in pd.date_range(ko - pd.Timedelta(hours=3),
                                   ko + pd.Timedelta(minutes=100), freq="5min"):
                mins = (t - ko).total_seconds() / 60
                if mins < 0:
                    state, secs = "Prematch", 0
                elif mins <= 45:
                    state, secs = "FirstHalf", mins * 60
                elif mins <= 60:
                    state, secs = "FirstHalf", 45 * 60
                else:
                    state, secs = "SecondHalf", min(mins - 15, 90) * 60
                param_rows.append({
                    "EventLevel2ID": match_id, "EventTime": t, "GameState": state,
                    "TimeInSecond": secs, "Goal90CurrentTG": tg,
                    "Goal90CurrentSUP": sup, "Corner90CurrentTG": ctg,
                    "Corner90CurrentSUP": csup, "Goal90SecondHalfSplitFH": 0.46,
                    "Corner90SecondHalfSplitFH": 0.47,
                })
            for t in pd.date_range(ko + pd.Timedelta(minutes=8),
                                   ko + pd.Timedelta(minutes=90), freq="23min"):
                event_rows.append({"event_id": match_id, "incident_datetime": t,
                                   "incident_type": 27 if rng.random() < 0.5 else 13,
                                   "home_or_away": int(rng.integers(1, 3)),
                                   "provider_id": 1})

            for pool_name, bet_code, lines, combos in POOL_SPECS:
                pool_id += 1
                open_at = ko - pd.Timedelta(hours=8)
                close_at = ko + pd.Timedelta(minutes=100)
                pool_rows.append({
                    "pool_id": pool_id, "match_id": match_id, "pool_name": pool_name,
                    "pool_type": "FullTime", "start_selling_time": open_at,
                    "close_time": close_at, "kickoff_date": ko.normalize(),
                })
                for li, label in enumerate(lines, start=1):
                    fair, sell_pair = _fixture_odds(pool_name, label, tg, sup, ctg, csup)
                    if min(fair) <= 1.0:
                        continue
                    p_eff = [1.0 / fair[0], 1.0 / fair[1]]
                    tot = sum(p_eff)
                    p_home = p_eff[0] / tot
                    probs = {combos[0]: p_eff[0], combos[1]: p_eff[1]}
                    fairs = {combos[0]: fair[0], combos[1]: fair[1]}
                    sells = {combos[0]: sell_pair[0], combos[1]: sell_pair[1]}
                    winner = combos[0] if rng.random() < p_home else combos[1]
                    # money arrives with strong autocorrelation, so persistence has signal
                    grid = pd.date_range(ko - pd.Timedelta(hours=2),
                                         close_at, freq="1min")
                    base = np.zeros(len(grid))
                    level = 400.0
                    for i, t in enumerate(grid):
                        mins = (t - ko).total_seconds() / 60
                        pull = 2500.0 if 0 <= mins <= 90 else 900.0
                        level = 0.86 * level + 0.14 * pull * (0.4 + rng.random())
                        base[i] = level
                    for ci, comb in enumerate(combos, start=1):
                        true_odds = fairs[comb]
                        sell = sells[comb]
                        for t in pd.date_range(ko - pd.Timedelta(hours=3),
                                              close_at, freq="5min"):
                            drift = 1.0 + ODDS_DRIFT * rng.standard_normal()
                            odds_rows.append({
                                "match_id": match_id, "bet_type_code": bet_code,
                                "EnumString": pool_name, "pool_id": pool_id,
                                "line_id": li, "line_label": label,
                                "combination_id": ci, "combination_string": comb,
                                "odds": round(sell * drift, 2),
                                "effective_datetime": t,
                                "true_odds": round(true_odds * drift, 3),
                            })
                        share = 0.5 + 0.1 * rng.standard_normal()
                        won = comb == winner
                        for i, t in enumerate(grid):
                            if rng.random() < 0.25:      # not every minute takes money
                                continue
                            turn = max(base[i] * share * (0.5 + rng.random()), 0.0)
                            if turn <= 0:
                                continue
                            div = turn * sell if won else 0.0
                            inv_rows.append({
                                "pool_id": pool_id, "pool_name": pool_name,
                                "pool_type": "FullTime", "match_id": match_id,
                                "line_no": li, "comb_id": ci, "odds_id": ci,
                                "turnover": round(turn, 2),
                                "dividend": round(div, 2),
                                "es_dividend": round(div * 0.98, 2),
                                "odds_combination": comb, "odds": round(sell, 2),
                                "ticket_count": int(1 + rng.integers(0, 8)),
                                "start_sell_time": t, "kickoff_date": ko.normalize(),
                                "tournament": "T",
                            })
                        market_rows.append({
                            "match_id": match_id, "pool_id": pool_id,
                            "bet_type_code": bet_code, "bet_type_code_str": pool_name,
                            "bookmaker": 1, "bookmaker_str": "Pinnacle",
                            "line_id": li, "line_label": label,
                            "combination_id": ci, "combination_string": comb,
                            "odds": round(true_odds * 0.98, 2),
                            "effective_datetime": ko - pd.Timedelta(minutes=30),
                        })

    def dump(rows, sub, name):
        df = pd.DataFrame(rows)
        df.to_parquet(root / sub / name, index=False)
        return len(df)

    n = {
        "odds": dump(odds_rows, "HKJC_Odds", "hkjc_odds_2026-07_2026-10.parquet"),
        "inv": dump(inv_rows, "Match_Investments", "match_investments_2026-07.parquet"),
        "pools": dump(pool_rows, "Pool_Details", "pool_details.parquet"),
        "matches": dump(match_rows, "Match_Information", "match_information.parquet"),
        "params": dump(param_rows, "HKJC_Parameters", "hkjc_parameters_2026-07.parquet"),
        "events": dump(event_rows, "Match_Events", "match_events_2026-07.parquet"),
        "market": dump(market_rows, "Market_Odds", "market_odds_2026-07.parquet"),
    }
    print("[fixture] " + "  ".join(f"{k}={v:,}" for k, v in n.items()))


def check_pricer_parity(tol: float = 1e-9) -> list:
    """pricing.py is a faster rewrite of poisson_betting, so prove they agree.

    Skipped, with a printed note, when poisson_betting.py has not been copied
    next to the script - it lives in hkjc_code, not here.
    """
    try:
        from . import poisson_betting as pb
    except Exception:
        print("[selftest] poisson_betting.py not present, pricer parity SKIPPED "
              "(copy it next to run.py to enable this check)")
        return []

    from . import pricing
    bad = []
    lines_ou = [1.5, 2.0, 2.5, 2.75, 3.25, 3.5, 9.5, 10.25]
    lines_ah = [-2.0, -1.75, -1.5, -1.25, -0.5, -0.25, 0.0, 0.5, 1.25, 1.75]
    checked = 0
    for tg in (1.6, 2.5, 3.4, 9.6):
        for sup in (-1.2, -0.3, 0.0, 0.7, 1.9):
            if tg < abs(sup):
                continue
            m = pricing.score_matrix(tg, sup)
            for line in lines_ou:
                mine = pricing.over_under(m, line)
                ref = pb.calculate_over_under_odds(tg, sup, line)
                for got, want, side in ((mine[0], ref["Over"], "Over"),
                                        (mine[1], ref["Under"], "Under")):
                    checked += 1
                    if abs(got - want) > tol * max(1.0, abs(want)):
                        bad.append(f"O/U {side} tg={tg} sup={sup} line={line}: "
                                   f"{got!r} vs poisson_betting {want!r}")
            for line in lines_ah:
                mine = pricing.asian_handicap(m, line)
                ref = pb.calculate_asian_handicap_odds(tg, sup, line)
                for got, want, side in ((mine[0], ref["Home"], "Home"),
                                        (mine[1], ref["Away"], "Away")):
                    checked += 1
                    if abs(got - want) > tol * max(1.0, abs(want)):
                        bad.append(f"AH {side} tg={tg} sup={sup} line={line}: "
                                   f"{got!r} vs poisson_betting {want!r}")
    print(f"[selftest] pricer parity: {checked:,} odds compared against "
          f"poisson_betting, {len(bad)} mismatch(es)")
    return bad[:10]


def run_selftest() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="algoe_selftest_"))
    try:
        root = tmp / "Raw_Data"
        make_fixture(root, days=3, matches_per_day=6, start="2026-07-01")
        cfg = Config(
            data_dir=root, out_dir=tmp / "out",
            cache_dir=tmp / "cache",
            start_date="2026-07-01", end_date="2026-07-03",
            pools=["HILO", "HDC", "CHLO"],
            prematch_window_min=180, sample_rows=2000, save_facts=True,
            optimize=True, optimize_every=12, optimize_max_buckets=120,
            optimize_starts=2,
        )
        rep = run_backtest(cfg)
        problems = check_pricer_parity() + check(rep, cfg) + check_optimizer(rep)
        print("\n[selftest] " + ("PASS" if not problems else "FAIL"))
        for p in problems:
            print("   - " + p)
        if not problems:
            keep = Path(__file__).parent / "sample_report"
            try:
                if keep.exists():
                    shutil.rmtree(keep)
                shutil.copytree(cfg.out_dir, keep,
                                ignore=shutil.ignore_patterns("facts"))
                print(f"[selftest] sample report copied to {keep / 'report.html'}")
            except OSError as exc:
                print(f"[selftest] could not refresh {keep}: {exc}")
        return 0 if not problems else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_optimizer(rep: dict) -> list:
    """The optimiser must at minimum never lose to the theta it started from."""
    bad = []
    o = rep.get("optimizer")
    if not o:
        return ["optimizer stage produced no output"]
    cov = o["coverage"]
    if cov["buckets_scored"] <= 0:
        return ["optimizer scored zero buckets"]
    a = o["totals"]["all"]
    for k in ("egm_opt", "egm_hkjc_theta", "egm_actual_odds", "margin_opt"):
        if a.get(k) is None:
            bad.append(f"optimizer totals missing {k}")
    # solving for theta cannot do worse than the warm start it began from,
    # since that point is inside the feasible set
    if a.get("egm_opt") is not None and a.get("egm_hkjc_theta") is not None:
        if a["egm_opt"] < a["egm_hkjc_theta"] - 1e-6 * max(1.0, abs(a["egm_hkjc_theta"])):
            bad.append(f"optimised E[GM] {a['egm_opt']:,.2f} is below the HKJC-theta "
                       f"baseline {a['egm_hkjc_theta']:,.2f}, so the solver moved "
                       f"the wrong way")
    if not o.get("theta"):
        bad.append("no theta comparison produced")
    rep = (o.get("prices") or {}).get("replication")
    if not rep:
        bad.append("no pricer replication check produced")
    elif rep["mae_rel"] > 0.05:
        # the fixture prices every line from the same theta it writes into
        # HKJC_Parameters, so anything beyond the injected odds drift means the
        # pricing path itself is wrong: line parsing, HDC sign, or the margins
        bad.append(f"pricer failed to reproduce the fixture board: mean abs "
                   f"relative error {rep['mae_rel']:.4f} against an injected "
                   f"drift of only {ODDS_DRIFT:.3f}")
    if not o.get("verdict"):
        bad.append("optimizer produced no verdict")
    return bad


def check(rep: dict, cfg: Config) -> list:
    bad = []
    cov = rep["coverage"]
    if cov["rows"] < 1000:
        bad.append(f"panel too small: {cov['rows']} rows")
    if cov["days"] != 3:
        bad.append(f"expected 3 days, got {cov['days']}")
    if cov["turnover"] <= 0:
        bad.append("no turnover captured")
    if cov["settled_rows"] <= 0:
        bad.append("no settled rows, dividend join failed")

    tm = rep["turnover"]["models"]
    for k in ("all|persist", "active|persist", "active|zero", "active|ema"):
        if k not in tm:
            bad.append(f"missing turnover model {k}")
    if "active|persist" in tm and "active|zero" in tm:
        pw, zw = tm["active|persist"]["wape"], tm["active|zero"]["wape"]
        if pw is None or zw is None:
            bad.append("WAPE not computed")
        elif pw >= zw:
            bad.append(f"persistence WAPE {pw:.3f} did not beat forecast-zero {zw:.3f} "
                       f"on autocorrelated synthetic money")
    bl = rep["turnover"]["bucket_level"].get("persist")
    if not bl or bl["n"] <= 0:
        bad.append("bucket-level totals missing")

    bel = rep["belief"]["models"]
    if "p_true" not in bel or not bel["p_true"]["n"]:
        bad.append("belief metrics missing for p_true")
    cal = rep["belief"]["calibration"].get("p_true", {})
    if not cal.get("bins"):
        bad.append("calibration has no populated bins")
    elif cal.get("money_ece") is None:
        bad.append("money ECE not computed")
    elif cal["money_ece"] > 0.15:
        bad.append(f"synthetic data should calibrate well, money ECE={cal['money_ece']:.3f}")

    gm = rep["gm"]["overall"]
    if not gm["turnover"]:
        bad.append("no turnover in GM section")
    elif gm["realized_margin"] is None or gm["expected_margin"] is None:
        bad.append("margins not computed")
    elif abs(gm["realized_margin"] - gm["expected_margin"]) > 0.05:
        bad.append(f"realised margin {gm['realized_margin']:.4f} far from expected "
                   f"{gm['expected_margin']:.4f} on synthetic data")

    if not rep["turnover"]["scatter"]:
        bad.append("scatter sample empty")
    if len(rep["daily"]) != 3:
        bad.append(f"daily series has {len(rep['daily'])} rows")
    if not rep.get("verdict"):
        bad.append("no verdict produced")

    out = Path(cfg.out_dir)
    html = out / "report.html"
    if not html.exists():
        bad.append("report.html not written")
    else:
        text = html.read_text(encoding="utf-8")
        for token in ("<svg", "Verdict", "Calibration", "gross margin"):
            if token not in text:
                bad.append(f"report.html missing '{token}'")
        if len(text) < 20_000:
            bad.append(f"report.html suspiciously small ({len(text)} bytes)")
    for name in ("report.json", "turnover_models.csv", "calibration.csv", "daily.csv"):
        if not (out / name).exists():
            bad.append(f"{name} not written")
    facts = sorted((out / "facts").glob("*.parquet")) if (out / "facts").exists() else []
    if len(facts) != 3:
        bad.append(f"expected 3 fact files, got {len(facts)}")
    elif facts:
        f = pd.read_parquet(facts[0])
        need = ["bucket", "pool_id", "line_id", "combination_id", "odds", "true_odds",
                "p_true", "turnover", "lag1", "f_persist", "realized_gm", "expected_gm",
                "clock_bin", "y_frac", "settled"]
        miss = [c for c in need if c not in f.columns]
        if miss:
            bad.append(f"facts parquet missing columns {miss}")
    return bad


if __name__ == "__main__":
    raise SystemExit(run_selftest())
