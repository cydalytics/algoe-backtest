"""
Predictor Registry

Names the live models already understand, plus the backtest-only
forecasts (oracle, momentum, match_share, phase_scale) and the
calibrator / algo / policy catalogues.

A winner on a live-compatible name can be promoted by changing a
config value. Oracle is a ceiling, not a candidate for promotion.

Change Log:
-----------
2026-09-11      Initialize (W06 backtest)
2026-09-11      Labs: extra turnover models + calibrator/algo registries
"""

from src.core import config
from src.models.true_prob import TrueProbModel
from src.models.turnover import TurnoverModel

from .algos import ALGO_LABELS, ALGOS
from .calibrators import CALIBRATOR_LABELS, CALIBRATORS

TURNOVER_MODELS = (
    "persistence", "naive_lag5", "trailing_mean",
    "gametime", "blend", "momentum", "match_share",
    "phase_scale", "oracle", "zero",
)

TRUE_PROB_SOURCES = (
    "hkjc_true", "hkjc_offer", "market", "model", "model_basis", "model_model",
)

# Display names only. The parquet path (src/backtest/parquet) writes a few ids
# the live models do not implement, so they are named here but deliberately not
# added to TURNOVER_MODELS / TRUE_PROB_SOURCES above: those tuples gate what a
# labs sweep is allowed to construct.
TURNOVER_LABELS = {
    "persistence": "Last 5 minutes (MVP)",
    "naive_lag5": "Last 5 minutes (alias)",
    "trailing_mean": "Mean of trailing buckets",
    "gametime": "Walk-forward phase × pool profile",
    "blend": "Blend of persistence and gametime",
    "momentum": "Last bucket × trailing trend",
    "match_share": "Match persist, profiled pool mix",
    "phase_scale": "Persist scaled to phase profile",
    "oracle": "Next-bucket actual (ceiling)",
    "zero": "Always zero (sanity)",
    "ema": "EMA of last 3 buckets",
    "hurdle": "CPU hurdle, walk-forward",
}

TRUE_PROB_LABELS = {
    "hkjc_true": "1 / HKJC true odds (MVP)",
    "hkjc_offer": "De-margined HKJC offer",
    "market": "De-margined market consensus",
    "model": "Poisson grid at Current theta",
    "model_basis": "Poisson grid at Basis theta",
    "model_model": "Poisson grid at Model theta",
    "poisson": "Poisson on HKJC TG/SUP",
    "demargin": "Public odds, margin removed",
}

POLICY_LABELS = {
    "always": "Always apply theta*",
    "threshold": "Apply if E[lift] ≥ threshold",
    "bps": "Apply if E[lift] ≥ bps cut",
}


def parse_list(text, default):
    if not text:
        return list(default)
    return [s.strip() for s in str(text).split(",") if s.strip()]


def parse_floats(text, default):
    if not text:
        return list(default)
    return [float(s.strip()) for s in str(text).split(",") if s.strip()]


def default_turnover():
    return list(config.BACKTEST_TURNOVER_MODELS)


def default_true_prob():
    return list(config.BACKTEST_TRUE_PROB_SOURCES)


def default_calibrators():
    return list(getattr(config, "BACKTEST_CALIBRATORS", ["raw"]))


def default_algos():
    return list(getattr(config, "BACKTEST_ALGOS", ["hold", "slsqp"]))


def default_policies():
    return list(getattr(config, "BACKTEST_POLICIES", ["always"]))


def default_elasticities():
    return list(getattr(config, "BACKTEST_ELASTICITIES", [0.0]))


def make_turnover(name) -> TurnoverModel:
    if name not in TURNOVER_MODELS:
        raise ValueError("unknown turnover predictor '{}'".format(name))
    return TurnoverModel(model=name)


def make_true_prob(name) -> TrueProbModel:
    if name not in TRUE_PROB_SOURCES:
        raise ValueError("unknown true-odds predictor '{}'".format(name))
    layer = config.THETA_LAYER
    source = name
    if name == "model_basis":
        source, layer = "model", "Basis"
    elif name == "model_model":
        source, layer = "model", "Model"
    return TrueProbModel(source=source, fallback=False, layer=layer)


def walk_forward_profile(history_frames):
    """Mean next-bucket turnover by (phase, pool) from already-seen ticks.

    Using ``t_actual`` (not ``t5m``) is the point: the profile is a real
    forecast of the coming bucket, fit only on information that would have
    been known after those earlier ticks settled.
    """
    counts = {}
    totals = {}
    for frame in history_frames:
        sel = frame.selections
        if sel.empty or "t_actual" not in sel.columns:
            continue
        for rec in sel[["phase", "pool_code", "t_actual"]].itertuples(index=False):
            key = (str(rec.phase), str(rec.pool_code))
            totals[key] = totals.get(key, 0.0) + float(rec.t_actual or 0.0)
            counts[key] = counts.get(key, 0) + 1
    return {k: totals[k] / counts[k] for k in totals if counts[k] > 0}


def catalog():
    return {
        "turnover": {k: TURNOVER_LABELS[k] for k in TURNOVER_MODELS},
        "true_prob": {k: TRUE_PROB_LABELS[k] for k in TRUE_PROB_SOURCES},
        "calibrators": dict(CALIBRATOR_LABELS),
        "algos": dict(ALGO_LABELS),
        "policies": dict(POLICY_LABELS),
        "elasticities": default_elasticities(),
    }
