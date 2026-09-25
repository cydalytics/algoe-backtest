"""
Algo E MVP Pipeline Configuration

Central configuration for the offline pipeline: data source, HKJC SQL
connection strings, MVP pool scope, pricing margins and optimizer search
bounds. All names are UPPERCASE by project convention so they stand out
in the code.

The whole project must run with no internet access. Every source below is
either the HKJC internal SQL Server, a parquet snapshot captured from it,
or a local simulator.

Change Log:
-----------
2026-08-18      Initialize
2026-08-30      Rewrite for the offline SQL pipeline (hkjc_code port)
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
APP_DIR = Path(__file__).resolve().parent.parent              # backend/src
BACKEND_DIR = APP_DIR.parent                                  # backend
WORKSPACE_DIR = BACKEND_DIR.parent                            # fortnight folder
REPO_ROOT_DIR = WORKSPACE_DIR.parent.parent                   # AlgoE repo root
CAPTURE_DIR = Path(
    os.environ.get("ALGOE_CAPTURE_DIR", str(WORKSPACE_DIR / "captures"))
)

# ---------------------------------------------------------------------------
# SQLite output store
# ---------------------------------------------------------------------------
DB_PATH = Path(os.environ.get("ALGOE_DB", str(WORKSPACE_DIR / "algoe.db")))

# ---------------------------------------------------------------------------
# Data source
# ---------------------------------------------------------------------------
# sql     - live HKJC SQL Server (the office / offline environment)
# capture - parquet snapshot previously dumped by scripts/capture.py
# sim     - coherent local simulator, same frames as sql (default for dev)
DATA_SOURCE = os.environ.get("ALGOE_SOURCE", "sim")

# pyodbc DSNs, straight from "5 Data Extraction - Real Time.ipynb".
SQL_CONN_QFM = os.environ.get(
    "ALGOE_CONN_QFM",
    "DRIVER={SQL Server};SERVER=QFMWDB1ST12,40000;DATABASE=qfm_outbound_db",
)
SQL_CONN_BIS = os.environ.get(
    "ALGOE_CONN_BIS",
    "DRIVER={SQL Server};SERVER=QFMBDB2LSR3A,40000;DATABASE=bis1_fbuser_db",
)
SQL_CONN_BIS2 = os.environ.get(
    "ALGOE_CONN_BIS2",
    "DRIVER={SQL Server};SERVER=QFMBDB2LSR3A,40000;DATABASE=QFM",
)

# Simulator determinism: same seed -> same book, so screenshots are stable.
SIM_SEED = int(os.environ.get("ALGOE_SIM_SEED", "20260901"))
SIM_MATCHES = int(os.environ.get("ALGOE_SIM_MATCHES", "12"))

# ---------------------------------------------------------------------------
# Tick cadence
# ---------------------------------------------------------------------------
BUCKET_MINUTES = 5
# Trailing turnover windows the preprocessor materialises, in minutes back
# from as-of. Window 0 (0-5m) is the MVP turnover forecast.
TURNOVER_WINDOWS = [5, 10, 15, 20, 25, 30]

# ---------------------------------------------------------------------------
# Pool scope
# ---------------------------------------------------------------------------
# Pools the optimizer actively searches theta for. Everything else the
# feed returns is still priced and shown, but flagged as carried.
OPTIMIZED_POOLS = [
    "HAD", "FHAD", "HHAD",
    "HDC", "FHDC",
    "HILO", "FHLO",
    "OOE", "TTG",
    "CRS", "FCRS", "HFT",
    "CHLO", "CHDC",
]

# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------
# Baseline margin per pool, from optimizer_v2.get_margin().
POOL_MARGIN = {
    "HAD": 0.13, "FHAD": 0.13, "HHAD": 0.13,
    "HDC": 0.08, "FHDC": 0.08,
    "HILO": 0.08, "FHLO": 0.08,
    "OOE": 0.08,
    "TTG": 0.30, "HFT": 0.30,
    "CRS": 0.50, "FCRS": 0.50,
    "CHLO": 0.08, "CHDC": 0.08,
}
DEFAULT_MARGIN = 0.15

# Score grid depth. Corners run hotter than goals so they need more room.
MAX_GOALS = 10
MAX_CORNERS = 25

# HKJC applies a draw inflation on the goal grid; 1.0 disables it.
DRAW_FACTOR = float(os.environ.get("ALGOE_DRAW_FACTOR", "1.0"))

# Fallback first-half share when algo_param carries no split.
DEFAULT_FH_SPLIT = 0.45

ODDS_FLOOR = 1.01
ODDS_CAP = 999.0
ODDS_ROUND = 0.01

# Anything longer than this is taken off the board rather than quoted, so
# the risk grid cannot show a liability nobody would ever actually write.
MAX_SELLABLE_ODDS = 500.0

# ---------------------------------------------------------------------------
# Theta semantics
# ---------------------------------------------------------------------------
# remaining   - Goal90*TG/SUP describe goals still to come; the score grid is
#               shifted by the current score before a line is resolved.
# full_match  - the optimizer_v2 reading: TG/SUP already describe the
#               finished match, no score shift.
# Pre-match the two are identical. In-play they diverge, so this is exposed
# in the API and shown on the cockpit's model tab.
THETA_SEMANTICS = os.environ.get("ALGOE_THETA_SEMANTICS", "remaining")

# Which algo_param layer feeds the belief and the starting point.
THETA_LAYER = os.environ.get("ALGOE_THETA_LAYER", "Current")   # Basis|Model|Current

# ---------------------------------------------------------------------------
# MVP simplifications (explicit, so the UI can label them)
# ---------------------------------------------------------------------------
# 1. TrueProb = 1 / HKJC true odds (pool_odds_true_change_h).
# 2. Next 5-minute turnover = trailing 5-minute turnover.
# hkjc_true | hkjc_offer | market | model - the first rung of the fallback
# chain; the others are still tried in order when a row has no price.
TRUE_PROB_SOURCE = os.environ.get("ALGOE_TRUE_PROB", "hkjc_true")

# The MVP rule takes 1/true_odds as-is. Rescaling each line onto 1.0 is
# more coherent but hides a feed that does not add up, so it is off by
# default and the raw book sum is reported instead.
TRUE_PROB_NORMALIZE = os.environ.get("ALGOE_TRUE_PROB_NORM", "0") == "1"
BOOK_SUM_TOLERANCE = 0.06

TURNOVER_MODEL = os.environ.get("ALGOE_TURNOVER_MODEL", "persistence")
# Nominal stake assumed on a selection nobody has bet yet, so a fresh
# pre-match card still carries relative weight in the objective.
TURNOVER_FLOOR = float(os.environ.get("ALGOE_TURNOVER_FLOOR", "100.0"))

# ---------------------------------------------------------------------------
# Optimizer (port of optimizer_v2.py, widened to 8 dimensions)
# ---------------------------------------------------------------------------
OPTIMIZER_VERSION = os.environ.get("ALGOE_OPTIMIZER", "slsqp_multistart")

# Absolute solver bounds per dimension.
THETA_BOUNDS = {
    "goal_tg": (0.05, 9.0),
    "goal_sup": (-5.0, 5.0),
    "goal_fh_tg": (0.02, 5.0),
    "goal_fh_sup": (-3.0, 3.0),
    "corner_tg": (0.5, 25.0),
    "corner_sup": (-10.0, 10.0),
    "corner_fh_tg": (0.2, 14.0),
    "corner_fh_sup": (-6.0, 6.0),
}

# Safety band: how far theta* may travel from the current parameter in one
# tick, per dimension. Traders reject anything that jumps further.
THETA_MAX_MOVE = {
    "goal_tg": 0.60,
    "goal_sup": 0.50,
    "goal_fh_tg": 0.35,
    "goal_fh_sup": 0.30,
    "corner_tg": 2.00,
    "corner_sup": 1.50,
    "corner_fh_tg": 1.20,
    "corner_fh_sup": 0.90,
}

# Constrain our offer to stay at or under the best external price on the
# Asian pools, exactly as optimizer_v2 does for HDC and HILO.
MARKET_CAP_POOLS = ["HDC", "HILO", "CHDC", "CHLO"]
# Which external statistic is the ceiling: max never beats the most
# generous competitor, avg never beats the consensus.
MARKET_CAP_STAT = os.environ.get("ALGOE_MARKET_CAP_STAT", "max")   # max|avg|min
OPTIMIZER_MAX_ITER = int(os.environ.get("ALGOE_OPT_MAXITER", "60"))
OPTIMIZER_WORKERS = int(os.environ.get("ALGOE_OPT_WORKERS", "5"))
# How many of the multi-start guesses to actually run. The desk uses all
# five; a backtest that only needs a recommendation, not the best of five,
# can drop this to 1.
OPTIMIZER_STARTS = int(os.environ.get("ALGOE_OPT_STARTS", "5"))
# Finite-difference probe, in theta units. Expectancies move in hundredths
# of a goal, so the solver has to probe on that scale to see a gradient at
# all; scipy's 1e-8 default is below the noise floor of the objective.
OPTIMIZER_STEP = float(os.environ.get("ALGOE_OPT_STEP", "1e-3"))
# Payout is measured in dollars of turnover, so a dollar is a sensible
# place to stop rather than the default 1e-6.
OPTIMIZER_FTOL = float(os.environ.get("ALGOE_OPT_FTOL", "1.0"))

# Points sampled per dimension for the E[GM] response curves the cockpit
# draws. Odd so the current value lands on a sample.
GM_CURVE_POINTS = 13

# Cap matches per tick in demo runs.
MAX_MATCHES_QUICK = 6

# Stop starting new matches once this much wall clock has gone on one tick.
# 0 means solve the whole card however long it takes. The board does not
# wait for this either way - results are published as they land - so the
# budget only decides how much of a very large card gets a recommendation
# before the next tick replaces it.
OPTIMIZER_BUDGET_SECONDS = float(os.environ.get("ALGOE_OPT_BUDGET", "240"))

# Solve in the background and publish per match, rather than making the
# first request wait for the whole card. Turn off to get the old
# behaviour, where a tick is not served until every match is solved.
OPTIMIZE_ASYNC = os.environ.get("ALGOE_OPT_ASYNC", "1") == "1"

# ---------------------------------------------------------------------------
# Board scope
# ---------------------------------------------------------------------------
# The notebook's active-pool query is "has started selling", with no
# stop-sell condition, so it keeps returning pools for matches that ended
# hours ago. A desk only wants what it can still trade, so a match is
# dropped once the feed publishes a full-time result, voids it, or simply
# stops moving its prices.
SHOW_FINISHED = os.environ.get("ALGOE_SHOW_FINISHED", "0") == "1"
# No price on the whole match has moved in this long: nobody is trading it.
DEAD_ODDS_MINUTES = float(os.environ.get("ALGOE_DEAD_ODDS_MIN", "30"))

# ---------------------------------------------------------------------------
# Incident type codes (event_level2_incident_h.incident_type)
# ---------------------------------------------------------------------------
INCIDENT_GOAL = 27
INCIDENT_GOAL_CANCEL = 6
INCIDENT_CORNER = 13
INCIDENT_CORNER_CANCEL = 5
INCIDENT_PENALTY = 42
INCIDENT_PENALTY_CANCEL = 7
INCIDENT_RED = 57
INCIDENT_RED_CANCEL = 8
INCIDENT_YELLOW_RED = 79
INCIDENT_YELLOW_RED_CANCEL = 10
INCIDENT_YELLOW = 78
INCIDENT_YELLOW_CANCEL = 9

# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
API_PORT = int(os.environ.get("ALGOE_API_PORT", "8001"))
API_CORS_ORIGINS = os.environ.get("ALGOE_CORS", "*").split(",")

# How long a tick is served before the next request rebuilds it. Matches
# the bucket, so the board never shows a book from a previous window.
TICK_TTL_SECONDS = int(os.environ.get("ALGOE_TICK_TTL", str(BUCKET_MINUTES * 60)))

# ---------------------------------------------------------------------------
# Triggers (the live loop's "is this worth rerunning" test)
# ---------------------------------------------------------------------------
TRIGGER_ODDS_MOVE_THRESHOLD = float(os.environ.get("ALGOE_TRIGGER_ODDS", "0.05"))
TRIGGER_THETA_MOVE_THRESHOLD = float(os.environ.get("ALGOE_TRIGGER_THETA", "0.10"))

# ---------------------------------------------------------------------------
# Backtest (W06)
# ---------------------------------------------------------------------------
# Predictors the runner will A/B unless the CLI/API overrides them.
# Persistence is the locked MVP baseline; the rest are the first alternatives
# the turnover / true-odds pillars have to beat.
BACKTEST_TURNOVER_MODELS = [
    s.strip() for s in os.environ.get(
        "ALGOE_BT_TURNOVER",
        "persistence,trailing_mean,gametime,blend,momentum,match_share,oracle",
    ).split(",") if s.strip()
]
BACKTEST_TRUE_PROB_SOURCES = [
    s.strip() for s in os.environ.get(
        "ALGOE_BT_TRUEPROB",
        "hkjc_true,hkjc_offer,market,model",
    ).split(",") if s.strip()
]
BACKTEST_CALIBRATORS = [
    s.strip() for s in os.environ.get(
        "ALGOE_BT_CAL",
        "raw,shrink,temperature,isotonic,normalize",
    ).split(",") if s.strip()
]
BACKTEST_ALGOS = [
    s.strip() for s in os.environ.get(
        "ALGOE_BT_ALGOS",
        "hold,slsqp,coordinate,grid,tight",
    ).split(",") if s.strip()
]
BACKTEST_POLICIES = [
    s.strip() for s in os.environ.get(
        "ALGOE_BT_POLICIES",
        "always,threshold,bps",
    ).split(",") if s.strip()
]
BACKTEST_ELASTICITIES = [
    float(s) for s in os.environ.get("ALGOE_BT_ELASTICITY", "0,1.0").split(",")
    if s.strip()
]
BACKTEST_MEETING_START = os.environ.get("ALGOE_BT_START", "2026-09-11T19:00:00")
BACKTEST_TICKS = int(os.environ.get("ALGOE_BT_TICKS", "12"))
BACKTEST_MATCHES = int(os.environ.get("ALGOE_BT_MATCHES", "8"))
# Blend = this weight on persistence, the rest on the gametime profile.
BACKTEST_BLEND_PERSIST = float(os.environ.get("ALGOE_BT_BLEND", "0.65"))
# Calibration bins for the true-odds pack.
BACKTEST_CAL_BINS = int(os.environ.get("ALGOE_BT_CAL_BINS", "10"))
BACKTEST_PLOT_POINTS = int(os.environ.get("ALGOE_BT_PLOT_N", "80"))
# Apply theta* only if the tick/match E[lift] clears this (policy=threshold).
BACKTEST_LIFT_THRESHOLD = float(os.environ.get("ALGOE_BT_LIFT", "500"))
# Same idea in basis points of forecast turnover (policy=bps).
BACKTEST_LIFT_BPS = float(os.environ.get("ALGOE_BT_LIFT_BPS", "20"))

# Price elasticity of next-bucket demand. Zero is the live-desk MVP
# (turnover does not move when we reprice). A positive number means a
# longer price attracts more money: t' = t * (odds'/odds)^(-e).
# The backtest is the only caller that should set this above zero.
DEMAND_ELASTICITY = float(os.environ.get("ALGOE_DEMAND_E", "0"))
