"""
Plot Payloads

Every chart the UI draws is a JSON blob produced here. The frontend
does not bin, residualise or rank - it just paints. Caps keep a stored
report from becoming a dump of every selection.

Change Log:
-----------
2026-09-11      Initialize (W06 backtest labs)
"""

from typing import Iterable, List, Sequence

import numpy as np
import pandas as pd

from src.core import config


def sample_xy(x, y, n=None, seed=0) -> List[dict]:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    x, y = x[mask], y[mask]
    n = n or config.BACKTEST_PLOT_POINTS
    if len(x) > n:
        rng = np.random.default_rng(seed)
        idx = rng.choice(len(x), n, replace=False)
        x, y = x[idx], y[idx]
    return [{"x": round(float(a), 4), "y": round(float(b), 4)} for a, b in zip(x, y)]


def bars(items: Iterable, value_key="value") -> List[dict]:
    out = []
    for item in items:
        if isinstance(item, dict):
            row = dict(item)
            if value_key in row and row[value_key] is not None:
                row[value_key] = _num(row[value_key])
            out.append(row)
        else:
            key, value = item
            out.append({"key": str(key), "value": _num(value)})
    return out


def overlay_hist(a, b, bins=24) -> List[dict]:
    """Shared-edge histograms so two stacks can be drawn on one axis."""
    a = _finite(a)
    b = _finite(b)
    if len(a) == 0 and len(b) == 0:
        return []
    lo = float(min(a.min() if len(a) else b.min(), b.min() if len(b) else a.min()))
    hi = float(max(a.max() if len(a) else b.max(), b.max() if len(b) else a.max()))
    if lo == hi:
        lo, hi = lo - 1.0, hi + 1.0
    edges = np.linspace(lo, hi, bins + 1)
    ca, _ = np.histogram(a, bins=edges)
    cb, _ = np.histogram(b, bins=edges)
    na = max(len(a), 1)
    nb = max(len(b), 1)
    return [
        {
            "lo": round(float(edges[i]), 4),
            "hi": round(float(edges[i + 1]), 4),
            "a": int(ca[i]),
            "b": int(cb[i]),
            "a_density": round(float(ca[i] / na), 6),
            "b_density": round(float(cb[i] / nb), 6),
        }
        for i in range(len(ca))
    ]


def ecdf(values, n=40) -> List[dict]:
    v = _finite(values)
    if len(v) == 0:
        return []
    qs = np.linspace(0.0, 1.0, n)
    xs = np.quantile(v, qs)
    return [{"x": round(float(x), 4), "y": round(float(q), 4)} for x, q in zip(xs, qs)]


def qq(a, b, n=40) -> List[dict]:
    a = _finite(a)
    b = _finite(b)
    if len(a) == 0 or len(b) == 0:
        return []
    qs = np.linspace(0.02, 0.98, n)
    return [
        {"x": round(float(x), 4), "y": round(float(y), 4)}
        for x, y in zip(np.quantile(a, qs), np.quantile(b, qs))
    ]


def _finite(values):
    v = np.asarray(values, dtype=float)
    return v[np.isfinite(v)]


def hist(values, bins=20) -> List[dict]:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) == 0:
        return []
    counts, edges = np.histogram(values, bins=bins)
    return [
        {
            "lo": round(float(edges[i]), 4),
            "hi": round(float(edges[i + 1]), 4),
            "n": int(counts[i]),
        }
        for i in range(len(counts))
    ]


def series(xs: Sequence, ys: Sequence, keys=None) -> List[dict]:
    out = []
    for i, (x, y) in enumerate(zip(xs, ys)):
        row = {"x": x if not _is_num(x) else _num(x), "y": _num(y)}
        if keys is not None:
            row["key"] = str(keys[i])
        out.append(row)
    return out


def bins_xy(x, y, bins=12) -> List[dict]:
    """Mean y against equal-count bins of x (residual / reliability helper)."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    x, y = x[mask], y[mask]
    if len(x) < max(bins, 4):
        return []
    try:
        labels = pd.qcut(x, bins, labels=False, duplicates="drop")
    except ValueError:
        return []
    out = []
    for i in sorted(set(int(v) for v in labels if pd.notna(v))):
        take = labels == i
        out.append({
            "x": round(float(x[take].mean()), 4),
            "y": round(float(y[take].mean()), 4),
            "n": int(take.sum()),
            "lo": round(float(x[take].min()), 4),
            "hi": round(float(x[take].max()), 4),
        })
    return out


def heatmap(rows: Sequence[str], cols: Sequence[str], values) -> dict:
    grid = []
    for i, row in enumerate(rows):
        for j, col in enumerate(cols):
            grid.append({
                "row": str(row),
                "col": str(col),
                "value": _num(values[i][j]) if values[i][j] is not None else None,
            })
    return {"rows": list(rows), "cols": list(cols), "cells": grid}


def _num(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return round(float(v), 6)


def _is_num(v):
    return isinstance(v, (int, float, np.floating, np.integer)) and not isinstance(v, bool)
