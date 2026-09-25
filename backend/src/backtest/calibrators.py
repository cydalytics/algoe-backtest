"""
Belief Calibrators

A true-odds *source* is where the number came from. A calibrator is a
walk-forward map that tries to make that number mean what it says.

    raw          leave p alone
    shrink       pull toward the realised base rate seen so far
    temperature  stretch / flatten logits (one T, fit on log-loss)
    isotonic     pool-adjacent-violators on (p, y)
    normalize    force each exhaustive line to sum to 1

Every fit uses only earlier ticks. The first tick is always raw.

Change Log:
-----------
2026-09-11      Initialize (W06 backtest labs)
"""

from typing import List, Optional

import numpy as np
import pandas as pd

from src.models.true_prob import EXHAUSTIVE_KINDS, LINE_KEYS


CALIBRATORS = ("raw", "shrink", "temperature", "isotonic", "normalize")

CALIBRATOR_LABELS = {
    "raw": "Uncalibrated",
    "shrink": "Shrink to base rate",
    "temperature": "Temperature scale",
    "isotonic": "Isotonic (PAV)",
    "normalize": "Renormalise the book",
}


def make_calibrator(name: str):
    if name not in CALIBRATORS:
        raise ValueError("unknown calibrator '{}'".format(name))
    return {
        "raw": RawCalibrator,
        "shrink": ShrinkCalibrator,
        "temperature": TemperatureCalibrator,
        "isotonic": IsotonicCalibrator,
        "normalize": NormalizeCalibrator,
    }[name]()


class RawCalibrator:
    name = "raw"

    def fit(self, p, y):
        return self

    def transform(self, p):
        return np.asarray(p, dtype=float)

    def apply(self, sel: pd.DataFrame) -> pd.DataFrame:
        sel["true_prob_cal"] = self.name
        return sel


class ShrinkCalibrator:
    """p' = λ p + (1-λ) ȳ. λ stays high so we do not flatten the book."""

    name = "shrink"

    def __init__(self, weight=0.75):
        self.weight = float(weight)
        self.rate = 0.5
        self.fitted = False

    def fit(self, p, y):
        y = np.asarray(y, dtype=float)
        if len(y):
            self.rate = float(np.clip(np.mean(y), 0.02, 0.98))
            self.fitted = True
        return self

    def transform(self, p):
        p = np.asarray(p, dtype=float)
        if not self.fitted:
            return p
        return np.clip(self.weight * p + (1.0 - self.weight) * self.rate, 1e-6, 1 - 1e-6)

    def apply(self, sel: pd.DataFrame) -> pd.DataFrame:
        p = pd.to_numeric(sel.get("true_prob"), errors="coerce")
        sel["true_prob"] = self.transform(p.to_numpy())
        sel["true_prob_cal"] = self.name
        return sel


class TemperatureCalibrator:
    """p' = σ(logit(p) / T). T>1 flattens; T<1 sharpens."""

    name = "temperature"

    def __init__(self):
        self.T = 1.0
        self.fitted = False

    def fit(self, p, y):
        p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
        y = np.asarray(y, dtype=float)
        if len(p) < 20:
            return self
        best_t, best_ll = 1.0, 1e9
        for T in np.linspace(0.45, 2.2, 18):
            q = _temperature(p, T)
            ll = float(-np.mean(y * np.log(q) + (1.0 - y) * np.log(1.0 - q)))
            if ll < best_ll:
                best_t, best_ll = float(T), ll
        self.T = best_t
        self.fitted = True
        return self

    def transform(self, p):
        p = np.asarray(p, dtype=float)
        if not self.fitted:
            return p
        return _temperature(p, self.T)

    def apply(self, sel: pd.DataFrame) -> pd.DataFrame:
        p = pd.to_numeric(sel.get("true_prob"), errors="coerce")
        sel["true_prob"] = self.transform(p.to_numpy())
        sel["true_prob_cal"] = self.name
        sel["cal_T"] = self.T
        return sel


class IsotonicCalibrator:
    """Monotone map p → P(y=1 | p), fit with pool-adjacent violators."""

    name = "isotonic"

    def __init__(self):
        self.x_: Optional[np.ndarray] = None
        self.y_: Optional[np.ndarray] = None

    def fit(self, p, y):
        p = np.asarray(p, dtype=float)
        y = np.asarray(y, dtype=float)
        mask = np.isfinite(p) & np.isfinite(y)
        p, y = p[mask], y[mask]
        if len(p) < 30:
            return self
        fitted = _pav(p, y)
        order = np.argsort(p, kind="mergesort")
        xs, ys = p[order], fitted[order]
        # collapse duplicate x so interp is well-defined
        uniq, idx = np.unique(xs, return_index=True)
        self.x_ = uniq
        self.y_ = ys[idx]
        return self

    def transform(self, p):
        p = np.asarray(p, dtype=float)
        if self.x_ is None or len(self.x_) < 2:
            return p
        return np.clip(np.interp(p, self.x_, self.y_), 1e-6, 1 - 1e-6)

    def apply(self, sel: pd.DataFrame) -> pd.DataFrame:
        p = pd.to_numeric(sel.get("true_prob"), errors="coerce")
        sel["true_prob"] = self.transform(p.to_numpy())
        sel["true_prob_cal"] = self.name
        return sel


class NormalizeCalibrator:
    """Rescale exhaustive lines onto 1. No history, no leakage."""

    name = "normalize"

    def fit(self, p, y):
        return self

    def transform(self, p):
        return np.asarray(p, dtype=float)

    def apply(self, sel: pd.DataFrame) -> pd.DataFrame:
        p = pd.to_numeric(sel.get("true_prob"), errors="coerce")
        keys = [k for k in LINE_KEYS if k in sel.columns]
        if keys:
            work = sel.loc[:, keys].copy()
            work["_p"] = p.to_numpy()
            book = work.groupby(keys, sort=False)["_p"].transform("sum")
            book.index = sel.index
        else:
            book = pd.Series(1.0, index=sel.index)
        kind = sel["kind"] if "kind" in sel.columns else pd.Series("", index=sel.index)
        scaled = p / book.where(book > 0)
        sel["true_prob"] = scaled.where(kind.isin(EXHAUSTIVE_KINDS), p)
        sel["true_prob_cal"] = self.name
        return sel


def _temperature(p, T):
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    logit = np.log(p) - np.log(1.0 - p)
    z = logit / max(float(T), 1e-6)
    return 1.0 / (1.0 + np.exp(-z))


def _pav(x, y) -> np.ndarray:
    """Non-decreasing fit of y against x (pool adjacent violators)."""
    order = np.argsort(x, kind="mergesort")
    ys = np.asarray(y, dtype=float)[order]
    blocks: List[List[float]] = []
    for value in ys:
        blocks.append([float(value), 1.0])
        while len(blocks) >= 2:
            mean_prev = blocks[-2][0] / blocks[-2][1]
            mean_cur = blocks[-1][0] / blocks[-1][1]
            if mean_prev <= mean_cur + 1e-15:
                break
            s = blocks[-2][0] + blocks[-1][0]
            c = blocks[-2][1] + blocks[-1][1]
            blocks.pop()
            blocks[-1] = [s, c]
    fitted = []
    for s, c in blocks:
        fitted.extend([s / c] * int(c))
    out = np.empty(len(y), dtype=float)
    out[order] = np.asarray(fitted, dtype=float)
    return out
