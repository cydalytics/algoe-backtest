"""
Preprocessing

Turns the seven raw frames of a Snapshot into the shape the rest of the
tick works in: one row per tradable selection, plus per-match state, theta,
competitor quotes and an incident tape.

Three things happen here that the notebook does inline and this pulls out:

* Score reconciliation. Two providers report the same match and disagree
  during a VAR check, so goals and corners are netted per provider (a
  cancel undoes a goal) and the highest count wins, exactly as cell 11 does.
* Half-time detection. algo_param has no half-time GameState - the break is
  FirstHalf with the clock parked on 45:00 - so it is inferred here once
  and every downstream consumer reads MatchState.ht_done.
* Turnover windows. The six trailing 5-minute buckets are pivoted onto the
  selection row, which is what makes the MVP forecast a column copy.

Change Log:
-----------
2026-08-18      Initialize
2026-08-30      Rewrite against the HKJC snapshot frames
"""

import math
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from src.core import config
from src.core.exceptions import FeatureEngineeringError
from src.core.logging import get_logger
from src.hkjc.snapshot import Snapshot
from src.pricing import pools as pool_defs
from src.pricing.book import MatchState

log = get_logger(__name__)

GAME_STATE_MAP = {
    "Prematch": "prematch",
    "FirstHalf": "first_half",
    "SecondHalf": "second_half",
    "XtraTimeFirstHalf": "et_first_half",
    "XtraTimeSecondHalf": "et_second_half",
    "PenaltyShootOut": "penalty_shootout",
}

GOAL_IN, GOAL_OUT = [config.INCIDENT_GOAL], [config.INCIDENT_GOAL_CANCEL]
CORNER_IN, CORNER_OUT = [config.INCIDENT_CORNER], [config.INCIDENT_CORNER_CANCEL]
RED_IN = [config.INCIDENT_RED, config.INCIDENT_YELLOW_RED]
RED_OUT = [config.INCIDENT_RED_CANCEL, config.INCIDENT_YELLOW_RED_CANCEL]
YELLOW_IN, YELLOW_OUT = [config.INCIDENT_YELLOW], [config.INCIDENT_YELLOW_CANCEL]

INCIDENT_LABEL = {
    config.INCIDENT_GOAL: "GOAL",
    config.INCIDENT_CORNER: "CORNER",
    config.INCIDENT_RED: "RED",
    config.INCIDENT_YELLOW_RED: "RED",
    config.INCIDENT_YELLOW: "YELLOW",
    config.INCIDENT_PENALTY: "PEN",
    config.INCIDENT_GOAL_CANCEL: "GOAL CANCELLED",
    config.INCIDENT_CORNER_CANCEL: "CORNER CANCELLED",
}


@dataclass
class MatchContext:
    """Per-match state, theta and metadata for one tick."""

    match_id: int
    home: str
    away: str
    league: str
    league_code: str
    kickoff: object
    frontend_id: str
    state: MatchState
    theta: Dict[str, Dict[str, float]]      # layer -> dim -> value
    theta_age_min: float
    game_state: str
    splits: Dict[str, float]
    # A published full-time result, or the match being voided, is the feed
    # saying there is nothing left to trade here.
    ft_score: tuple = (None, None)
    voided: bool = False

    @property
    def finished(self) -> bool:
        return self.voided or self.ft_score[0] is not None


@dataclass
class TickFrame:
    """Everything one tick needs, already joined."""

    as_of: object
    source: str
    contexts: Dict[int, MatchContext]
    selections: pd.DataFrame
    quotes: pd.DataFrame
    events: pd.DataFrame
    odds_history: pd.DataFrame
    warnings: List[str] = field(default_factory=list)
    # Walk-forward (phase, pool) -> mean next-bucket turnover. Set by the
    # backtest runner so the gametime / blend predictors have something to
    # read; the live desk leaves it empty and those strategies fall back.
    turnover_profile: Optional[dict] = None

    @property
    def match_ids(self):
        return list(self.contexts.keys())


