"""
Algo E MVP - Entry Point

Thin wrapper that runs the pipeline script (backend/scripts/main.py).
Keeping `python main.py` working from this folder is the meeting-demo
convention; the script itself lives with the other scripts.

Change Log:
-----------
2026-08-18      Initialize
"""

import runpy
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "backend" / "scripts" / "main.py"

if __name__ == "__main__":
    sys.argv[0] = str(SCRIPT)
    runpy.run_path(str(SCRIPT), run_name="__main__")