"""
Preflight

Run this first on the offline machine. It answers, in order, the questions
that actually block a deployment, and it stops being useful the moment it
starts guessing - so every check reports what it found rather than just
pass or fail.

    1. Is the Python new enough, and are the libraries importable
    2. Are the ODBC drivers installed, and which ones
    3. Do the three database connections open
    4. Do the notebook's queries return rows
    5. Does a full tick run end to end, and how long does it take

Nothing here writes to the database. The SQL checks are read-only and the
tick runs against whichever source is configured.

    python scripts/preflight.py                 # configured source
    python scripts/preflight.py --source sql    # force live SQL
    python scripts/preflight.py --skip-tick     # environment only

Exit code 0 means the desk can start. 1 means something listed as REQUIRED
failed; optional gaps are reported but do not fail the run.

Change Log:
-----------
2026-08-30      Initialize
"""

import argparse
import importlib
import os
import re
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OK, WARN, BAD = "  OK  ", " WARN ", " FAIL "

# (module, minimum version, required, what it is for)
PACKAGES = [
    ("pandas", (2, 0), True, "every dataframe in the pipeline"),
    ("numpy", (1, 24), True, "score grids"),
    ("scipy", (1, 10), True, "SLSQP - the optimizer"),
    ("fastapi", (0, 110), True, "the API"),
    ("uvicorn", (0, 27), True, "serving the API"),
    ("pyarrow", (14, 0), False, "parquet capture and replay"),
    ("pyodbc", (5, 0), False, "live SQL Server - not needed for sim"),
]


class Report:
    def __init__(self):
        self.failed = []
        self.warned = []

    def line(self, mark, name, detail=""):
        print("[{}] {:<28} {}".format(mark, name, detail))

    def ok(self, name, detail=""):
        self.line(OK, name, detail)

    def warn(self, name, detail=""):
        self.warned.append(name)
        self.line(WARN, name, detail)

    def bad(self, name, detail=""):
        self.failed.append(name)
        self.line(BAD, name, detail)

    def head(self, title):
        print("\n" + title)
        print("-" * 72)


