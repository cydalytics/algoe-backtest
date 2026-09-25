"""
Meeting World

A coherent multi-tick football meeting. One seed produces a set of matches
with a shared underlying truth, a full 90-minute incident tape, and a
5-minute investment / odds tape. ``snapshot(as_of)`` slices that world the
way the live desk would have seen it; ``labels(as_of)`` is the next bucket
and the finished scoreline, which is what the backtest scores against.

The one-shot ``SimSource`` cannot do this: every ``fetch`` rebuilds a new
card, so there is no next-bucket actual and no settlement.

Change Log:
-----------
2026-09-11      Initialize (W06 backtest)
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.core import config
from src.core.logging import get_logger
from src.hkjc.simulator import (
    BET_TYPE_CODE, BOOKMAKERS, LEAGUES, POOL_WEIGHT, QUOTED_POOLS, TEAMS,
    _round_to,
)
from src.hkjc.snapshot import Snapshot
from src.pricing import book as pricing
from src.pricing import pools as pool_defs

log = get_logger(__name__)

# Kickoff offsets from meeting start, in minutes. Cycled so a short card
# still covers prematch, both halves and the break.
KO_OFFSETS = (25, -12, -38, -52, -70, 8, -20, 40, 15, -80, -5, 50)

POOL_ORDER = (
    "HAD", "HDC", "HILO", "HHAD", "OOE", "TTG", "CRS", "HFT",
    "CHLO", "CHDC", "FHAD", "FHDC", "FHLO", "FCRS",
)

COMB_ID = {
    "H": 1, "D": 2, "A": 3, "L": 4, "O": 5, "E": 6, "OTHER": 99,
}


def _floor_5(stamp: datetime) -> datetime:
    stamp = stamp.replace(second=0, microsecond=0)
    return stamp - timedelta(minutes=stamp.minute % config.BUCKET_MINUTES)


def phase_at(kickoff: datetime, as_of: datetime) -> Tuple[str, float]:
    """Game phase and clock minute at ``as_of``."""
    if as_of < kickoff:
        return "prematch", 0.0
    elapsed = (as_of - kickoff).total_seconds() / 60.0
    if elapsed < 45.0:
        return "first_half", float(elapsed)
    if elapsed < 60.0:
        return "half_time", 45.0
    if elapsed < 105.0:
        return "second_half", 45.0 + float(elapsed - 60.0)
    return "full_time", 90.0


def remaining_theta(truth, minute, phase, fh_goal, fh_corner):
    """Expectation still to come, same reading the live simulator uses."""
    if phase == "prematch":
        played = 0.0
    elif phase in ("half_time", "full_time"):
        played = 45.0 if phase == "half_time" else 90.0
    else:
        played = min(float(minute), 90.0)
    left = max(90.0 - played, 0.0)
    frac = left / 90.0 if phase != "full_time" else 0.0
    if phase == "prematch":
        fh_g, fh_c = fh_goal, fh_corner
    elif phase == "first_half":
        fh_g = max(45.0 - played, 0.0) / max(left, 1e-6)
        fh_c = fh_g
    else:
        fh_g = fh_c = 0.0
    return {
        "goal_tg": truth["goal_tg"] * frac,
        "goal_sup": truth["goal_sup"] * frac,
        "corner_tg": truth["corner_tg"] * frac,
        "corner_sup": truth["corner_sup"] * frac,
        "fh_goal_share": fh_g,
        "fh_corner_share": fh_c,
        "played": played,
    }


def pool_id_for(match_index: int, code: str) -> int:
    return 500_000_000 + match_index * 200 + POOL_ORDER.index(code)


def line_id_for(line) -> int:
    if line is None:
        return 1
    return 1000 + int(round(float(line) * 4))


def comb_id_for(sel: str) -> int:
    if sel in COMB_ID:
        return COMB_ID[sel]
    if ":" in sel:
        try:
            h, a = sel.split(":", 1)
            return 10 + int(h) * 10 + int(a)
        except ValueError:
            return 90
    if sel.endswith("+") and sel[:-1].isdigit():
        return 80 + int(sel[:-1])
    if sel.isdigit():
        return 70 + int(sel)
    if len(sel) == 2 and sel[0] in "HDA" and sel[1] in "HDA":
        return 50 + "HDA".index(sel[0]) * 3 + "HDA".index(sel[1])
    return 90


def label_key(match_id, pool_code, line, selection) -> str:
    line_s = "" if line is None else "{:.4f}".format(float(line))
    return "{}|{}|{}|{}".format(int(match_id), pool_code, line_s, selection)


@dataclass
class MatchTruth:
    match_id: int
    index: int
    home: str
    away: str
    league_code: str
    league_name: str
    country: str
    kickoff: datetime
    truth: dict
    fh_goal: float
    fh_corner: float
    incidents: list
    final: pricing.MatchState
    competition: str = "league"


@dataclass
class MeetingWorld:
    """A generated meeting that can be sliced at any 5-minute stamp."""

    seed: int
    n_matches: int
    start: datetime
    n_ticks: int
    matches: List[MatchTruth] = field(default_factory=list)
    investments: pd.DataFrame = field(default_factory=pd.DataFrame)
    odds: pd.DataFrame = field(default_factory=pd.DataFrame)
    market: pd.DataFrame = field(default_factory=pd.DataFrame)
    events: pd.DataFrame = field(default_factory=pd.DataFrame)
    params_by_tick: Dict[datetime, pd.DataFrame] = field(default_factory=dict)
    info_by_tick: Dict[datetime, pd.DataFrame] = field(default_factory=dict)
    active_by_tick: Dict[datetime, pd.DataFrame] = field(default_factory=dict)

    @property
    def timeline(self) -> List[datetime]:
        step = timedelta(minutes=config.BUCKET_MINUTES)
        return [self.start + i * step for i in range(self.n_ticks)]

    def snapshot(self, as_of: datetime) -> Snapshot:
        """The book as the desk would have seen it at ``as_of``."""
        as_of = _floor_5(as_of)
        if as_of not in self.info_by_tick:
            raise KeyError("no tick built at {}".format(as_of))
        inv = self.investments
        odds = self.odds
        market = self.market
        events = self.events
        if not inv.empty:
            inv = inv[inv["start_sell_time"] <= as_of]
        if not odds.empty:
            odds = odds[odds["effective_datetime"] <= as_of]
        if not market.empty:
            market = market[market["effective_datetime"] <= as_of]
        if not events.empty:
            events = events[events["incident_datetime"] <= as_of]
        snap = Snapshot(
            as_of=as_of,
            source="scenario",
            active_pools=self.active_by_tick[as_of].copy(),
            match_info=self.info_by_tick[as_of].copy(),
            params=self.params_by_tick[as_of].copy(),
            events=events.copy(),
            odds=odds.copy(),
            market=market.copy(),
            investments=inv.copy(),
        ).conform()
        snap.notes.append(
            "meeting world seed={} ticks={} - labels live in the next bucket"
            .format(self.seed, self.n_ticks)
        )
        return snap

    def labels(self, as_of: datetime) -> pd.DataFrame:
        """Next-bucket turnover plus the finished scoreline, keyed for join."""
        as_of = _floor_5(as_of)
        end = as_of + timedelta(minutes=config.BUCKET_MINUTES)
        inv = self.investments
        if inv.empty:
            return pd.DataFrame(columns=[
                "label_key", "t_actual", "t_actual_tickets",
                "ft_home", "ft_away", "ht_home", "ht_away",
                "c_ft_home", "c_ft_away", "c_ht_home", "c_ht_away",
            ])
        window = inv[
            (inv["start_sell_time"] > as_of) & (inv["start_sell_time"] <= end)
        ]
        if window.empty:
            agg = pd.DataFrame(columns=["label_key", "t_actual", "t_actual_tickets"])
        else:
            agg = window.groupby("label_key", as_index=False).agg(
                t_actual=("turnover", "sum"),
                t_actual_tickets=("ticket_count", "sum"),
            )
        finals = []
        for match in self.matches:
            st = match.final
            finals.append({
                "match_id": match.match_id,
                "ft_home": st.home_score, "ft_away": st.away_score,
                "ht_home": st.ht_home, "ht_away": st.ht_away,
                "c_ft_home": st.home_corner, "c_ft_away": st.away_corner,
                "c_ht_home": st.ht_home_corner, "c_ht_away": st.ht_away_corner,
            })
        fin = pd.DataFrame(finals)
        # Broadcast finals onto every label row via match_id parsed from key.
        if agg.empty:
            agg = pd.DataFrame(columns=["label_key", "t_actual", "t_actual_tickets"])
        agg["match_id"] = agg["label_key"].map(
            lambda k: int(str(k).split("|", 1)[0]) if k else 0
        )
        return agg.merge(fin, on="match_id", how="left")

    def finals(self) -> Dict[int, dict]:
        out = {}
        for match in self.matches:
            st = match.final
            out[match.match_id] = {
                "ft": (st.home_score, st.away_score),
                "ht": (st.ht_home, st.ht_away),
                "c_ft": (st.home_corner, st.away_corner),
                "c_ht": (st.ht_home_corner, st.ht_away_corner),
            }
        return out


def build_world(seed=None, n_matches=None, n_ticks=None,
                start=None) -> MeetingWorld:
    """Generate a deterministic meeting."""
    seed = config.SIM_SEED if seed is None else int(seed)
    n_matches = config.BACKTEST_MATCHES if n_matches is None else int(n_matches)
    n_ticks = config.BACKTEST_TICKS if n_ticks is None else int(n_ticks)
    if start is None:
        start = datetime.fromisoformat(config.BACKTEST_MEETING_START)
    start = _floor_5(start)

    rng = np.random.default_rng(seed)
    py = __import__("random").Random(seed)
    builder = _WorldBuilder(start, n_ticks, rng, py)
    used = set()
    for i in range(n_matches):
        builder.add_match(i, used)
    world = builder.finish(seed, n_matches)
    log.info(
        "meeting world seed=%s: %d matches, %d ticks, %d investment rows",
        seed, n_matches, n_ticks, len(world.investments),
    )
    return world


class _WorldBuilder:
    """Walks each match through the meeting and writes the tapes."""

    def __init__(self, start, n_ticks, rng, py):
        self.start = start
        self.n_ticks = n_ticks
        self.rng = rng
        self.py = py
        self.step = timedelta(minutes=config.BUCKET_MINUTES)
        # One extra bucket so the last scored tick has a next-window label.
        self.buckets = [start + i * self.step for i in range(n_ticks + 1)]
        self.matches: List[MatchTruth] = []
        self.investments = []
        self.odds = []
        self.market = []
        self.events = []
        self.params_by_tick = {t: [] for t in self.buckets[:-1]}
        self.info_by_tick = {t: [] for t in self.buckets[:-1]}
        self.active_by_tick = {t: [] for t in self.buckets[:-1]}
        self._incident_seq = 900_000_000

    def add_match(self, index, used):
        match_id = 50_030_000 + index * 13
        code, league_name, country = LEAGUES[index % len(LEAGUES)]
        squad = [t for t in TEAMS[code] if t not in used]
        if len(squad) < 2:
            squad = TEAMS[code][:]
        self.py.shuffle(squad)
        home, away = squad[0], squad[1]
        used.update((home, away))

        kickoff = self.start + timedelta(minutes=int(KO_OFFSETS[index % len(KO_OFFSETS)]))
        strength = self.rng.normal(0.25, 0.75)
        truth = {
            "goal_tg": float(np.clip(self.rng.normal(2.65, 0.45), 1.6, 4.2)),
            "goal_sup": float(np.clip(strength, -2.0, 2.0)),
            "corner_tg": float(np.clip(self.rng.normal(10.2, 1.5), 6.5, 14.5)),
            "corner_sup": float(np.clip(strength * 1.7, -4.5, 4.5)),
        }
        fh_goal = float(np.clip(self.rng.normal(0.45, 0.02), 0.40, 0.50))
        fh_corner = float(np.clip(self.rng.normal(0.46, 0.02), 0.41, 0.51))
        incidents, final = self._play(match_id, truth, kickoff)
        self.events.extend(incidents)

        if code == "UCL":
            competition = "cup"
        elif index % 5 == 4:
            competition = "friendly"
        else:
            competition = "league"
        match = MatchTruth(
            match_id=match_id, index=index, home=home, away=away,
            league_code=code, league_name=league_name, country=country,
            kickoff=kickoff, truth=truth, fh_goal=fh_goal, fh_corner=fh_corner,
            incidents=incidents, final=final, competition=competition,
        )
        self.matches.append(match)

        for as_of in self.buckets:
            phase, minute = phase_at(kickoff, as_of)
            state = self._state_at(incidents, kickoff, as_of, phase, minute)
            remaining = remaining_theta(truth, minute, phase, fh_goal, fh_corner)
            if as_of in self.info_by_tick:
                self.info_by_tick[as_of].append(
                    self._match_row(match, state, phase))
                self.params_by_tick[as_of].append(
                    self._param_row(match, remaining, minute, phase, as_of))
            if phase == "full_time":
                continue
            self._write_book(match, remaining, state, phase, as_of)

    def finish(self, seed, n_matches) -> MeetingWorld:
        def frame(rows, empty_cols):
            return pd.DataFrame(rows) if rows else pd.DataFrame(columns=empty_cols)

        scored = self.buckets[:-1]
        return MeetingWorld(
            seed=seed, n_matches=n_matches, start=self.start, n_ticks=self.n_ticks,
            matches=self.matches,
            investments=frame(self.investments, []),
            odds=frame(self.odds, []),
            market=frame(self.market, []),
            events=frame(self.events, []),
            params_by_tick={
                t: frame(self.params_by_tick[t], []) for t in scored
            },
            info_by_tick={
                t: frame(self.info_by_tick[t], []) for t in scored
            },
            active_by_tick={
                t: frame(self.active_by_tick[t], []) for t in scored
            },
        )

    # -- play ---------------------------------------------------------------

    def _play(self, match_id, truth, kickoff):
        rows = []
        score = {"h": 0, "a": 0}
        corners = {"h": 0, "a": 0}
        ht_score = (0, 0)
        ht_corners = (0, 0)
        goal_h = (truth["goal_tg"] + truth["goal_sup"]) / 2 / 90.0
        goal_a = (truth["goal_tg"] - truth["goal_sup"]) / 2 / 90.0
        corner_h = (truth["corner_tg"] + truth["corner_sup"]) / 2 / 90.0
        corner_a = (truth["corner_tg"] - truth["corner_sup"]) / 2 / 90.0
        for m in range(1, 91):
            when = kickoff + timedelta(minutes=m)
            if m > 45:
                when = kickoff + timedelta(minutes=15 + m)
            for side, rate, kind in (
                ("h", max(goal_h, 0), config.INCIDENT_GOAL),
                ("a", max(goal_a, 0), config.INCIDENT_GOAL),
                ("h", max(corner_h, 0), config.INCIDENT_CORNER),
                ("a", max(corner_a, 0), config.INCIDENT_CORNER),
            ):
                if self.rng.random() < rate:
                    if kind == config.INCIDENT_GOAL:
                        score[side] += 1
                    else:
                        corners[side] += 1
                    rows.extend(self._incident(match_id, when, kind, side, m))
            if self.rng.random() < 0.035:
                rows.extend(self._incident(
                    match_id, when, config.INCIDENT_YELLOW,
                    "h" if self.rng.random() < 0.5 else "a", m))
            if m == 45:
                ht_score = (score["h"], score["a"])
                ht_corners = (corners["h"], corners["a"])
        state = pricing.MatchState(
            home_score=score["h"], away_score=score["a"],
            ht_home=ht_score[0], ht_away=ht_score[1],
            home_corner=corners["h"], away_corner=corners["a"],
            ht_home_corner=ht_corners[0], ht_away_corner=ht_corners[1],
            minute=90.0, phase="full_time",
        )
        return rows, state

    def _incident(self, match_id, when, kind, side, game_minute):
        rows = []
        for provider in (1, 2):
            if provider == 2 and kind == config.INCIDENT_CORNER and self.rng.random() < 0.12:
                continue
            self._incident_seq += 1
            lag = timedelta(seconds=int(self.rng.integers(0, 25))) if provider == 2 else timedelta(0)
            rows.append({
                "match_id": match_id,
                "event_incident_id": self._incident_seq,
                "event_type": 2,
                "seq_id": game_minute,
                "stage_id": 3 if game_minute <= 45 else 5,
                "provider_id": provider,
                "incident_type": kind,
                "incident_datetime": when + lag,
                "game_time": when,
                "home_or_away": 1.0 if side == "h" else 2.0,
                "info": "",
                "last_modified_datetime": when + lag,
                "is_deleted": "0",
            })
        return rows

    def _state_at(self, incidents, kickoff, as_of, phase, minute):
        score = {"h": 0, "a": 0}
        corners = {"h": 0, "a": 0}
        ht_score = None
        ht_corners = None
        for row in incidents:
            when = row["incident_datetime"]
            if when > as_of:
                continue
            if row.get("provider_id", 1) != 1:
                continue
            kind = row["incident_type"]
            side = "h" if row["home_or_away"] == 1.0 else "a"
            if kind == config.INCIDENT_GOAL:
                score[side] += 1
            elif kind == config.INCIDENT_CORNER:
                corners[side] += 1
            if row["seq_id"] == 45:
                ht_score = (score["h"], score["a"])
                ht_corners = (corners["h"], corners["a"])
        if phase in ("half_time", "second_half", "full_time") and ht_score is None:
            ht_score = (score["h"], score["a"])
            ht_corners = (corners["h"], corners["a"])
        return pricing.MatchState(
            home_score=score["h"], away_score=score["a"],
            ht_home=ht_score[0] if ht_score else None,
            ht_away=ht_score[1] if ht_score else None,
            home_corner=corners["h"], away_corner=corners["a"],
            ht_home_corner=ht_corners[0] if ht_corners else None,
            ht_away_corner=ht_corners[1] if ht_corners else None,
            minute=minute, phase=phase,
        )

    # -- rows ---------------------------------------------------------------

    def _match_row(self, match: MatchTruth, state, phase):
        ht_done = state.ht_done
        finished = phase == "full_time"
        return {
            "match_id": match.match_id,
            "FrontendID": "{:04d}".format(1200 + match.index),
            "LeagueId": 40_000 + match.index,
            "LeagueName": match.league_name,
            "LeagueCode": match.league_code,
            "VenueId": 70_000 + match.index,
            "VenueName": "{} Stadium".format(match.home),
            "Country": match.country,
            "Season": "2026",
            "HomeName": match.home, "HomeId": 80_000 + match.index * 2,
            "AwayName": match.away, "AwayId": 80_001 + match.index * 2,
            "KOTime": match.kickoff,
            "HomeScoreFT": state.home_score if finished else -1,
            "AwayScoreFT": state.away_score if finished else -1,
            "HomeScoreHT": state.ht_home if ht_done and state.ht_home is not None else -1,
            "AwayScoreHT": state.ht_away if ht_done and state.ht_away is not None else -1,
            "HomeCornerFT": state.home_corner if finished else -1,
            "AwayCornerFT": state.away_corner if finished else -1,
            "HomeCornerHT": state.ht_home_corner if ht_done and state.ht_home_corner is not None else -1,
            "AwayCornerHT": state.ht_away_corner if ht_done and state.ht_away_corner is not None else -1,
            "HomeYellowFT": -1, "AwayYellowFT": -1,
            "HomeYellowHT": -1, "AwayYellowHT": -1,
            "HomeRedFT": -1, "AwayRedFT": -1,
            "HomeRedHT": -1, "AwayRedHT": -1,
            "IsVoid": False,
        }

    def _param_row(self, match, remaining, minute, phase, as_of):
        state_name = {
            "prematch": "Prematch", "first_half": "FirstHalf",
            "half_time": "FirstHalf", "second_half": "SecondHalf",
            "full_time": "SecondHalf",
        }[phase]
        frac = max(remaining["corner_tg"], 1e-6) / max(match.truth["corner_tg"], 1e-6)

        def layered(base, spread):
            def draw(width):
                return base * (1 + float(np.clip(self.rng.normal(0, width),
                                                 -2 * width, 2 * width)))
            return draw(spread * 2.2), draw(spread * 1.4), draw(spread)

        def layered_sup(base, total, spread):
            def draw(width):
                value = base + float(np.clip(self.rng.normal(0, width * frac),
                                             -2 * width * frac, 2 * width * frac))
                return float(np.clip(value, -total, total))
            return draw(spread * 2.2), draw(spread * 1.4), draw(spread)

        g_b, g_m, g_c = layered(max(remaining["goal_tg"], 0.05), 0.045)
        gs_b, gs_m, gs_c = layered_sup(remaining["goal_sup"], remaining["goal_tg"], 0.07)
        c_b, c_m, c_c = layered(max(remaining["corner_tg"], 0.2), 0.06)
        cs_b, cs_m, cs_c = layered_sup(remaining["corner_sup"], remaining["corner_tg"], 0.22)
        stamp = as_of - timedelta(seconds=int(self.rng.integers(20, 180)))
        return {
            "match_id": match.match_id,
            "AlgoEventID": 600_000 + match.match_id % 100_000,
            "EventTime": stamp,
            "TimeInSecond": int(remaining["played"] * 60),
            "Minutes": int(remaining["played"]),
            "Seconds": 0,
            "IsClockSet": phase != "prematch",
            "GameState": state_name,
            "Goal90BasisTG": g_b, "Goal90BasisSUP": gs_b,
            "Goal90ModelTG": g_m, "Goal90ModelSUP": gs_m,
            "Goal90CurrentTG": g_c, "Goal90CurrentSUP": gs_c,
            "Goal90DrawFactor": 1.0,
            "Goal90SecondHalfSplitFH": remaining["fh_goal_share"],
            "Goal90SecondHalfSplitFT": 1.0 - remaining["fh_goal_share"],
            "Corner90BasisTG": c_b, "Corner90BasisSUP": cs_b,
            "Corner90ModelTG": c_m, "Corner90ModelSUP": cs_m,
            "Corner90CurrentTG": c_c, "Corner90CurrentSUP": cs_c,
            "Corner90DrawFactor": 1.0,
            "Corner90SecondHalfSplitFH": remaining["fh_corner_share"],
            "Corner90SecondHalfSplitFT": 1.0 - remaining["fh_corner_share"],
            "CreateTime": stamp,
        }

    # -- book + tape --------------------------------------------------------

    def _write_book(self, match, remaining, state, phase, as_of):
        theta = {
            "goal_tg": remaining["goal_tg"],
            "goal_sup": remaining["goal_sup"],
            "goal_fh_tg": remaining["goal_tg"] * remaining["fh_goal_share"],
            "goal_fh_sup": remaining["goal_sup"] * remaining["fh_goal_share"],
            "corner_tg": remaining["corner_tg"],
            "corner_sup": remaining["corner_sup"],
            "corner_fh_tg": remaining["corner_tg"] * remaining["fh_corner_share"],
            "corner_fh_sup": remaining["corner_sup"] * remaining["fh_corner_share"],
        }
        model = pricing.BookModel(theta, state)
        plan = self._pool_plan(theta, state)
        volume = self._volume(phase, match.index, as_of, match.kickoff)

        for pool_code, lines in plan:
            pool = pool_defs.resolve(pool_code)
            pid = pool_id_for(match.index, pool_code)
            if as_of in self.active_by_tick:
                self.active_by_tick[as_of].append(
                    {"match_id": match.match_id, "pool_id": pid})
            for line in lines:
                market = model.market(pool, line)
                if not market:
                    continue
                keys = [k for k, v in market.items() if v > 1e-6]
                if not keys:
                    continue
                lid = line_id_for(line)
                fairs = [pricing.fair_odds(market[k]) for k in keys]
                appetite = {k: float(np.clip(market[k], 0.02, 0.95)) ** 0.75
                            for k in keys}
                norm = sum(appetite.values()) or 1.0
                is_main = line == lines[len(lines) // 2]
                for sel, fair in zip(keys, fairs):
                    cid = comb_id_for(sel)
                    self._quote(match, pool, pid, lid, line, cid, sel,
                                fair, market[sel], as_of)
                    self._invest(match, pool, pid, lid, line, cid, sel,
                                 appetite[sel] / norm, volume, is_main, as_of)

    def _pool_plan(self, theta, state):
        total_goal = state.home_score + state.away_score + theta["goal_tg"]
        diff_goal = state.home_score - state.away_score + theta["goal_sup"]
        total_corner = state.home_corner + state.away_corner + theta["corner_tg"]
        diff_corner = state.home_corner - state.away_corner + theta["corner_sup"]

        def spread(centre, step, count, quantum):
            base = _round_to(centre, quantum)
            half = count // 2
            return [round(base + (i - half) * step, 2) for i in range(count)]

        plan = [
            ("HAD", [None]),
            ("HDC", spread(-diff_goal, 0.25, 5, 0.25)),
            ("HILO", spread(total_goal, 0.25, 5, 0.25)),
            ("HHAD", spread(-_round_to(diff_goal, 1.0), 1.0, 3, 1.0)),
            ("OOE", [None]),
            ("TTG", [None]),
            ("CRS", [None]),
            ("HFT", [None]),
            ("CHLO", spread(total_corner, 0.5, 3, 0.5)),
            ("CHDC", spread(-diff_corner, 0.5, 3, 0.5)),
        ]
        if not state.ht_done:
            fh_goal = theta["goal_fh_tg"]
            fh_sup = theta["goal_fh_sup"]
            ht_total = state.home_score + state.away_score + fh_goal
            ht_diff = state.home_score - state.away_score + fh_sup
            plan.extend([
                ("FHAD", [None]),
                ("FHDC", spread(-ht_diff, 0.25, 3, 0.25)),
                ("FHLO", spread(ht_total, 0.25, 3, 0.25)),
                ("FCRS", [None]),
            ])
        return plan

    def _volume(self, phase, index, as_of, kickoff):
        base = {
            "prematch": 60_000, "first_half": 420_000,
            "half_time": 180_000, "second_half": 610_000,
        }.get(phase, 40_000)
        # Persistence is a real baseline only if the next bucket is close
        # but not identical - a shared shock plus independent noise.
        minutes_to_ko = (kickoff - as_of).total_seconds() / 60.0
        prematch_ramp = 1.0
        if phase == "prematch":
            prematch_ramp = float(np.clip(1.4 - minutes_to_ko / 180.0, 0.35, 1.6))
        tilt = 0.55 + 1.4 * self.rng.random()
        marquee = 2.4 if index % 4 == 0 else 1.0
        return base * tilt * marquee * prematch_ramp

    def _quote(self, match, pool, pool_id, line_id, line, comb_id, sel,
               fair, prob, as_of):
        belief = float(np.clip(prob * (1 + self.rng.normal(0, 0.012)), 1e-6, 0.999))
        true_odds = min(1.0 / belief, config.ODDS_CAP)
        offer = pricing.sell_odds(true_odds, pool.margin,
                                  1.0 + float(self.rng.normal(0, 0.008)))
        label = pool_defs.line_label(line) if line is not None else ""
        base = {
            "match_id": match.match_id,
            "bet_type_code": BET_TYPE_CODE.get(pool.code, 0),
            "pool_name": pool.code,
            "pool_id": pool_id,
            "line_id": line_id,
            "line_label": label,
            "combination_id": comb_id,
            "combination_string": sel,
        }
        # Two prints so the preprocessor can form a 5-minute delta.
        self.odds.append(dict(
            base,
            odds=pricing.ladder(max(offer * (1 + self.rng.normal(0, 0.01)),
                                    config.ODDS_FLOOR)),
            true_odds=round(true_odds * (1 + self.rng.normal(0, 0.008)), 3),
            effective_datetime=as_of - timedelta(
                minutes=config.BUCKET_MINUTES,
                seconds=int(self.rng.integers(5, 120))),
        ))
        self.odds.append(dict(
            base, odds=offer, true_odds=round(true_odds, 3),
            effective_datetime=as_of - timedelta(
                seconds=int(self.rng.integers(5, 90))),
        ))
        if pool.code not in QUOTED_POOLS:
            return
        for code, name in BOOKMAKERS[:4]:
            sharp = name in ("PinnacleSports", "Sbobet_com", "Matchbook")
            their_margin = pool.margin * (0.45 if sharp else 1.05)
            price = pricing.ladder(
                true_odds / (1 + their_margin) * (1 + float(self.rng.normal(0, 0.014)))
            )
            self.market.append(dict(
                base, bookmaker=code, bookmaker_str=name, odds=price,
                effective_datetime=as_of - timedelta(
                    seconds=int(self.rng.integers(3, 180))),
            ))

    def _invest(self, match, pool, pool_id, line_id, line, comb_id, sel,
                sel_share, match_volume, is_main, as_of):
        share = POOL_WEIGHT.get(pool.code, 0.01)
        line_factor = 1.0 if is_main else 0.32
        amount = match_volume * share * line_factor * sel_share
        amount *= float(self.rng.uniform(0.55, 1.5))
        if amount < 15:
            return
        avg_stake = float(self.rng.lognormal(mean=5.6, sigma=0.55))
        tickets = max(1, int(amount / max(avg_stake, 40.0)))
        when = as_of - timedelta(seconds=int(self.rng.integers(1, 299)))
        self.investments.append({
            "pool_id": pool_id,
            "pool_name": pool.code,
            "match_id": match.match_id,
            "frontend_id": None,
            "tournament": None,
            "h_team": None, "a_team": None,
            "ht_home_score": None, "ht_away_score": None,
            "ft_home_score": None, "ft_away_score": None,
            "kickoff_date": None,
            "start_sell_time": when,
            "comb_id": comb_id,
            "line_no": line_id,
            "odds_id": None,
            "turnover": round(amount, 2),
            "dividend": None,
            "es_dividend": None,
            "odds_combination": sel,
            "odds": None,
            "ticket_count": tickets,
            "label_key": label_key(match.match_id, pool.code, line, sel),
        })
