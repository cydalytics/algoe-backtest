"""
Simulated Source

A coherent stand-in for the HKJC SQL Servers. It emits the same seven
frames with the same columns, but every number is generated from one
underlying truth per match, so pools, lines, combinations, odds, incidents
and turnover all agree with each other.

This exists because ``Simulated_Data/`` cannot be used for this: that set
was produced by sampling each column independently from its own marginal,
so it reproduces the schema but not the relationships - FCRS rows carry a
combination of "H", HILO rows carry "01:01", and every (match, pool, line)
appears exactly once. Nothing can be priced off that.

Same seed, same book. Screenshots and demos stay reproducible.

Change Log:
-----------
2026-08-30      Initialize
"""

import random
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from src.core import config
from src.core.logging import get_logger
from src.pricing import book as pricing
from src.pricing import pools as pool_defs

log = get_logger(__name__)

LEAGUES = [
    ("EPL", "English Premier League", "ENG"),
    ("ESP", "Spanish La Liga", "ESP"),
    ("GER", "German Bundesliga", "GER"),
    ("ITA", "Italian Serie A", "ITA"),
    ("FRA", "French Ligue 1", "FRA"),
    ("JPN", "Japanese J1 League", "JPN"),
    ("HKG", "Hong Kong Premier League", "HKG"),
    ("UCL", "UEFA Champions League", "EUR"),
]

TEAMS = {
    "EPL": ["Arsenal", "Liverpool", "Manchester City", "Chelsea", "Newcastle",
            "Aston Villa", "Tottenham", "Brighton", "West Ham", "Everton"],
    "ESP": ["Real Madrid", "Barcelona", "Atletico Madrid", "Sevilla",
            "Real Sociedad", "Villarreal", "Valencia", "Athletic Bilbao"],
    "GER": ["Bayern Munich", "Bayer Leverkusen", "Borussia Dortmund",
            "RB Leipzig", "Stuttgart", "Eintracht Frankfurt", "Wolfsburg"],
    "ITA": ["Inter Milan", "AC Milan", "Juventus", "Napoli", "Roma",
            "Atalanta", "Lazio", "Fiorentina"],
    "FRA": ["Paris Saint-Germain", "Marseille", "Monaco", "Lille", "Lyon",
            "Nice", "Lens"],
    "JPN": ["Vissel Kobe", "Yokohama F Marinos", "Urawa Reds", "Kawasaki Frontale",
            "Sanfrecce Hiroshima", "FC Tokyo"],
    "HKG": ["Kitchee", "Eastern", "Lee Man", "Tai Po", "Southern District"],
    "UCL": ["Real Madrid", "Bayern Munich", "Manchester City", "Inter Milan",
            "Paris Saint-Germain", "Arsenal", "Barcelona", "Borussia Dortmund"],
}

BOOKMAKERS = [
    (13, "PinnacleSports"), (12, "Bet365"), (9, "Sbobet_com"), (6, "IBCBET"),
    (11, "WilliamHill"), (3, "Bwin"), (22, "Betway"), (24, "DafaBet"),
    (28, "Mansion88"), (41, "Unibet"), (7, "Ladbrokes"), (30, "Matchbook"),
]

# Pools that carry external prices. Nobody publishes a competitive line on
# correct score, so quoting one would be inventing data.
QUOTED_POOLS = {"HAD", "HDC", "HILO", "HHAD", "FHLO", "FHDC", "FHAD", "CHLO", "OOE", "TTG"}

BET_TYPE_CODE = {
    "HAD": 1, "FHAD": 2, "HILO": 3, "FHLO": 4, "CHLO": 5, "HDC": 6,
    "HHAD": 7, "HFT": 8, "CRS": 9, "FCRS": 10, "OOE": 11, "TTG": 12,
    "FTS": 13, "NTS": 15, "FCHLO": 45, "FHDC": 46, "FCHDC": 47, "CHDC": 48,
}

