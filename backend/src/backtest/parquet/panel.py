"""Build the 5-minute selection-bucket panel: one row per
(bucket, pool_id, line_id, combination_id).

This is the fact table everything else is measured on. It carries, as of the
START of each bucket, what we knew (odds, true_odds, TG/SUP, clock, score) and
what then happened (turnover, dividend) inside that bucket.

Loading is done a month at a time and expansion a day at a time, all vectorised,
so 2.5 months finishes in minutes rather than the per-timestamp Python loop used
by "2 Features Extract Part 1.ipynb".
"""
from __future__ import annotations

from typing import Dict, Iterator, Optional, Tuple

import numpy as np
import pandas as pd

from . import beliefs
from .config import Config
from .loader import load_window

KEY = ["pool_id", "line_id", "combination_id"]

# Incident codes, from the comments in notebook 2 and src/core/config.py.
# Cancellations are ignored on purpose: "minutes since the last goal" is about
# what the market believed at the time, and a goal that is later chalked off was
# still a goal while the money was arriving.
GOAL_IN, GOAL_OUT = 27, 6
CORNER_IN = 13
YELLOW_IN = 78

# Event windows, in minutes since the incident. These are the cuts the desk
# asks for: money behaves differently right after a goal than in a quiet spell.
POST_GOAL_TIGHT = 5
POST_GOAL_WIDE = 15
POST_CORNER_TIGHT = 5

PREMATCH_BINS = [(np.inf, 360, "pre >6h"), (360, 120, "pre 6-2h"), (120, 60, "pre 2-1h"),
                 (60, 15, "pre 60-15m"), (15, -np.inf, "pre 15-0m")]
INPLAY_EDGES = [0, 15, 30, 45, 60, 75, 90]
ODDS_EDGES = [0, 1.5, 1.8, 2.2, 3.0, np.inf]
ODDS_LABELS = ["<1.50", "1.50-1.80", "1.80-2.20", "2.20-3.00", ">3.00"]


# ---------------------------------------------------------------- month cache
class MonthCache:
    """Raw rows for one calendar month plus the lookback needed to carry
    odds/params forward into the first buckets of the month."""

    def __init__(self, cfg: Config, m_start: pd.Timestamp, m_end: pd.Timestamp):
        step = pd.Timedelta(minutes=cfg.bucket_minutes)
        back = pd.Timedelta(days=cfg.lookback_days)
        self.cfg = cfg
        self.m_start, self.m_end = m_start, m_end

        self.odds = load_window(cfg.data_dir, "odds", m_start - back, m_end, pools=cfg.pools)
        # one bucket of extra history so the first bucket of the month has a lag
        self.inv = load_window(cfg.data_dir, "investments", m_start - step, m_end,
                               pools=cfg.pools if _inv_has_pool_name(cfg) else None)
        self.params = load_window(cfg.data_dir, "params", m_start - back, m_end)
        try:
            self.events = load_window(cfg.data_dir, "events", m_start - back, m_end)
        except Exception:
            self.events = pd.DataFrame()

        if not self.inv.empty and "pool_name" not in self.inv.columns:
            # derive pool_name from the odds table when investments lacks it
            names = self.odds[["pool_id", "pool_name"]].drop_duplicates("pool_id")
            self.inv = self.inv.merge(names, on="pool_id", how="left")
        if not self.inv.empty and cfg.pools:
            self.inv = self.inv[self.inv["pool_name"].isin(cfg.pools)]

        self.agg = aggregate_investments(self.inv, cfg.bucket_minutes)
        self.settle = build_settlement(self.inv)
        self.odds_bucket = bucket_last_odds(self.odds, cfg.bucket_minutes)
        self.params_bucket = bucket_last_params(self.params)
        self.event_marks = event_marks(self.events)
        self.keys = collect_keys(self.odds, self.inv)


def _inv_has_pool_name(cfg: Config) -> bool:
    return True  # filtering is re-applied after load; harmless if column absent


