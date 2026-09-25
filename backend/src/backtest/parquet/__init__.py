"""Parquet backtest engine.

Reads the raw HKJC parquet folders written by "1 Data Extraction.ipynb",
builds the 5-minute selection-bucket panel, and scores it. Used two ways:

    * as a package by the API (incremental per-day cache, see ``cache.py``)
    * as a script in the offline bundle: ``python -m backtest.run run ...``

Both paths share this one copy of the code, so a fix lands in both.

Change Log:
-----------
2026-09-15      Initialize (moved from parquet_backtest/, relative imports)
"""

from .config import DEFAULT_MARGINS, DEFAULT_POOLS, SUBDIRS, TIME_COL, Config

__all__ = ["Config", "SUBDIRS", "TIME_COL", "DEFAULT_POOLS", "DEFAULT_MARGINS"]
