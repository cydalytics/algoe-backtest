"""
Wide Fact Book

One row per (tick, selection). Labels and slice dimensions sit once;
each lab writes its forecast onto a named column so the workbench can
compose a stack without walking the meeting again.

    t_hat__{model}              next-bucket demand
    p__{source}__{calibrator}   calibrated true probability
    odds_star__{algo}           posted or solved sell odds

Expected GM on a composed stack is honest at the current offer:
t_hat_selected × (1 − p_selected × sell_odds). Solver odds are those
produced on the MVP stack — they are a price path, not a re-solve.

Change Log:
-----------
2026-09-12      Initialize (W06 workbench)
"""

from typing import Iterable, List, Optional

import pandas as pd

from .dimensions import DIM_COLS, FILTER_COLS

KEY = ("as_of", "label_key")

BASE_COLS = list(DIM_COLS) + [
    "t_actual", "t_actual_tickets", "result",
    "t5m", "tickets5m", "sell_odds", "hkjc_odds", "hkjc_true_odds",
    "book_sum", "invested",
]


class FactBook:
    """Collects scored columns from independent labs into one wide table."""

    def __init__(self):
        self.base: Optional[pd.DataFrame] = None
        self._extra: dict = {}
        self.turnover: List[str] = []
        self.beliefs: List[str] = []   # "source/cal"
        self.algos: List[str] = []

    def ingest_base(self, labeled: Iterable) -> None:
        pieces = []
        for frame in labeled:
            sel = frame.selections
            if sel is None or sel.empty:
                continue
            keep = [c for c in BASE_COLS if c in sel.columns]
            pieces.append(sel[keep].copy())
        if not pieces:
            self.base = pd.DataFrame()
            return
        base = pd.concat(pieces, ignore_index=True)
        if all(c in base.columns for c in KEY):
            base = base.drop_duplicates(list(KEY), keep="last")
        self.base = base

    def add_posted(self, scored: pd.DataFrame) -> None:
        """HKJC/model posted offer lives on the scored tick, not the label."""
        for col in ("sell_odds", "t5m", "tickets5m", "book_sum", "invested"):
            if col not in scored.columns:
                continue
            if self.base is not None and col in self.base.columns and self.base[col].notna().any():
                continue
            self._put(scored, col, col)

    def add_turnover(self, scored: pd.DataFrame, name: str) -> None:
        self.add_posted(scored)
        col = t_hat_col(name)
        self._put(scored, "t_hat", col)
        if name not in self.turnover:
            self.turnover.append(name)

    def add_belief(self, scored: pd.DataFrame, source: str, calibrator: str) -> None:
        col = p_col(source, calibrator)
        self._put(scored, "true_prob", col)
        key = "{}/{}".format(source, calibrator)
        if key not in self.beliefs:
            self.beliefs.append(key)

    def add_algo(self, scored: pd.DataFrame, algo: str) -> None:
        self._put(scored, "sell_odds_star", odds_star_col(algo))
        if "t_hat_star" in scored.columns:
            self._put(scored, "t_hat_star", t_hat_star_col(algo))
        if "t_actual_star" in scored.columns:
            self._put(scored, "t_actual_star", t_actual_star_col(algo))
        if algo not in self.algos:
            self.algos.append(algo)

    def _put(self, scored: pd.DataFrame, src: str, dest: str) -> None:
        if scored is None or scored.empty or src not in scored.columns:
            return
        missing = [c for c in KEY if c not in scored.columns]
        if missing:
            return
        piece = scored[list(KEY) + [src]].copy()
        piece = piece.rename(columns={src: dest})
        piece = piece.drop_duplicates(list(KEY), keep="last")
        self._extra[dest] = piece.set_index(list(KEY))[dest]

    def build(self) -> pd.DataFrame:
        if self.base is None or self.base.empty:
            return pd.DataFrame()
        out = self.base.copy()
        if self._extra:
            out = out.set_index(list(KEY))
            for col, series in self._extra.items():
                out[col] = series
            out = out.reset_index()
        return out

    def catalog(self, frame=None) -> dict:
        frame = self.base if frame is None else frame
        facets = {}
        if frame is not None and not frame.empty:
            for col in FILTER_COLS:
                if col not in frame.columns:
                    continue
                values = sorted({str(v) for v in frame[col].dropna().unique()})
                facets[col] = values
        return {
            "turnover": list(self.turnover),
            "beliefs": list(self.beliefs),
            "algos": list(self.algos),
            "filters": facets,
        }


def t_hat_col(name: str) -> str:
    return "t_hat__{}".format(name)


def p_col(source: str, calibrator: str) -> str:
    return "p__{}__{}".format(source, calibrator)


def odds_star_col(algo: str) -> str:
    return "odds_star__{}".format(algo)


def t_hat_star_col(algo: str) -> str:
    return "t_hat_star__{}".format(algo)


def t_actual_star_col(algo: str) -> str:
    return "t_actual_star__{}".format(algo)


def parse_belief(token: str):
    """'hkjc_true/raw' → (source, calibrator)."""
    if "/" in token:
        source, cal = token.split("/", 1)
        return source, cal
    return token, "raw"


def discover_models(frame: pd.DataFrame) -> dict:
    turnover, beliefs, algos = [], [], []
    for col in frame.columns:
        if col.startswith("t_hat__") and not col.startswith("t_hat_star"):
            turnover.append(col[len("t_hat__"):])
        elif col.startswith("p__"):
            rest = col[len("p__"):]
            if "__" in rest:
                source, cal = rest.split("__", 1)
                beliefs.append("{}/{}".format(source, cal))
        elif col.startswith("odds_star__"):
            algos.append(col[len("odds_star__"):])
    return {
        "turnover": turnover,
        "beliefs": beliefs,
        "algos": algos or ["hold"],
    }