def aggregate_investments(inv: pd.DataFrame, bucket_minutes: int) -> pd.DataFrame:
    """1-minute investment rows -> per selection-bucket money."""
    if inv.empty:
        return pd.DataFrame(columns=KEY + ["bucket", "turnover", "dividend",
                                           "es_dividend", "tickets"])
    df = inv.copy()
    df["bucket"] = df["start_sell_time"].dt.floor(f"{bucket_minutes}min")
    for c in ("dividend", "es_dividend", "ticket_count"):
        if c not in df.columns:
            df[c] = np.nan
    out = (df.groupby(KEY + ["bucket"], dropna=True, observed=True)
             .agg(turnover=("turnover", "sum"),
                  dividend=("dividend", "sum"),
                  es_dividend=("es_dividend", "sum"),
                  tickets=("ticket_count", "sum"))
             .reset_index())
    return out


def build_settlement(inv: pd.DataFrame) -> pd.DataFrame:
    """Per selection outcome, derived from actual payout rather than re-deriving
    settlement rules from scores.

    y_frac = dividend / (turnover * odds_at_bet) in [0, 1]:
      1.0  -> selection won outright
      0.0  -> lost
      ~0.5 -> quarter-line half win / half loss, or a push refund
    """
    if inv.empty or "dividend" not in inv.columns:
        return pd.DataFrame(columns=KEY + ["y_frac", "settled", "turnover_key",
                                           "avg_odds"])
    df = inv.copy()
    df["stake_return"] = df["turnover"] * df.get("odds", np.nan)
    g = (df.groupby(KEY, dropna=True, observed=True)
           .agg(div_sum=("dividend", "sum"),
                ret_sum=("stake_return", "sum"),
                turnover_key=("turnover", "sum"))
           .reset_index())
    g["y_frac"] = np.where(g["ret_sum"] > 0, g["div_sum"] / g["ret_sum"], np.nan)
    g["y_frac"] = g["y_frac"].clip(0.0, 1.0)
    # Turnover-weighted odds actually struck on this selection. y_frac alone
    # cannot be read as a win or a push without it: a refunded push pays
    # 1/avg_odds of the potential return, not a fixed fraction.
    g["avg_odds"] = np.where(g["turnover_key"] > 0,
                             g["ret_sum"] / g["turnover_key"], np.nan)
    # a selection is settled once any payout information exists for its match
    g["settled"] = g["div_sum"].notna() & (g["ret_sum"] > 0)
    return g[KEY + ["y_frac", "settled", "turnover_key", "avg_odds"]]


def bucket_last_odds(odds: pd.DataFrame, bucket_minutes: int) -> pd.DataFrame:
    """Last posted odds (and true_odds) per selection per bucket."""
    if odds.empty:
        return pd.DataFrame(columns=KEY + ["bucket", "odds", "true_odds"])
    df = odds.dropna(subset=["effective_datetime", "odds"]).copy()
    df["bucket"] = df["effective_datetime"].dt.floor(f"{bucket_minutes}min")
    if "true_odds" not in df.columns:
        df["true_odds"] = np.nan
    df = df.sort_values("effective_datetime")
    out = (df.groupby(KEY + ["bucket"], observed=True)
             .agg(odds=("odds", "last"),
                  true_odds=("true_odds", "last"),
                  n_moves=("odds", "size"))
             .reset_index())
    return out


def bucket_last_params(params: pd.DataFrame) -> pd.DataFrame:
    if params.empty:
        return pd.DataFrame(columns=["match_id", "event_time"])
    cols = [c for c in ("match_id", "event_time", "tg", "sup", "ctg", "csup",
                        "game_state", "time_in_second", "goal_split_fh",
                        "corner_split_fh") if c in params.columns]
    return params[cols].dropna(subset=["event_time"]).sort_values("event_time")


