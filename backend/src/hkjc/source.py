"""
Source Factory

Picks the data source from config. All three implementations expose the
same ``fetch(as_of, match_ids) -> Snapshot`` and ``describe()``.

    sql      live HKJC SQL Servers (production, inside the offline network)
    capture  parquet snapshot dumped by scripts/capture.py
    sim      coherent local simulator (default for development)

Change Log:
-----------
2026-08-30      Initialize
"""

from src.core import config
from src.core.exceptions import ExtractionError
from src.hkjc.capture_source import CaptureSource
from src.hkjc.simulator import SimSource
from src.hkjc.sql_source import SqlSource

_BUILDERS = {
    "sql": SqlSource,
    "capture": CaptureSource,
    "sim": SimSource,
}

# "hkjc" reads more naturally than "sql" when you are standing at the desk,
# and the package is called hkjc, so accept it rather than fail on a name
# that obviously means the live feed.
_ALIAS = {"hkjc": "sql", "live": "sql", "prod": "sql", "replay": "capture"}


def get_source(name=None, **kwargs):
    """Build the configured source.

    Args:
        name (str): Override for ``ALGOE_SOURCE``.
        **kwargs: Passed to the source constructor.
    """
    key = (name or config.DATA_SOURCE or "sim").strip().lower()
    key = _ALIAS.get(key, key)
    builder = _BUILDERS.get(key)
    if builder is None:
        raise ExtractionError(
            "unknown source '{}' - expected one of {}".format(
                key, ", ".join(sorted(set(_BUILDERS) | set(_ALIAS)))
            )
        )
    return builder(**kwargs)
