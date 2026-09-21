"""Start Naka from a checkout, with no console window.

Double-click it, or let the sign-in entry run it. The installed app is
Naka.exe instead; this is the same thing for development.
"""

import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
runpy.run_module("tray", run_name="__main__", alter_sys=True)