def event_marks(events: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """Per match, the sorted times of each incident kind we cut money by.

    One frame per kind so a bucket can be joined against each with a single
    backward merge_asof, which is what turns "minutes since the last goal" into
    a vectorised lookup instead of a per-row scan of the incident table.
    """
    out: Dict[str, pd.DataFrame] = {}
    if events is None or events.empty:
        return out
    df = events.dropna(subset=["match_id", "incident_datetime", "incident_type"]).copy()
    if "provider_id" in df.columns and df["provider_id"].notna().any():
        # the feed carries several providers; provider 1 is the desk's primary
        primary = df[df["provider_id"] == 1]
        if not primary.empty:
            df = primary
    df["match_id"] = pd.to_numeric(df["match_id"], errors="coerce").fillna(-1).astype("int64")
    df["incident_type"] = pd.to_numeric(df["incident_type"], errors="coerce")
    for name, code in (("goal", GOAL_IN), ("corner", CORNER_IN), ("yellow", YELLOW_IN)):
        sub = df[df["incident_type"] == code][["match_id", "incident_datetime"]]
        if sub.empty:
            continue
        sub = sub.rename(columns={"incident_datetime": "at_{}".format(name)})
        out[name] = sub.sort_values("at_{}".format(name)).reset_index(drop=True)
    return out


def collect_keys(odds: pd.DataFrame, inv: pd.DataFrame) -> pd.DataFrame:
    """Every selection we might have to score, with its descriptive attributes."""
    frames = []
    ocols = ["match_id", "pool_id", "pool_name", "line_id", "line_label",
             "combination_id", "combination_string"]
    if not odds.empty:
        frames.append(odds[[c for c in ocols if c in odds.columns]])
    if not inv.empty:
        frames.append(inv[[c for c in ocols if c in inv.columns]])
    if not frames:
        return pd.DataFrame(columns=ocols)
    keys = pd.concat(frames, ignore_index=True)
    keys = keys.dropna(subset=KEY).drop_duplicates(KEY)
    return keys


# ------------------------------------------------------------------ day panel
def build_day(cfg: Config, day: pd.Timestamp, cache: MonthCache,
              pools_df: pd.DataFrame, matches_df: pd.DataFrame) -> pd.DataFrame:
    step = pd.Timedelta(minutes=cfg.bucket_minutes)
    day_start, day_end = day, day + pd.Timedelta(days=1)
    last_bucket = day_end - step

    keys = cache.keys
    if keys.empty:
        return pd.DataFrame()

    keys = keys.merge(pools_df, on="pool_id", how="left", suffixes=("", "_pool"))
    if not matches_df.empty:
        mcols = [c for c in ("match_id", "league_code", "league_name", "ko_time",
                             "home_name", "away_name", "is_void") if c in matches_df.columns]
        keys = keys.merge(matches_df[mcols], on="match_id", how="left")
    if "ko_time" not in keys.columns:
        keys["ko_time"] = pd.NaT

    span_lo, span_hi = _active_span(cfg, keys, step)
    lo = span_lo.clip(lower=day_start)
    hi = span_hi.clip(upper=last_bucket)
    lo = lo.dt.ceil(f"{cfg.bucket_minutes}min")
    hi = hi.dt.floor(f"{cfg.bucket_minutes}min")
    alive = lo.notna() & hi.notna() & (lo <= hi)

    index = _expand(keys.loc[alive, KEY], lo[alive], hi[alive], step)

    # never drop a bucket that actually took money, even outside the window
    money = cache.agg
    if not money.empty:
        m = money[(money["bucket"] >= day_start) & (money["bucket"] <= last_bucket)
                  & (money["turnover"] > 0)][KEY + ["bucket"]]
        m = m.merge(keys[KEY], on=KEY, how="inner")
        index = pd.concat([index, m], ignore_index=True)
    if index.empty:
        return pd.DataFrame()
    index = index.drop_duplicates(KEY + ["bucket"])

    facts = index.merge(keys.drop(columns=[c for c in ("pool_name_pool",) if c in keys.columns]),
                        on=KEY, how="left")
    facts = _attach_money(facts, money, step)
    facts = _attach_odds(facts, cache.odds_bucket)
    facts = _attach_params(facts, cache.params_bucket)
    facts = _attach_events(facts, cache.event_marks)
    if not cache.settle.empty:
        facts = facts.merge(cache.settle, on=KEY, how="left")
    else:
        facts["y_frac"] = np.nan
        facts["settled"] = False
        facts["avg_odds"] = np.nan
    facts = _derive(cfg, facts)
    return facts


def _active_span(cfg: Config, keys: pd.DataFrame, step: pd.Timedelta):
    sell_lo = keys.get("start_selling_time", pd.Series(pd.NaT, index=keys.index))
    close = keys.get("close_time", pd.Series(pd.NaT, index=keys.index))
    ko = keys["ko_time"]

    if cfg.full_span:
        lo = sell_lo
    else:
        window_lo = ko - pd.Timedelta(minutes=cfg.prematch_window_min)
        lo = pd.concat([sell_lo, window_lo], axis=1).max(axis=1)
        lo = lo.fillna(sell_lo).fillna(window_lo)
    # if the pool never reports a close time, assume it shuts 2h30 after kick-off
    hi = close.fillna(ko + pd.Timedelta(minutes=150))
    return pd.to_datetime(lo), pd.to_datetime(hi)


def _expand(keys: pd.DataFrame, lo: pd.Series, hi: pd.Series,
            step: pd.Timedelta) -> pd.DataFrame:
    """Cross each key with its own contiguous run of buckets."""
    if keys.empty:
        return pd.DataFrame(columns=list(keys.columns) + ["bucket"])
    lo_i = lo.values.astype("datetime64[ns]").astype("int64")
    hi_i = hi.values.astype("datetime64[ns]").astype("int64")
    step_i = np.int64(step.value)
    n = ((hi_i - lo_i) // step_i + 1).astype("int64")
    n = np.maximum(n, 0)
    total = int(n.sum())
    if total == 0:
        return pd.DataFrame(columns=list(keys.columns) + ["bucket"])
    row = np.repeat(np.arange(len(n)), n)
    starts = np.repeat(np.cumsum(n) - n, n)
    offset = np.arange(total) - starts
    bucket = lo_i[row] + offset * step_i
    out = keys.iloc[row].reset_index(drop=True)
    out["bucket"] = pd.to_datetime(bucket)
    return out


def _attach_money(facts: pd.DataFrame, money: pd.DataFrame,
                  step: pd.Timedelta) -> pd.DataFrame:
    cols = ["turnover", "dividend", "es_dividend", "tickets"]
    if money.empty:
        for c in cols:
            facts[c] = 0.0
        for k in (1, 2, 3):
            facts[f"lag{k}"] = 0.0
        facts["tickets_lag1"] = 0.0
        return facts

    facts = facts.merge(money, on=KEY + ["bucket"], how="left")
    facts[cols] = facts[cols].fillna(0.0)

    # lag k = turnover in the bucket k steps earlier, i.e. the "last 5 min"
    for k in (1, 2, 3):
        lag = money[KEY + ["bucket", "turnover"]].copy()
        lag["bucket"] = lag["bucket"] + k * step
        lag = lag.rename(columns={"turnover": f"lag{k}"})
        facts = facts.merge(lag, on=KEY + ["bucket"], how="left")
        facts[f"lag{k}"] = facts[f"lag{k}"].fillna(0.0)

    # the same shift on ticket count, so the naive ticket baseline exists too
    tick = money[KEY + ["bucket", "tickets"]].copy()
    tick["bucket"] = tick["bucket"] + step
    tick = tick.rename(columns={"tickets": "tickets_lag1"})
    facts = facts.merge(tick, on=KEY + ["bucket"], how="left")
    facts["tickets_lag1"] = pd.to_numeric(facts["tickets_lag1"],
                                          errors="coerce").fillna(0.0)
    return facts


def _asof_int_keys(df: pd.DataFrame) -> pd.DataFrame:
    for c in KEY:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(-1).astype("int64")
    return df


def _attach_odds(facts: pd.DataFrame, odds_bucket: pd.DataFrame) -> pd.DataFrame:
    """Carry the last known odds forward into every bucket."""
    if odds_bucket.empty:
        facts["odds"] = np.nan
        facts["true_odds"] = np.nan
        facts["odds_age_min"] = np.nan
        return facts
    left = _asof_int_keys(facts.copy()).sort_values("bucket")
    right = _asof_int_keys(odds_bucket.copy()).sort_values("bucket")
    right["odds_bucket_at"] = right["bucket"]
    merged = pd.merge_asof(left, right[KEY + ["bucket", "odds", "true_odds", "odds_bucket_at"]],
                           on="bucket", by=KEY, direction="backward")
    merged["odds_age_min"] = (merged["bucket"] - merged["odds_bucket_at"]).dt.total_seconds() / 60
    return merged.drop(columns=["odds_bucket_at"])


def _attach_events(facts: pd.DataFrame, marks: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Time of the last goal / corner / card before each bucket."""
    for name in ("goal", "corner", "yellow"):
        col = "at_{}".format(name)
        if name not in marks:
            facts[col] = pd.NaT
            continue
        left = facts.sort_values("bucket").copy()
        left["match_id"] = pd.to_numeric(left["match_id"], errors="coerce") \
            .fillna(-1).astype("int64")
        facts = pd.merge_asof(left, marks[name], left_on="bucket", right_on=col,
                             by="match_id", direction="backward")
    return facts


def _attach_params(facts: pd.DataFrame, params_bucket: pd.DataFrame) -> pd.DataFrame:
    if params_bucket.empty:
        for c in ("tg", "sup", "ctg", "csup", "game_state", "time_in_second"):
            facts[c] = np.nan
        return facts
    left = facts.sort_values("bucket").copy()
    left["match_id"] = pd.to_numeric(left["match_id"], errors="coerce").fillna(-1).astype("int64")
    right = params_bucket.copy()
    right["match_id"] = pd.to_numeric(right["match_id"], errors="coerce").fillna(-1).astype("int64")
    right = right.sort_values("event_time")
    merged = pd.merge_asof(left, right, left_on="bucket", right_on="event_time",
                           by="match_id", direction="backward")
    return merged


def _derive(cfg: Config, f: pd.DataFrame) -> pd.DataFrame:
    eps = 1e-12
    f = f[f["odds"].notna() & (f["odds"] > 1.0)].copy()
    if f.empty:
        return f

    # ---- clock ---------------------------------------------------------
    f["mins_to_ko"] = (f["ko_time"] - f["bucket"]).dt.total_seconds() / 60
    if "time_in_second" in f.columns:
        f["match_minute"] = f["time_in_second"] / 60.0
    else:
        f["match_minute"] = np.nan
    state = f.get("game_state", pd.Series(pd.NA, index=f.index)).astype("string")
    f["is_inplay"] = (state.notna() & (state != "Prematch")) | (f["mins_to_ko"] < 0)
    f["clock_bin"] = _clock_bin(f)
    f["odds_bin"] = pd.cut(f["odds"], ODDS_EDGES, labels=ODDS_LABELS, right=False).astype("string")

    # ---- where we sit relative to the last incident --------------------
    f = _derive_events(f)

    # ---- belief: one column per independent source ----------------------
    f = beliefs.attach(cfg, f)

    # ---- belief: p_true, the single headline number ---------------------
    f["p_true_raw"], f["p_source"] = _true_prob(cfg, f)
    line_grp = ["pool_id", "line_id", "bucket"]
    f["book_sum"] = f.groupby(line_grp, observed=True)["p_true_raw"].transform("sum")
    f["sell_book_sum"] = f.groupby(line_grp, observed=True)["odds"].transform(
        lambda s: (1.0 / s).sum())
    if cfg.normalize_book:
        ok = f["book_sum"] > eps
        f["p_true"] = np.where(ok, f["p_true_raw"] / f["book_sum"].where(ok, 1.0), np.nan)
    else:
        f["p_true"] = f["p_true_raw"]
    f["p_sell"] = 1.0 / f["odds"]
    f["edge"] = f["p_sell"] - f["p_true"]          # margin per unit stake
    f["hkjc_margin"] = f["sell_book_sum"] - 1.0

    # ---- forecasts of next-bucket turnover -----------------------------
    f["f_persist"] = f["lag1"]
    f["f_ma3"] = f[["lag1", "lag2", "lag3"]].mean(axis=1)
    f["f_ema"] = 0.5 * f["lag1"] + 0.3 * f["lag2"] + 0.2 * f["lag3"]
    f["f_zero"] = 0.0
    # not a forecast: the realised amount, kept as the ceiling any forecast is
    # measured against. It is only honest as an upper bound.
    f["f_oracle"] = f["turnover"]

    # ---- money outcome -------------------------------------------------
    f["realized_gm"] = f["turnover"] - f["dividend"]
    f["expected_gm"] = f["turnover"] * (1.0 - f["odds"] * f["p_true"])
    f["es_gm"] = f["turnover"] - f["es_dividend"]
    f["day"] = f["bucket"].dt.floor("D")
    return f


def _derive_events(f: pd.DataFrame) -> pd.DataFrame:
    """Minutes since the last incident, and the window that puts a row in.

    Prematch rows are their own window: nothing has happened yet, so calling
    them "quiet" would mix them in with a settled 70th minute.
    """
    for name in ("goal", "corner", "yellow"):
        col = "at_{}".format(name)
        at = f[col] if col in f.columns else pd.Series(pd.NaT, index=f.index)
        age = (f["bucket"] - pd.to_datetime(at)).dt.total_seconds() / 60.0
        f["minutes_since_{}".format(name)] = age.where(age >= 0)

    sg = f["minutes_since_goal"]
    sc = f["minutes_since_corner"]
    window = pd.Series("quiet", index=f.index, dtype="object")
    window[(sc.notna()) & (sc <= POST_CORNER_TIGHT)] = "post_corner_5"
    window[(sg.notna()) & (sg <= POST_GOAL_WIDE)] = "post_goal_15"
    window[(sg.notna()) & (sg <= POST_GOAL_TIGHT)] = "post_goal_5"
    window[~f["is_inplay"].astype(bool)] = "prematch"
    f["event_window"] = window.astype("string")

    kind = pd.Series("none", index=f.index, dtype="object")
    ages = pd.DataFrame({"goal": sg, "corner": sc,
                         "yellow": f["minutes_since_yellow"]})
    has_any = ages.notna().any(axis=1)
    if has_any.any():
        kind[has_any] = ages[has_any].idxmin(axis=1)
    kind[~f["is_inplay"].astype(bool)] = "none"
    f["event_kind"] = kind.astype("string")
    return f


def _clock_bin(f: pd.DataFrame) -> pd.Series:
    out = pd.Series(pd.NA, index=f.index, dtype="string")
    pre = ~f["is_inplay"]
    mk = f["mins_to_ko"]
    for hi, lo, label in PREMATCH_BINS:
        sel = pre & (mk <= hi) & (mk > lo)
        out[sel] = label
    minute = f["match_minute"]
    live = f["is_inplay"]
    for i in range(len(INPLAY_EDGES) - 1):
        a, b = INPLAY_EDGES[i], INPLAY_EDGES[i + 1]
        out[live & (minute >= a) & (minute < b)] = f"live {a}-{b}m"
    out[live & (minute >= 90)] = "live 90m+"
    out[live & minute.isna()] = "live (no clock)"
    return out.fillna("unknown")


def _true_prob(cfg: Config, f: pd.DataFrame):
    """p_true with the documented precedence, plus a per-row provenance tag."""
    n = len(f)
    p = pd.Series(np.nan, index=f.index, dtype="float64")
    src = pd.Series("none", index=f.index, dtype="object")
    want = cfg.true_prob_source

    if want in ("auto", "true_odds") and "true_odds" in f.columns:
        ok = f["true_odds"].notna() & (f["true_odds"] > 1.0)
        p[ok] = 1.0 / f.loc[ok, "true_odds"]
        src[ok] = "true_odds"

    if want in ("auto", "poisson"):
        todo = p.isna()
        if todo.any():
            pp = _poisson_prob(f.loc[todo])
            got = pp.notna()
            p[todo & got.reindex(p.index, fill_value=False)] = pp[got]
            src[todo & got.reindex(src.index, fill_value=False)] = "poisson"

    todo = p.isna()
    if todo.any() and want in ("auto", "demargin"):
        margin = f.loc[todo, "pool_name"].map(cfg.margin_for).astype("float64")
        margin = margin.fillna(cfg.margin_for("_default"))
        p[todo] = (1.0 / f.loc[todo, "odds"]) / (1.0 + margin)
        src[todo] = "demargin"
    return p, src


def _text(value) -> str:
    """Series cells can be pd.NA. ``value or ''`` then raises on that NA."""
    if value is None or pd.isna(value):
        return ""
    return str(value)


def _num(value, default: float = 0.0) -> float:
    if value is None or pd.isna(value):
        return default
    return float(value)


def _poisson_prob(f: pd.DataFrame) -> pd.Series:
    """Fair probability from HKJC's own TG/SUP, using the desk's poisson_betting
    module. Returns NaN when it is unavailable."""
    out = pd.Series(np.nan, index=f.index, dtype="float64")
    try:
        from . import poisson_betting as pb
    except Exception:
        return out
    if "tg" not in f.columns:
        return out
    sub = f[f["tg"].notna() & f["line_label"].notna()]
    if sub.empty:
        return out
    cache: Dict[tuple, dict] = {}
    for idx, r in sub.iterrows():
        pool = _text(r.get("pool_name"))
        line = _parse_line(r.get("line_label"))
        if line is None:
            continue
        corner = pool.startswith("C") or pool.startswith("FC") or pool.startswith("ETC")
        tg = float(r["ctg"]) if corner and pd.notna(r.get("ctg")) else float(r["tg"])
        sup = float(r["csup"]) if corner and pd.notna(r.get("csup")) else _num(r.get("sup"))
        hilo = "LO" in pool or "HILO" in pool
        key = (round(tg, 3), round(sup, 3), round(line, 3), hilo)
        if key not in cache:
            try:
                cache[key] = (pb.calculate_over_under_odds(tg, sup, line) if hilo
                              else pb.calculate_asian_handicap_odds(tg, sup, -line))
            except Exception:
                cache[key] = {}
        fair = cache[key]
        odd = _pick_side(fair, _text(r.get("combination_string")),
                         int(_num(r.get("combination_id"))), hilo)
        if odd and odd > 1.0:
            out.at[idx] = 1.0 / odd
    return out


def _parse_line(label) -> Optional[float]:
    """'2.5', '2/2.5', '-1.5/-2' -> float (quarter lines averaged)."""
    if label is None or pd.isna(label):
        return None
    s = str(label).strip().replace(" ", "")
    if not s:
        return None
    try:
        if "/" in s:
            return float(np.mean([float(x) for x in s.split("/") if x]))
        return float(s)
    except ValueError:
        return None


def _pick_side(fair: dict, comb_string: str, comb_id: int, hilo: bool) -> Optional[float]:
    if not fair:
        return None
    cs = comb_string.upper()
    if hilo:
        want = ["Over", "High"] if cs.startswith("H") or comb_id == 1 else ["Under", "Low"]
    else:
        want = ["Home"] if cs.startswith("H") or comb_id == 1 else ["Away"]
    for k in want:
        if k in fair and fair[k]:
            return float(fair[k])
    return None


# ------------------------------------------------------------------- driver
def iter_days(cfg: Config, pools_df: pd.DataFrame,
              matches_df: pd.DataFrame) -> Iterator[Tuple[pd.Timestamp, pd.DataFrame]]:
    start = pd.Timestamp(cfg.start_date)
    end = pd.Timestamp(cfg.end_date) + pd.Timedelta(days=1)
    months = pd.date_range(start.replace(day=1), end, freq="MS")
    for m_start in months:
        m_end = min(m_start + pd.offsets.MonthBegin(1), end)
        if m_end <= start:
            continue
        print(f"[load] {m_start:%Y-%m} ...", flush=True)
        cache = MonthCache(cfg, max(m_start, start), m_end)
        print(f"[load] odds={len(cache.odds):,} inv={len(cache.inv):,} "
              f"params={len(cache.params):,} keys={len(cache.keys):,}", flush=True)
        for day in pd.date_range(max(m_start, start), m_end - pd.Timedelta(days=1), freq="D"):
            facts = build_day(cfg, day, cache, pools_df, matches_df)
            yield day, facts
        del cache