class Preprocessor:
    """Snapshot -> TickFrame."""

    def __init__(self, theta_layer=None):
        self.layer = theta_layer or config.THETA_LAYER

    def build(self, snap: Snapshot) -> TickFrame:
        if snap.match_info.empty:
            log.warning("snapshot has no matches")
            return TickFrame(snap.as_of, snap.source, {},
                             _empty_selections(), _empty_quotes(),
                             pd.DataFrame(), pd.DataFrame(),
                             ["snapshot contained no matches"])

        warnings = list(snap.notes)
        events = self._normalise_events(snap)
        contexts = self._contexts(snap, events, warnings)
        if not contexts:
            raise FeatureEngineeringError("no match survived preprocessing")

        latest_odds = self._latest_odds(snap)
        quotes = self._latest_quotes(snap)
        turnover = self._turnover_windows(snap)
        selections = self._selections(
            snap, contexts, latest_odds, quotes, turnover, warnings
        )
        contexts, selections = self._tradable(contexts, selections, warnings)
        if not contexts:
            raise FeatureEngineeringError(
                "no tradable match on this snapshot - every match is either "
                "finished, voided or has stopped pricing"
            )
        history = self._odds_history(snap)

        log.info(
            "preprocessed %d matches, %d selections, %d quotes",
            len(contexts), len(selections), len(quotes),
        )
        return TickFrame(
            as_of=snap.as_of, source=snap.source, contexts=contexts,
            selections=selections, quotes=quotes, events=events,
            odds_history=history, warnings=warnings,
        )

    # -- events --------------------------------------------------------------

    def _normalise_events(self, snap: Snapshot) -> pd.DataFrame:
        ev = snap.events
        if ev.empty:
            return pd.DataFrame(columns=[
                "match_id", "at", "minute", "type", "side", "provider_id", "detail"
            ])
        ev = ev.copy()
        ev = ev[ev["is_deleted"].astype(str).isin(["0", "False", "false", "None", "nan"])]
        ev["incident_datetime"] = pd.to_datetime(ev["incident_datetime"], errors="coerce")
        ev = ev.dropna(subset=["incident_datetime"])
        ev["incident_type"] = pd.to_numeric(ev["incident_type"], errors="coerce")
        ev["home_or_away"] = pd.to_numeric(ev["home_or_away"], errors="coerce")

        out = pd.DataFrame({
            "match_id": ev["match_id"].astype(int),
            "at": ev["incident_datetime"],
            "provider_id": ev["provider_id"],
            "type": ev["incident_type"].map(INCIDENT_LABEL).fillna("OTHER"),
            "side": ev["home_or_away"].map({1.0: "home", 2.0: "away"}).fillna(""),
            "raw_type": ev["incident_type"],
            # stage 3 is the first half, 5 the second; this is how a
            # half-time score is recovered when the result feed lags.
            "stage_id": pd.to_numeric(ev["stage_id"], errors="coerce").fillna(0),
        })
        out["detail"] = out["type"] + out["side"].apply(
            lambda s: " ({})".format(s) if s else ""
        )
        return out.sort_values("at", ascending=False).reset_index(drop=True)

    def _reconcile(self, ev_match: pd.DataFrame, codes_in, codes_out):
        """Net a tally per provider and take the highest, per side.

        A cancel incident removes an earlier one, and providers do not
        cancel in lockstep, so trusting a single provider risks pricing off
        a goal that has since been chalked off.
        """
        if ev_match.empty:
            return 0, 0
        rows = ev_match[ev_match["raw_type"].isin(codes_in + codes_out)]
        if rows.empty:
            return 0, 0
        best_home, best_away = 0, 0
        for _, group in rows.groupby("provider_id"):
            delta = group["raw_type"].isin(codes_in).map({True: 1, False: -1})
            home = int(delta[group["side"] == "home"].sum())
            away = int(delta[group["side"] == "away"].sum())
            best_home = max(best_home, max(home, 0))
            best_away = max(best_away, max(away, 0))
        return best_home, best_away

    # -- per-match context ---------------------------------------------------

    def _contexts(self, snap, events, warnings) -> Dict[int, MatchContext]:
        params = snap.params.set_index("match_id") if not snap.params.empty else None
        contexts = {}

        for _, row in snap.match_info.iterrows():
            match_id = int(row["match_id"])
            prow = None
            if params is not None and match_id in params.index:
                prow = params.loc[match_id]
                if isinstance(prow, pd.DataFrame):
                    prow = prow.iloc[-1]

            ev_match = events[events["match_id"] == match_id] if not events.empty else events
            goals_h, goals_a = self._reconcile(ev_match, GOAL_IN, GOAL_OUT)
            corners_h, corners_a = self._reconcile(ev_match, CORNER_IN, CORNER_OUT)

            game_state, minute = self._clock(prow)
            phase = self._phase(game_state, minute)

            # A published full-time result is the feed saying the match is
            # over, and it is the only signal that says so plainly: GameState
            # is still SecondHalf long after the whistle, with the clock
            # running past 90, so the phase alone would keep a finished match
            # on the board looking tradable.
            ft_h, ft_a = _score_or_none(row, "HomeScoreFT", "AwayScoreFT")
            voided = _flag(row.get("IsVoid"))
            if ft_h is not None or voided:
                phase = "full_time"

            ht_h, ht_a = _score_or_none(row, "HomeScoreHT", "AwayScoreHT")
            ht_ch, ht_ca = _score_or_none(row, "HomeCornerHT", "AwayCornerHT")
            if phase != "prematch" and ht_h is None and minute >= 45:
                # The feed has not published a half-time result yet; the tape
                # is the only record of where the score stood at the break.
                ht_h, ht_a = self._first_half_score(ev_match, GOAL_IN, GOAL_OUT)
                ht_ch, ht_ca = self._first_half_score(ev_match, CORNER_IN, CORNER_OUT)

            state = MatchState(
                home_score=goals_h, away_score=goals_a,
                ht_home=ht_h, ht_away=ht_a,
                home_corner=corners_h, away_corner=corners_a,
                ht_home_corner=ht_ch, ht_away_corner=ht_ca,
                minute=minute, phase=phase,
            )

            theta, splits, age = self._theta(prow, snap.as_of, warnings, match_id)

            contexts[match_id] = MatchContext(
                match_id=match_id,
                home=str(row.get("HomeName") or "Home"),
                away=str(row.get("AwayName") or "Away"),
                league=str(row.get("LeagueName") or row.get("LeagueCode") or ""),
                league_code=str(row.get("LeagueCode") or ""),
                kickoff=row.get("KOTime"),
                frontend_id=str(row.get("FrontendID") or ""),
                state=state, theta=theta, theta_age_min=age,
                game_state=game_state, splits=splits,
                ft_score=(ft_h, ft_a), voided=voided,
            )
        return contexts

    def _tradable(self, contexts, selections, warnings):
        """Keep the matches a trader can still act on.

        The notebook's active-pool query asks only whether a pool has
        started selling, so it keeps returning matches that finished hours
        ago - which is why a card can come back full of second halves in
        the ninety-third minute. Three things say a match is done, and any
        one of them is enough:

            the feed published a full-time result, or voided the match
            not one of its pools is in the active list
            no price on it has moved in DEAD_ODDS_MINUTES

        Dropped matches are counted by reason in the tick's warnings, so a
        card that comes back smaller than expected says why.
        """
        if not contexts:
            return contexts, selections

        by_match = {}
        if not selections.empty:
            for match_id, rows in selections.groupby("match_id"):
                by_match[int(match_id)] = rows

        keep, dropped = {}, {"finished": 0, "no pools": 0, "not pricing": 0}
        dropped_details = {"finished": [], "no pools": [], "not pricing": []}
        for match_id, ctx in contexts.items():
            if ctx.finished and not config.SHOW_FINISHED:
                dropped["finished"] += 1
                dropped_details["finished"].append(
                    "{} {} v {} (FT:{}, void:{})".format(
                        match_id, ctx.home, ctx.away,
                        ctx.ft_score, ctx.voided))
                continue
            rows = by_match.get(int(match_id))
            if rows is None or rows.empty:
                dropped["no pools"] += 1
                dropped_details["no pools"].append(
                    "{} {} v {} (no odds rows)".format(
                        match_id, ctx.home, ctx.away))
                continue
            if "pool_open" in rows and not bool(rows["pool_open"].any()):
                dropped["no pools"] += 1
                dropped_details["no pools"].append(
                    "{} {} v {} (no pools in active_pools list)".format(
                        match_id, ctx.home, ctx.away))
                continue
            ages = pd.to_numeric(rows.get("odds_age_sec"), errors="coerce")
            freshest = ages.min() if ages is not None and len(ages) else None
            # Only apply staleness filter to in-play matches.
            # Pre-match odds move slowly; a match kicking off in 3 hours
            # does not need a fresh price every 30 minutes.
            if state := ctx.state:
                is_live = state.in_play
            else:
                is_live = True  # be conservative if state is missing
            if (is_live and freshest is not None and pd.notna(freshest)
                    and freshest > config.DEAD_ODDS_MINUTES * 60):
                dropped["not pricing"] += 1
                dropped_details["not pricing"].append(
                    "{} {} v {} (oldest odds {:.0f}s, threshold {:.0f}s)".format(
                        match_id, ctx.home, ctx.away,
                        freshest, config.DEAD_ODDS_MINUTES * 60))
                continue
            keep[match_id] = ctx

        total = sum(dropped.values())
        if total:
            detail_parts = []
            for reason, entries in dropped_details.items():
                if entries:
                    detail_parts.append("{}={} [{}]".format(
                        entries[0],
                        len(entries),
                        ", ".join(entries[:5])))
            warnings.append(
                "{} of {} matches dropped: {} | details: {}".format(
                    total, len(contexts),
                    ", ".join("{} {}".format(v, k)
                              for k, v in dropped.items() if v),
                    "; ".join(detail_parts)))

        if not keep and dropped["not pricing"]:
            # Every match failing on staleness alone means the clock, not
            # the card: an as-of that disagrees with the feed's timestamps
            # ages everything at once. An over-filtered board is worse than
            # a stale one, so keep whatever has not actually finished and
            # say plainly that the ages cannot be trusted.
            keep = {
                match_id: ctx for match_id, ctx in contexts.items()
                if (not ctx.finished or config.SHOW_FINISHED)
                and not by_match.get(int(match_id), pd.DataFrame()).empty
            }
            if keep:
                log.warning("staleness filter matched every match; ignoring it")
                warnings.append(
                    "every match looked stale, so the {}-minute staleness rule "
                    "was ignored - check that the odds timestamps agree with "
                    "this machine's clock".format(int(config.DEAD_ODDS_MINUTES))
                )

        if not selections.empty and keep:
            selections = selections[
                selections["match_id"].isin(keep)
            ].reset_index(drop=True)
        return keep, selections

    def _clock(self, prow):
        if prow is None:
            return "Prematch", 0.0
        state = str(prow.get("GameState") or "Prematch")
        seconds = pd.to_numeric(prow.get("TimeInSecond"), errors="coerce")
        minute = float(seconds) / 60.0 if pd.notna(seconds) else 0.0
        return state, round(minute, 1)

    def _phase(self, game_state, minute):
        """Map GameState onto a phase, inferring the break from the clock.

        algo_param never says HalfTime - during the interval the state is
        still FirstHalf with the clock stopped at 45:00 - so anything at or
        past 45 minutes that still calls itself FirstHalf is the break.
        """
        phase = GAME_STATE_MAP.get(game_state, "prematch")
        if phase == "first_half" and minute >= 45.0:
            return "half_time"
        return phase

    def _first_half_score(self, ev_match, codes_in, codes_out):
        """Reconciled first-half score, taken from stage-3 incidents only."""
        if ev_match.empty:
            return None, None
        first = ev_match[ev_match["stage_id"] <= 3]
        return self._reconcile(first, codes_in, codes_out)

    def _theta(self, prow, as_of, warnings, match_id):
        """Read all three algo_param layers into the 8-dimensional space."""
        layers = {}
        splits = {"goal": config.DEFAULT_FH_SPLIT, "corner": config.DEFAULT_FH_SPLIT}
        age = 0.0

        if prow is None:
            warnings.append("match {}: no algo_param row, using defaults".format(match_id))
            defaults = {
                "goal_tg": 2.6, "goal_sup": 0.0,
                "goal_fh_tg": 2.6 * config.DEFAULT_FH_SPLIT, "goal_fh_sup": 0.0,
                "corner_tg": 10.0, "corner_sup": 0.0,
                "corner_fh_tg": 10.0 * config.DEFAULT_FH_SPLIT, "corner_fh_sup": 0.0,
            }
            return {k: dict(defaults) for k in ("Basis", "Model", "Current")}, splits, 999.0

        goal_split = _num(prow.get("Goal90SecondHalfSplitFH"), config.DEFAULT_FH_SPLIT)
        corner_split = _num(prow.get("Corner90SecondHalfSplitFH"), config.DEFAULT_FH_SPLIT)
        splits = {"goal": goal_split, "corner": corner_split}

        for layer in ("Basis", "Model", "Current"):
            tg = _num(prow.get("Goal90{}TG".format(layer)), 2.6)
            sup = _num(prow.get("Goal90{}SUP".format(layer)), 0.0)
            ctg = _num(prow.get("Corner90{}TG".format(layer)), 10.0)
            csup = _num(prow.get("Corner90{}SUP".format(layer)), 0.0)
            sup = self._coherent_sup(sup, tg, layer, "goal", match_id, warnings)
            csup = self._coherent_sup(csup, ctg, layer, "corner", match_id, warnings)
            layers[layer] = {
                "goal_tg": tg,
                "goal_sup": sup,
                "goal_fh_tg": tg * goal_split,
                "goal_fh_sup": sup * goal_split,
                "corner_tg": ctg,
                "corner_sup": csup,
                "corner_fh_tg": ctg * corner_split,
                "corner_fh_sup": csup * corner_split,
            }

        stamp = pd.to_datetime(prow.get("EventTime"), errors="coerce")
        if pd.notna(stamp):
            age = max((as_of - stamp.to_pydatetime()).total_seconds() / 60.0, 0.0)
        return layers, splits, round(age, 1)

    def _coherent_sup(self, sup, total, layer, domain, match_id, warnings):
        """Keep supremacy inside the total it is a difference of.

        Late in a match the remaining expectancy collapses towards zero
        while the supremacy carries its own noise, so the feed can hand us
        a difference wider than the total it came from. Taken literally
        that says one side cannot possibly score again, and every market
        on the other side of it prices at zero. Clipping is the honest
        reading; saying so out loud is what stops it being a silent one.
        """
        if total <= 0 or abs(sup) <= total:
            return sup
        warnings.append(
            "match {}: {} {} SUP {:+.2f} exceeds TG {:.2f}; clipped".format(
                match_id, layer, domain, sup, total)
        )
        return math.copysign(total, sup)

    # -- odds and quotes -----------------------------------------------------

    def _latest_odds(self, snap: Snapshot) -> pd.DataFrame:
        """Newest HKJC price per selection, plus the price five minutes back."""
        odds = snap.odds
        if odds.empty:
            return pd.DataFrame()
        odds = odds.copy()
        odds["effective_datetime"] = pd.to_datetime(
            odds["effective_datetime"], errors="coerce"
        )
        odds = odds.dropna(subset=["effective_datetime"])
        odds = odds[odds["effective_datetime"] <= snap.as_of]
        keys = ["pool_id", "line_id", "combination_id"]
        odds = odds.sort_values("effective_datetime")

        latest = odds.groupby(keys, as_index=False).last()
        cutoff = snap.as_of - timedelta(minutes=config.BUCKET_MINUTES)
        prior = odds[odds["effective_datetime"] <= cutoff]
        prior = prior.groupby(keys, as_index=False).last()[keys + ["odds"]]
        prior = prior.rename(columns={"odds": "odds_prev"})

        merged = latest.merge(prior, on=keys, how="left")
        merged["odds_age_sec"] = (
            snap.as_of - merged["effective_datetime"]
        ).dt.total_seconds()
        return merged

    def _latest_quotes(self, snap: Snapshot) -> pd.DataFrame:
        """Newest external price per selection per bookmaker."""
        market = snap.market
        if market.empty:
            return _empty_quotes()
        market = market.copy()
        market["effective_datetime"] = pd.to_datetime(
            market["effective_datetime"], errors="coerce"
        )
        market = market.dropna(subset=["effective_datetime", "odds"])
        market = market[market["effective_datetime"] <= snap.as_of]
        market = market.sort_values("effective_datetime")
        latest = market.groupby(
            ["pool_id", "line_id", "combination_id", "bookmaker"], as_index=False
        ).last()
        latest["age_sec"] = (
            snap.as_of - latest["effective_datetime"]
        ).dt.total_seconds().astype(int)
        return latest

    def _odds_history(self, snap: Snapshot) -> pd.DataFrame:
        """Trimmed HKJC price history for the cockpit's sparklines."""
        odds = snap.odds
        if odds.empty:
            return pd.DataFrame()
        odds = odds.copy()
        odds["effective_datetime"] = pd.to_datetime(
            odds["effective_datetime"], errors="coerce"
        )
        window = snap.as_of - timedelta(minutes=max(config.TURNOVER_WINDOWS) + 5)
        odds = odds[
            (odds["effective_datetime"] >= window)
            & (odds["effective_datetime"] <= snap.as_of)
        ]
        return odds.sort_values("effective_datetime")[
            ["pool_id", "line_id", "combination_id", "odds", "true_odds",
             "effective_datetime"]
        ]

    # -- turnover ------------------------------------------------------------

    def _turnover_windows(self, snap: Snapshot) -> pd.DataFrame:
        """Trailing 5-minute turnover and ticket counts per selection.

        Window w covers (as_of - w, as_of - w + 5], matching the notebook's
        inv_hist_windows construction. Window 5 is the most recent bucket
        and, under the MVP rule, also the forecast for the next one.
        """
        inv = snap.investments
        keys = ["pool_id", "line_no", "comb_id"]
        if inv.empty:
            return pd.DataFrame(columns=keys)

        inv = inv.copy()
        inv["start_sell_time"] = pd.to_datetime(
            inv["start_sell_time"], errors="coerce"
        )
        inv = inv.dropna(subset=["start_sell_time"])
        for col in keys:
            inv[col] = pd.to_numeric(inv[col], errors="coerce")
        inv = inv.dropna(subset=keys)
        for col in keys:
            inv[col] = inv[col].astype(int)
        inv["turnover"] = pd.to_numeric(inv["turnover"], errors="coerce").fillna(0.0)
        inv["ticket_count"] = pd.to_numeric(
            inv["ticket_count"], errors="coerce"
        ).fillna(0).astype(int)

        frames = []
        for window in config.TURNOVER_WINDOWS:
            end = snap.as_of - timedelta(minutes=window - config.BUCKET_MINUTES)
            start = snap.as_of - timedelta(minutes=window)
            slice_ = inv[
                (inv["start_sell_time"] > start) & (inv["start_sell_time"] <= end)
            ]
            agg = slice_.groupby(keys, as_index=False).agg(
                turnover=("turnover", "sum"), tickets=("ticket_count", "sum")
            )
            agg = agg.rename(columns={
                "turnover": "t{}m".format(window),
                "tickets": "tickets{}m".format(window),
            })
            frames.append(agg)

        out = frames[0]
        for frame in frames[1:]:
            out = out.merge(frame, on=keys, how="outer")

        # Everything already sold on this selection today.
        day_start = snap.as_of.replace(hour=0, minute=0, second=0, microsecond=0)
        today = inv[inv["start_sell_time"] >= day_start]
        invested = today.groupby(keys, as_index=False).agg(
            invested=("turnover", "sum"), tickets_today=("ticket_count", "sum")
        )
        out = out.merge(invested, on=keys, how="outer").fillna(0.0)
        return out

    # -- selections ----------------------------------------------------------

    def _selections(self, snap, contexts, latest_odds, quotes, turnover, warnings):
        if latest_odds.empty:
            warnings.append("snapshot carried no odds; book is empty")
            return _empty_selections()

        open_pools = set(
            snap.active_pools["pool_id"].astype(int).tolist()
        ) if not snap.active_pools.empty else set()

        quote_stats = self._quote_stats(quotes)
        rows = []
        unknown = {}

        for rec in latest_odds.to_dict("records"):
            match_id = int(rec["match_id"])
            ctx = contexts.get(match_id)
            if ctx is None:
                continue
            code = rec.get("pool_name")
            pool = pool_defs.resolve(code)
            if pool is None:
                if not pool_defs.is_extra_time(code):
                    unknown[str(code)] = unknown.get(str(code), 0) + 1
                continue

            sel = pool_defs.normalize_selection(pool, rec.get("combination_string"))
            if sel is None:
                unknown["{}:{}".format(code, rec.get("combination_string"))] = 1
                continue

            line_value = pool_defs.parse_line(rec.get("line_label"))
            if pool.has_line and line_value is None:
                warnings.append(
                    "{} pool {} line {} has no parsable label".format(
                        code, rec.get("pool_id"), rec.get("line_id"))
                )
                continue

            pool_id = int(rec["pool_id"])
            line_id = int(rec["line_id"]) if pd.notna(rec.get("line_id")) else 0
            comb_id = int(rec["combination_id"]) if pd.notna(rec.get("combination_id")) else 0
            stats = quote_stats.get((pool_id, line_id, comb_id), {})

            rows.append({
                "match_id": match_id,
                "key": pool_defs.selection_key(match_id, pool.code, line_id, comb_id),
                "pool_id": pool_id,
                "pool_code": pool.code,
                "pool_name": pool.name,
                "family": pool.family,
                "seg": pool.seg,
                "domain": pool.domain,
                "kind": pool.kind,
                "optimized": pool.optimized,
                "settles_on_score": pool.settles_on_score,
                "margin": pool.margin,
                "line_id": line_id,
                "line_label": str(rec.get("line_label") or ""),
                "line_value": line_value,
                "comb_id": comb_id,
                "comb_str": str(rec.get("combination_string") or ""),
                "selection": sel,
                "sel_label": pool_defs.selection_label(pool, sel, ctx.home, ctx.away),
                "hkjc_odds": _num(rec.get("odds"), 0.0),
                "hkjc_odds_prev": _num(rec.get("odds_prev"), None),
                "hkjc_true_odds": _num(rec.get("true_odds"), None),
                "odds_age_sec": int(_num(rec.get("odds_age_sec"), 0)),
                "pool_open": (not open_pools) or (pool_id in open_pools),
                "mkt_min": stats.get("min"),
                "mkt_avg": stats.get("avg"),
                "mkt_max": stats.get("max"),
                "mkt_n": stats.get("n", 0),
                "mkt_age_sec": stats.get("age"),
            })

        skipped_ctx = int(len(latest_odds)) - int(len(rows)) - int(sum(unknown.values()))
        if skipped_ctx > 0:
            warnings.append(
                "{} odds rows skipped because match had no context (already filtered)".format(
                    skipped_ctx))
        if unknown:
            warnings.append(
                "unmapped feed rows (pool not recognized or selection unparseable): "
                + ", ".join(
                    "{} x{}".format(k, v) for k, v in sorted(unknown.items())[:12]
                )
            )

        frame = pd.DataFrame(rows)
        if frame.empty:
            return _empty_selections()

        frame = frame.merge(
            turnover,
            left_on=["pool_id", "line_id", "comb_id"],
            right_on=["pool_id", "line_no", "comb_id"],
            how="left",
        ) if not turnover.empty else frame

        for window in config.TURNOVER_WINDOWS:
            col = "t{}m".format(window)
            if col not in frame.columns:
                frame[col] = 0.0
            frame[col] = frame[col].fillna(0.0)
            tcol = "tickets{}m".format(window)
            if tcol not in frame.columns:
                frame[tcol] = 0
            frame[tcol] = frame[tcol].fillna(0).astype(int)
        for col in ("invested", "tickets_today"):
            if col not in frame.columns:
                frame[col] = 0.0
            frame[col] = frame[col].fillna(0.0)

        frame["avg_stake"] = np.where(
            frame["tickets5m"] > 0, frame["t5m"] / frame["tickets5m"].clip(lower=1), 0.0
        )
        frame = self._mark_main_lines(frame)
        return frame.reset_index(drop=True)

    def _quote_stats(self, quotes):
        """Best, worst and mean external price per selection."""
        if quotes.empty:
            return {}
        grouped = quotes.groupby(["pool_id", "line_id", "combination_id"])
        out = {}
        for key, group in grouped:
            prices = pd.to_numeric(group["odds"], errors="coerce").dropna()
            prices = prices[prices > 1.0]
            if prices.empty:
                continue
            out[tuple(int(k) for k in key)] = {
                "min": float(prices.min()),
                "max": float(prices.max()),
                "avg": float(prices.mean()),
                "n": int(len(prices)),
                "age": int(group["age_sec"].min()),
            }
        return out

    def _mark_main_lines(self, frame):
        """Flag the line each pool is really trading on.

        For Asian pools that is the line closest to even money, which is
        what the feed's own main-line flag tracks; single-line pools are
        trivially main.
        """
        frame = frame.copy()
        frame["is_main_line"] = True
        asian = frame["kind"].isin(("asian_hcp", "asian_total", "handicap_3w"))
        if not asian.any():
            return frame

        for (_, _), group in frame[asian].groupby(["match_id", "pool_code"]):
            balance = {}
            for line_id, legs in group.groupby("line_id"):
                prices = legs["hkjc_odds"].astype(float)
                prices = prices[prices > 1.0]
                if len(prices) < 2:
                    balance[line_id] = 9e9
                else:
                    balance[line_id] = float(prices.max() - prices.min())
            if not balance:
                continue
            main = min(balance, key=balance.get)
            frame.loc[group.index, "is_main_line"] = group["line_id"] == main
        return frame


