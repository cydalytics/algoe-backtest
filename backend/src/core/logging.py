"""
Logging Helpers

Console logging for the pipeline. setup_logging() configures the root
logger; StepLogger prints one numbered step as a box and marks it
PASS / WARN / FAIL, mirroring the reference pipeline scripts.

Change Log:
-----------
2026-08-18      Initialize
"""

import logging
import time

LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
BOX_WIDTH = 92


def setup_logging(log_level: str = "INFO", log_file: str = None) -> None:
    """Configure the root logger (console + optional file).

    Args:
        log_level (str): Logging level name, e.g. "INFO", "DEBUG".
        log_file (str): Optional path to append logs to.
    """
    handlers = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format=LOG_FORMAT,
        handlers=handlers,
    )


def get_logger(name: str):
    """Return the module logger (call with __name__)."""
    return logging.getLogger(name)


def _print_box(lines):
    """Print a boxed panel, reference-pipeline style."""
    print("\u250c" + "\u2500" * (BOX_WIDTH - 2) + "\u2510")
    for line in lines:
        print("\u2502 {:<{}} \u2502".format(line, BOX_WIDTH - 4))
    print("\u2514" + "\u2500" * (BOX_WIDTH - 2) + "\u2518")


class StepLogger:
    """Print one pipeline step as a box, then mark it PASS / WARN / FAIL.

    Every event is also written to the "pipeline" logger so the run is
    traceable in logs and in the SQLite step timeline at the same time.
    """

    def __init__(self, step_no, title, subtitle=""):
        self.step_no = step_no
        self.title = title
        self.status = "running"
        self.detail = ""
        self.seconds = 0.0
        self._t0 = time.perf_counter()
        self._log = get_logger("pipeline")
        _print_box(["STEP {}  {}".format(step_no, title), subtitle])
        print()

    def ok(self, detail):
        """Mark the step as passed."""
        self.status = "ok"
        self.detail = detail
        self.seconds = time.perf_counter() - self._t0
        print("   [PASS]  {}   ({:.1f}s)\n".format(detail, self.seconds))
        self._log.info("STEP %s PASS: %s", self.step_no, detail)
        return self

    def warn(self, detail):
        """Attach a warning to the step (keeps status)."""
        self.detail = detail
        print("   [WARN]  {}\n".format(detail))
        self._log.warning("STEP %s WARN: %s", self.step_no, detail)
        return self

    def fail(self, detail):
        """Mark the step as failed."""
        self.status = "failed"
        self.detail = detail
        self.seconds = time.perf_counter() - self._t0
        print("   [FAIL]  {}   ({:.1f}s)\n".format(detail, self.seconds))
        self._log.error("STEP %s FAIL: %s", self.step_no, detail)
        return self