# Relative share of a match's turnover, before the in-play tilt.
POOL_WEIGHT = {
    "HAD": 0.16, "HDC": 0.22, "HILO": 0.20, "HHAD": 0.06,
    "FHAD": 0.03, "FHDC": 0.03, "FHLO": 0.04,
    "CHLO": 0.07, "CHDC": 0.03,
    "OOE": 0.02, "TTG": 0.04, "CRS": 0.06, "FCRS": 0.02, "HFT": 0.02,
}

PHASES = ["prematch", "prematch", "first_half", "first_half",
          "half_time", "second_half", "second_half", "second_half"]


def _round_to(value, step):
    return round(value / step) * step


class SimSource:
    """Deterministic, internally consistent HKJC-shaped data."""

    name = "sim"

    def __init__(self, seed=None, n_matches=None):
        self.seed = config.SIM_SEED if seed is None else int(seed)
        self.n_matches = config.SIM_MATCHES if n_matches is None else int(n_matches)

    def describe(self) -> str:
        return "simulator (seed {}, {} matches)".format(self.seed, self.n_matches)

    def fetch(self, as_of: datetime = None, match_ids=None):
        from src.hkjc.snapshot import Snapshot

        as_of = as_of or datetime.now().replace(second=0, microsecond=0)
        rng = random.Random(self.seed)
        nprng = np.random.default_rng(self.seed)

        builder = _Builder(as_of, rng, nprng)
        for i in range(self.n_matches):
            builder.add_match(i)

        snap = Snapshot(
            as_of=as_of,
            source=self.name,
            active_pools=pd.DataFrame(builder.active_pools),
            match_info=pd.DataFrame(builder.match_info),
            params=pd.DataFrame(builder.params),
            events=pd.DataFrame(builder.events),
            odds=pd.DataFrame(builder.odds),
            market=pd.DataFrame(builder.market),
            investments=pd.DataFrame(builder.investments),
        ).conform()
        snap.notes.append(
            "simulated source - not HKJC data; true odds are the generator's "
            "own belief, so MVP TrueProb is exact by construction"
        )
        log.info("sim snapshot %s: %s", as_of, snap.summary())
        return snap