def _version(mod):
    raw = getattr(mod, "__version__", "") or ""
    parts = []
    for chunk in raw.split(".")[:3]:
        digits = "".join(c for c in chunk if c.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return raw, tuple(parts)


# ---------------------------------------------------------------------------

def check_python(r: Report):
    r.head("Python")
    version = sys.version_info
    text = "{}.{}.{}  {}".format(version.major, version.minor, version.micro,
                                 sys.executable)
    if version < (3, 9):
        r.bad("python", text + "  (3.9 or newer required)")
    else:
        r.ok("python", text)

    # Binary wheels are built per interpreter version AND architecture, and
    # pip's message when they do not match names a filename rather than the
    # reason. Print the one tag this machine will accept so the wheels can
    # be built for it on the connected side without any guessing.
    import sysconfig
    platform = sysconfig.get_platform().replace("-", "_").replace(".", "_")
    tag = "cp{}{}-{}".format(version.major, version.minor, platform)
    r.ok("wheel tag", "{}   <- vendor wheels must carry this".format(tag))
    r.ok("build them with",
         ".\\package.ps1 -Wheels -PythonVersion {}{} -Platform {}".format(
             version.major, version.minor, platform))

    vendor = _vendor_dir()
    if vendor is None:
        return
    wheels = sorted(p.name for p in vendor.glob("*.whl"))
    if not wheels:
        r.warn("vendor/", "{} has no wheels in it".format(vendor))
        return
    # Pure-python wheels say py3 and work anywhere; only the compiled ones
    # carry a cpXY tag, and those are the ones that can be wrong.
    wrong = [w for w in wheels
             if re.search(r"-cp3\d+-", w)
             and "-cp{}{}-".format(version.major, version.minor) not in w]
    if wrong:
        r.bad("vendor/", "{} wheel(s) are for the wrong interpreter: {}".format(
            len(wrong), ", ".join(wrong[:4])))
    else:
        r.ok("vendor/", "{} wheels, all usable here".format(len(wheels)))


def _vendor_dir():
    """The bundle's vendor folder, whether run from the bundle or the repo."""
    here = Path(__file__).resolve()
    for parent in here.parents[:4]:
        candidate = parent / "vendor"
        if candidate.is_dir():
            return candidate
    return None


def check_packages(r: Report):
    r.head("Packages")
    for name, floor, required, purpose in PACKAGES:
        try:
            mod = importlib.import_module(name)
        except ImportError as exc:
            note = "{}  ({})".format(purpose, exc)
            (r.bad if required else r.warn)(
                name, "MISSING - " + note)
            continue
        raw, parsed = _version(mod)
        if parsed and parsed < floor:
            wanted = ".".join(str(p) for p in floor)
            r.warn(name, "{}  older than {} - {}".format(raw, wanted, purpose))
        else:
            r.ok(name, "{:<10} {}".format(raw or "?", purpose))


def check_drivers(r: Report):
    """ODBC is the usual reason a machine that ran the notebook cannot run
    this: pyodbc imports fine but no driver is registered for the user."""
    r.head("ODBC drivers")
    try:
        import pyodbc
    except ImportError:
        r.warn("pyodbc", "not installed - only sim and capture sources work")
        return
    try:
        drivers = list(pyodbc.drivers())
    except Exception as exc:                              # noqa: BLE001
        r.bad("pyodbc.drivers()", str(exc))
        return
    if not drivers:
        r.bad("odbc drivers", "none registered - install the SQL Server driver")
        return
    sql = [d for d in drivers if "SQL Server" in d]
    for d in drivers:
        r.ok("driver", d)
    if not sql:
        r.warn("odbc drivers", "no SQL Server driver among {}".format(
            ", ".join(drivers)))


def check_connections(r: Report, run_queries: bool):
    r.head("Database")
    from src.core import config

    targets = [
        ("QFM", config.SQL_CONN_QFM),
        ("BIS", config.SQL_CONN_BIS),
        ("BIS2", config.SQL_CONN_BIS2),
    ]
    try:
        import pyodbc
    except ImportError:
        for name, _ in targets:
            r.warn(name, "skipped - pyodbc not installed")
        return False

    live = {}
    for name, conn_str in targets:
        if not conn_str:
            r.warn(name, "no connection string configured")
            continue
        shown = conn_str.split("PWD=")[0].strip().rstrip(";")
        try:
            started = time.time()
            conn = pyodbc.connect(conn_str, timeout=10)
            cursor = conn.cursor()
            cursor.execute("SELECT @@SERVERNAME, DB_NAME()")
            server, database = cursor.fetchone()
            conn.close()
            r.ok(name, "{} / {}  in {:.1f}s".format(
                server, database, time.time() - started))
            live[name] = conn_str
        except Exception as exc:                          # noqa: BLE001
            r.bad(name, "{}  [{}]".format(
                str(exc).replace("\n", " ")[:150], shown))

    if not run_queries or not live:
        return bool(live)

    r.head("Queries")
    from src.hkjc import queries
    probes = [
        ("active pools", "QFM", queries.ACTIVE_POOLS),
    ]
    for label, target, sql in probes:
        if target not in live:
            r.warn(label, "skipped - {} did not connect".format(target))
            continue
        try:
            import pandas as pd
            conn = pyodbc.connect(live[target], timeout=30)
            frame = pd.read_sql(sql, conn)
            conn.close()
            if frame.empty:
                r.warn(label, "connected but returned 0 rows - no live matches?")
            else:
                r.ok(label, "{} rows, {} matches".format(
                    len(frame),
                    frame["match_id"].nunique() if "match_id" in frame else "?"))
        except Exception as exc:                          # noqa: BLE001
            r.bad(label, str(exc).replace("\n", " ")[:200])
    return True


def check_config(r: Report, source):
    r.head("Configuration")
    from src.core import config

    r.ok("data source", source or config.DATA_SOURCE)
    r.ok("true prob", "1 / HKJC true odds")
    r.ok("turnover", "next 5 min = last 5 min")
    r.ok("optimized pools", "{} of them".format(len(config.OPTIMIZED_POOLS)))
    r.ok("theta semantics", config.THETA_SEMANTICS)
    r.ok("sqlite", str(config.DB_PATH))

    parent = os.path.dirname(str(config.DB_PATH)) or "."
    if not os.access(parent, os.W_OK):
        r.bad("sqlite", "cannot write to {}".format(parent))


def check_tick(r: Report, source):
    r.head("Pipeline")
    from src.pipeline import Pipeline

    try:
        started = time.time()
        tick = Pipeline(source=source).run()
        elapsed = time.time() - started
    except Exception as exc:                              # noqa: BLE001
        r.bad("tick", "{}: {}".format(type(exc).__name__, exc))
        traceback.print_exc()
        return

    matches = len(tick.contexts)
    rows = len(tick.frame.selections)
    if matches == 0:
        r.warn("tick", "ran in {:.1f}s but found no matches".format(elapsed))
        return

    r.ok("tick", "{} matches, {} selections in {:.1f}s".format(
        matches, rows, elapsed))
    for stage, seconds in tick.timings.items():
        r.ok("  " + stage, "{:.2f}s".format(seconds))

    totals = tick.totals
    r.ok("turnover", "{:,.0f}".format(totals.get("turnover", 0) or 0))
    r.ok("expected GM", "{:,.0f}".format(totals.get("gm_now", 0) or 0))
    uplift = totals.get("uplift")
    if uplift is not None:
        r.ok("uplift", "{:,.0f}  ({:.0f} bps)".format(
            uplift, totals.get("uplift_bps", 0) or 0))

    for warning in tick.warnings[:10]:
        r.warn("tick warning", warning)
    if len(tick.warnings) > 10:
        r.warn("tick warnings", "... and {} more".format(len(tick.warnings) - 10))


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Check this machine can run Algo E")
    ap.add_argument("--source", default=None,
                    help="sim, capture or sql (default: configured)")
    ap.add_argument("--skip-tick", action="store_true",
                    help="environment checks only")
    ap.add_argument("--skip-sql", action="store_true",
                    help="do not try the database")
    args = ap.parse_args()

    print("=" * 72)
    print("Algo E preflight")
    print("=" * 72)

    r = Report()
    check_python(r)
    check_packages(r)

    if not args.skip_sql:
        check_drivers(r)
        live = (args.source or "").lower() in ("sql", "hkjc", "live")
        check_connections(r, run_queries=live)

    try:
        check_config(r, args.source)
    except Exception as exc:                              # noqa: BLE001
        r.bad("configuration", str(exc))

    if not args.skip_tick:
        check_tick(r, args.source)

    print("\n" + "=" * 72)
    if r.failed:
        print("NOT READY - {} required check(s) failed:".format(len(r.failed)))
        for name in r.failed:
            print("  - {}".format(name))
        if r.warned:
            print("plus {} warning(s)".format(len(r.warned)))
        print("=" * 72)
        return 1
    if r.warned:
        print("READY, with {} warning(s):".format(len(r.warned)))
        for name in dict.fromkeys(r.warned):
            print("  - {}".format(name))
    else:
        print("READY - everything checks out")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