def _num(value, default=0.0):
    try:
        if value is None:
            return default
        out = float(value)
        return default if np.isnan(out) else out
    except (TypeError, ValueError):
        return default


def _flag(value) -> bool:
    """A SQL bit, read whichever way the driver happened to hand it over.

    The same column arrives as True, 1, 1.0, "1" or "True" depending on the
    driver and whether it went through parquet on the way, and a flag that
    silently reads False is a finished match left on the board.
    """
    if value is None or (isinstance(value, float) and np.isnan(vaue)):
        return False
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("1", "true", "t", "y", "yes"):
        return True
    try:
        return float(text) != 0.0
    except ValueError:
        return False


def _score_or_none(row, home_col, away_col):
    home = pd.to_numeric(row.get(home_col), errors="coerce")
    away = pd.to_numeric(row.get(away_col), errors="coerce")
    if pd.isna(home) or home < 0:
        return None, None
    return int(home), int(away if pd.notna(away) and away >= 0 else 0)


def _empty_selections():
    return pd.DataFrame(columns=[
        "match_id", "key", "pool_id", "pool_code", "pool_name", "family",
        "seg", "domain", "kind", "optimized", "settles_on_score", "margin",
        "line_id", "line_label", "line_value", "comb_id", "comb_str",
        "selection", "sel_label", "hkjc_odds", "hkjc_odds_prev",
        "hkjc_true_odds", "odds_age_sec", "pool_open", "mkt_min", "mkt_avg",
        "mkt_max", "mkt_n", "mkt_age_sec", "is_main_line",
    ])


def _empty_quotes():
    return pd.DataFrame(columns=[
        "pool_id", "line_id", "combination_id", "bookmaker", "bookmaker_str",
        "odds", "effective_datetime", "age_sec",
    ])