class _Builder:
    """Accumulates the seven frames one match at a time."""

    def __init__(self, as_of, rng, nprng):
        self.as_of = as_of
        self.rng = rng
        self.np = nprng
        self.active_pools = []
        self.match_info = []
        self.params = []
        self.events = []
        self.odds = []
        self.market = []
        self.investments = []
        self.used_teams = set()
        self._pool_seq = 500_000_000
        self._incident_seq = 900_000_000

    def next_pool_id(self):
        self._pool_seq += 7
        return self._pool_seq

    # -- match ---------------------------------------------------------------

    def add_match(self, index):
        rng, np_rng = self.rng, self.np
        match_id = 50_030_000 + index * 13

        code, league_name, country = LEAGUES[index % len(LEAGUES)]
        squad = [t for t in TEAMS[code] if t not in self.used_teams]
        if len(squad) < 2:
            squad = TEAMS[code][:]
        rng.shuffle(squad)
        home, away = squad[0], squad[1]
        self.used_teams.update((home, away))

        phase = PHASES[index % len(PHASES)]
        minute, kickoff = self._clock(phase)

        # Underlying truth for the whole match.
        strength = np_rng.normal(0.25, 0.75)
        truth = {
            "goal_tg": float(np.clip(np_rng.normal(2.65, 0.45), 1.6, 4.2)),
            "goal_sup": float(np.clip(strength, -2.0, 2.0)),
            "corner_tg": float(np.clip(np_rng.normal(10.2, 1.5), 6.5, 14.5)),
            "corner_sup": float(np.clip(strength * 1.7, -4.5, 4.5)),
        }
        fh_goal_split = float(np.clip(np_rng.normal(0.45, 0.02), 0.40, 0.50))
        fh_corner_split = float(np.clip(np_rng.normal(0.46, 0.02), 0.41, 0.51))

        incidents, state = self._simulate_play(match_id, truth, minute, phase, kickoff)
        self.events.extend(incidents)

        remaining = self._remaining_theta(truth, minute, phase,
                                          fh_goal_split, fh_corner_split)

        self.match_info.append(self._match_row(
            match_id, index, code, league_name, country, home, away,
            kickoff, state, phase))
        self.params.append(self._param_row(
            match_id, remaining, truth, minute, phase,
            fh_goal_split, fh_corner_split))

        self._add_book(match_id, home, away, remaining, state, phase, index)

    def _clock(self, phase):
        rng = self.rng
        if phase == "prematch":
            minute = 0.0
            kickoff = self.as_of + timedelta(minutes=rng.randint(6, 210))
        elif phase == "first_half":
            minute = float(rng.randint(8, 43))
            kickoff = self.as_of - timedelta(minutes=minute)
        elif phase == "half_time":
            minute = 45.0
            kickoff = self.as_of - timedelta(minutes=rng.randint(48, 57))
        else:
            minute = float(rng.randint(48, 87))
            kickoff = self.as_of - timedelta(minutes=minute + 15)
        return minute, kickoff

    def _simulate_play(self, match_id, truth, minute, phase, kickoff):
        """Walk the clock, emitting incidents at the true rates."""
        rng, np_rng = self.rng, self.np
        rows = []
        score = {"h": 0, "a": 0}
        corners = {"h": 0, "a": 0}
        ht_score = None
        ht_corners = None

        goal_h = (truth["goal_tg"] + truth["goal_sup"]) / 2 / 90.0
        goal_a = (truth["goal_tg"] - truth["goal_sup"]) / 2 / 90.0
        corner_h = (truth["corner_tg"] + truth["corner_sup"]) / 2 / 90.0
        corner_a = (truth["corner_tg"] - truth["corner_sup"]) / 2 / 90.0

        elapsed = int(minute)
        for m in range(1, elapsed + 1):
            when = kickoff + timedelta(minutes=m)
            for side, rate, kind in (
                ("h", max(goal_h, 0), config.INCIDENT_GOAL),
                ("a", max(goal_a, 0), config.INCIDENT_GOAL),
                ("h", max(corner_h, 0), config.INCIDENT_CORNER),
                ("a", max(corner_a, 0), config.INCIDENT_CORNER),
            ):
                if np_rng.random() < rate:
                    if kind == config.INCIDENT_GOAL:
                        score[side] += 1
                    else:
                        corners[side] += 1
                    rows.extend(self._incident_row(match_id, when, kind, side, m))
            if np_rng.random() < 0.035:
                rows.extend(self._incident_row(
                    match_id, when, config.INCIDENT_YELLOW,
                    "h" if np_rng.random() < 0.5 else "a", m))
            if np_rng.random() < 0.0016:
                rows.extend(self._incident_row(
                    match_id, when, config.INCIDENT_RED,
                    "h" if np_rng.random() < 0.5 else "a", m))
            if m == 45:
                ht_score = (score["h"], score["a"])
                ht_corners = (corners["h"], corners["a"])

        if phase in ("half_time", "second_half") and ht_score is None:
            ht_score = (score["h"], score["a"])
            ht_corners = (corners["h"], corners["a"])

        state = pricing.MatchState(
            home_score=score["h"], away_score=score["a"],
            ht_home=ht_score[0] if ht_score else None,
            ht_away=ht_score[1] if ht_score else None,
            home_corner=corners["h"], away_corner=corners["a"],
            ht_home_corner=ht_corners[0] if ht_corners else None,
            ht_away_corner=ht_corners[1] if ht_corners else None,
            minute=minute, phase=phase,
        )
        return rows, state

    def _incident_row(self, match_id, when, kind, side, game_minute):
        """Both providers report the incident, one of them a little late.

        Returned as a list because the reconciler downstream is only
        meaningful when the same incident arrives more than once; a
        provider occasionally misses a corner, which is exactly the
        disagreement the max-per-provider rule is there to survive.
        """
        rows = []
        for provider in (1, 2):
            if provider == 2 and kind == config.INCIDENT_CORNER and self.np.random() < 0.12:
                continue
            self._incident_seq += 1
            lag = timedelta(seconds=int(self.np.integers(0, 25))) if provider == 2 else timedelta(0)
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

    def _remaining_theta(self, truth, minute, phase, fh_goal, fh_corner):
        """Expectation still to come, plus the share landing in the first half."""
        if phase == "prematch":
            played = 0.0
        elif phase == "half_time":
            played = 45.0
        else:
            played = min(minute, 90.0)
        left = max(90.0 - played, 1.0)
        frac = left / 90.0

        if phase in ("prematch",):
            fh_goal_share, fh_corner_share = fh_goal, fh_corner
        elif phase == "first_half":
            fh_goal_share = max(45.0 - played, 0.0) / left
            fh_corner_share = fh_goal_share
        else:
            fh_goal_share = fh_corner_share = 0.0

        return {
            "goal_tg": truth["goal_tg"] * frac,
            "goal_sup": truth["goal_sup"] * frac,
            "corner_tg": truth["corner_tg"] * frac,
            "corner_sup": truth["corner_sup"] * frac,
            "fh_goal_share": fh_goal_share,
            "fh_corner_share": fh_corner_share,
            "played": played,
        }

    def _match_row(self, match_id, index, code, league_name, country,
                   home, away, kickoff, state, phase):
        ht_done = state.ht_done
        return {
            "match_id": match_id,
            "FrontendID": "{:04d}".format(1200 + index),
            "LeagueId": 40_000 + index,
            "LeagueName": league_name,
            "LeagueCode": code,
            "VenueId": 70_000 + index,
            "VenueName": "{} Stadium".format(home),
            "Country": country,
            "Season": "2026",
            "HomeName": home, "HomeId": 80_000 + index * 2,
            "AwayName": away, "AwayId": 80_001 + index * 2,
            "KOTime": kickoff,
            "HomeScoreFT": -1, "AwayScoreFT": -1,
            "HomeScoreHT": state.ht_home if ht_done else -1,
            "AwayScoreHT": state.ht_away if ht_done else -1,
            "HomeCornerFT": -1, "AwayCornerFT": -1,
            "HomeCornerHT": state.ht_home_corner if ht_done else -1,
            "AwayCornerHT": state.ht_away_corner if ht_done else -1,
            "HomeYellowFT": -1, "AwayYellowFT": -1,
            "HomeYellowHT": -1, "AwayYellowHT": -1,
            "HomeRedFT": -1, "AwayRedFT": -1,
            "HomeRedHT": -1, "AwayRedHT": -1,
            "IsVoid": False,
        }

    def _param_row(self, match_id, remaining, truth, minute, phase,
                   fh_goal, fh_corner):
        """algo_param, written under the remaining-expectancy reading."""
        np_rng = self.np
        state_name = {
            "prematch": "Prematch", "first_half": "FirstHalf",
            "half_time": "FirstHalf", "second_half": "SecondHalf",
        }[phase]

        # Noise shrinks with what is left to play: the parameters converge on
        # the truth as a match runs down, and a fixed spread would otherwise
        # hand the pricer a supremacy wider than the total it came from.
        frac = max(remaining["corner_tg"], 1e-6) / max(truth["corner_tg"], 1e-6)

        def layered(base, spread):
            """Basis is the pre-match anchor, Current the live trader number."""
            def draw(width):
                # Two sigma is as far as a trader's number ever drifts from
                # the book it is quoting; beyond that it is a data error.
                return base * (1 + float(np.clip(np_rng.normal(0, width),
                                                 -2 * width, 2 * width)))
            return draw(spread * 2.2), draw(spread * 1.4), draw(spread)

        def layered_sup(base, total, spread):
            """Same, but a difference can never outgrow its own total."""
            def draw(width):
                value = base + float(np.clip(np_rng.normal(0, width * frac),
                                             -2 * width * frac, 2 * width * frac))
                return float(np.clip(value, -total, total))
            return draw(spread * 2.2), draw(spread * 1.4), draw(spread)

        g_basis, g_model, g_current = layered(remaining["goal_tg"], 0.045)
        gs_basis, gs_model, gs_current = layered_sup(
            remaining["goal_sup"], remaining["goal_tg"], 0.07)
        c_basis, c_model, c_current = layered(remaining["corner_tg"], 0.06)
        cs_basis, cs_model, cs_current = layered_sup(
            remaining["corner_sup"], remaining["corner_tg"], 0.22)

        stamp = self.as_of - timedelta(seconds=int(self.rng.uniform(20, 260)))
        return {
            "match_id": match_id,
            "AlgoEventID": 600_000 + match_id % 100_000,
            "EventTime": stamp,
            "TimeInSecond": int(remaining["played"] * 60),
            "Minutes": int(remaining["played"]),
            "Seconds": 0,
            "IsClockSet": phase != "prematch",
            "GameState": state_name,
            "Goal90BasisTG": g_basis, "Goal90BasisSUP": gs_basis,
            "Goal90ModelTG": g_model, "Goal90ModelSUP": gs_model,
            "Goal90CurrentTG": g_current, "Goal90CurrentSUP": gs_current,
            "Goal90DrawFactor": 1.0,
            "Goal90SecondHalfSplitFH": remaining["fh_goal_share"],
            "Goal90SecondHalfSplitFT": 1.0 - remaining["fh_goal_share"],
            "Corner90BasisTG": c_basis, "Corner90BasisSUP": cs_basis,
            "Corner90ModelTG": c_model, "Corner90ModelSUP": cs_model,
            "Corner90CurrentTG": c_current, "Corner90CurrentSUP": cs_current,
            "Corner90DrawFactor": 1.0,
            "Corner90SecondHalfSplitFH": remaining["fh_corner_share"],
            "Corner90SecondHalfSplitFT": 1.0 - remaining["fh_corner_share"],
            "CreateTime": stamp,
        }

    # -- book ----------------------------------------------------------------

    def _add_book(self, match_id, home, away, remaining, state, phase, index):
        """Open pools, quote them, and put money through them."""
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

        plan = self._pool_plan(theta, state, phase)
        match_volume = self._match_volume(phase, index)

        for pool_code, lines in plan:
            pool = pool_defs.resolve(pool_code)
            pool_id = self.next_pool_id()
            self.active_pools.append({"match_id": match_id, "pool_id": pool_id})

            for line_id, line in enumerate(lines, start=1):
                market = model.market(pool, line)
                if not market:
                    continue
                keys = [k for k, v in market.items() if v > 1e-6]
                if not keys:
                    continue
                fairs = [pricing.fair_odds(market[k]) for k in keys]
                appetite = {k: float(np.clip(market[k], 0.02, 0.95)) ** 0.75
                            for k in keys}
                norm = sum(appetite.values()) or 1.0
                is_main = line_id == self._main_index(lines)
                for comb_id, (sel, fair) in enumerate(zip(keys, fairs), start=1):
                    self._quote(match_id, pool, pool_id, line_id, line,
                                comb_id, sel, fair, market[sel])
                    self._invest(match_id, pool, pool_id, line_id, comb_id,
                                 sel, appetite[sel] / norm, match_volume, is_main)

    def _main_index(self, lines):
        return (len(lines) // 2) + 1

    def _pool_plan(self, theta, state, phase):
        """Which pools are open and at which lines, given the match state."""
        rng = self.rng
        ht_done = state.ht_done

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
        if not ht_done:
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
        elif rng.random() < 0.35:
            # A handful of first-half pools linger past the break before the
            # feed closes them. The pipeline must mark these settled.
            plan.append(("FHAD", [None]))

        return plan

    def _match_volume(self, phase, index):
        """Turnover through this match in the trailing 5 minutes, in HKD."""
        base = {
            "prematch": 60_000, "first_half": 420_000,
            "half_time": 180_000, "second_half": 610_000,
        }[phase]
        tilt = 0.55 + 1.4 * self.np.random()
        marquee = 2.4 if index % 4 == 0 else 1.0
        return base * tilt * marquee

    def _quote(self, match_id, pool, pool_id, line_id, line,
               comb_id, sel, fair, prob):
        """Write the HKJC odds history plus the external book for one leg."""
        np_rng = self.np
        # HKJC's own belief drifts a little from ours; that gap is what the
        # MVP TrueProb rule is reading.
        belief = float(np.clip(prob * (1 + np_rng.normal(0, 0.012)), 1e-6, 0.999))
        true_odds = min(1.0 / belief, config.ODDS_CAP)
        offer = pricing.sell_odds(true_odds, pool.margin,
                                  1.0 + float(np_rng.normal(0, 0.008)))

        label = pool_defs.line_label(line) if line is not None else ""
        base = {
            "match_id": match_id,
            "bet_type_code": BET_TYPE_CODE.get(pool.code, 0),
            "pool_name": pool.code,
            "pool_id": pool_id,
            "line_id": line_id,
            "line_label": label,
            "combination_id": comb_id,
            "combination_string": sel,
        }

        # Six odds prints over the last half hour, drifting toward the
        # current number so the sparkline has a shape.
        for k in range(6, 0, -1):
            drift = np_rng.normal(0, 0.010) * k
            self.odds.append(dict(
                base,
                odds=pricing.ladder(max(offer * (1 + drift), config.ODDS_FLOOR)),
                true_odds=round(true_odds * (1 + drift * 0.8), 3),
                effective_datetime=self.as_of - timedelta(
                    minutes=5 * k, seconds=int(np_rng.integers(0, 240))),
            ))
        self.odds.append(dict(
            base, odds=offer, true_odds=round(true_odds, 3),
            effective_datetime=self.as_of - timedelta(
                seconds=int(np_rng.integers(5, 180))),
        ))

        if pool.code not in QUOTED_POOLS:
            return
        n_books = int(np_rng.integers(6, len(BOOKMAKERS) + 1))
        chosen = self.rng.sample(BOOKMAKERS, n_books)
        for code, name in chosen:
            # Sharper shops run thinner margins and quote longer.
            sharp = name in ("PinnacleSports", "Sbobet_com", "Matchbook")
            their_margin = pool.margin * (0.45 if sharp else np_rng.uniform(0.8, 1.25))
            price = pricing.ladder(
                true_odds / (1 + their_margin) * (1 + float(np_rng.normal(0, 0.014)))
            )
            self.market.append(dict(
                base,
                bookmaker=code,
                bookmaker_str=name,
                odds=price,
                effective_datetime=self.as_of - timedelta(
                    seconds=int(np_rng.integers(3, 600))),
            ))

    def _invest(self, match_id, pool, pool_id, line_id, comb_id, sel,
                sel_share, match_volume, is_main):
        """Turnover history for one selection across the trailing windows.

        ``sel_share`` is this leg's slice of its own line, already
        normalised - punters crowd the short side, and a little harder than
        the price alone would suggest.
        """
        np_rng = self.np
        share = POOL_WEIGHT.get(pool.code, 0.01)
        line_factor = 1.0 if is_main else 0.32
        base = match_volume * share * line_factor * sel_share

        if base < 40:
            return

        for window in config.TURNOVER_WINDOWS:
            decay = 1.0 + 0.06 * (window / 5.0 - 1.0)
            amount = base * decay * float(np_rng.uniform(0.55, 1.5))
            if amount < 15:
                continue
            # Most flow is retail; a thin tail of large tickets is what the
            # desk actually wants to spot.
            avg_stake = float(np_rng.lognormal(mean=5.6, sigma=0.55))
            if np_rng.random() < 0.06:
                avg_stake *= float(np_rng.uniform(4.0, 12.0))
            tickets = max(1, int(amount / max(avg_stake, 40.0)))
            when = (self.as_of - timedelta(minutes=window)
                    + timedelta(seconds=int(np_rng.integers(1, 299))))
            self.investments.append({
                "pool_id": pool_id,
                "pool_name": pool.code,
                "match_id": match_id,
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
            })
