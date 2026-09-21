"""Can this machine run Naka at all? Asked before anything is downloaded.

Runs before torch exists, so it asks the driver through nvidia-smi rather
than asking CUDA. The deeper check — that torch really has kernels for this
card — comes later, once torch is installed (eval/check_gpu.py). This one is
about not starting a 20 GB download on a machine that cannot use it.

Naka refuses rather than falls back: without a suitable NVIDIA card every
reply would take tens of seconds, and someone meeting it that way would
reasonably conclude it is broken.
"""

import os
import subprocess
import sys
from pathlib import Path

# CUDA 12.8, which the torch build needs, requires driver 570 or newer.
MIN_DRIVER = 570
# Whisper and Kokoro, resident alongside the language model.
SPEECH_MB = 1700

_NVSMI = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) \
    / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe"
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

DRIVER_URL = "https://www.nvidia.com/drivers"


def probe() -> dict | None:
    """The first NVIDIA card's name, memory and driver, or None if none answers."""
    for exe in ("nvidia-smi", str(_NVSMI)):
        try:
            result = subprocess.run(
                [exe, "--query-gpu=name,memory.total,driver_version",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=15,
                creationflags=_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode != 0 or not result.stdout.strip():
            continue
        name, memory, driver = (part.strip() for part in
                                result.stdout.strip().splitlines()[0].split(","))
        return {"name": name, "vram_mb": int(float(memory)), "driver": driver}
    return None


def gate(models: list[dict]) -> dict:
    """A verdict for the setup screen: whether to continue, and why not.

    The reason is written for the person reading it, not for a log: what is
    wrong, and what would fix it.
    """
    card = probe()
    if card is None:
        return {"ok": False, "reason":
                "No NVIDIA graphics card was found, or its driver is not "
                "installed. Naka runs entirely on your own machine and needs "
                f"an NVIDIA card to do it. Drivers: {DRIVER_URL}"}

    try:
        major = int(card["driver"].split(".")[0])
    except ValueError:
        major = 0
    if major < MIN_DRIVER:
        return {**card, "ok": False, "reason":
                f"Your NVIDIA driver is version {card['driver']}. Naka needs "
                f"{MIN_DRIVER} or newer. Update it from {DRIVER_URL} and run "
                "setup again."}

    fits = [m["id"] for m in models
            if m.get("vram_mb", 0) + SPEECH_MB <= card["vram_mb"]]
    if not fits:
        smallest = min(m.get("vram_mb", 0) for m in models) + SPEECH_MB
        return {**card, "ok": False, "fits": [], "reason":
                f"Your {card['name']} has {card['vram_mb'] // 1024} GB of "
                f"memory. The smallest model Naka offers needs about "
                f"{-(-smallest // 1024)} GB."}

    return {**card, "ok": True, "fits": fits,
            "warning": None if card["vram_mb"] >= 16000 else
            "Naka will run, but a game will not fit alongside it — release "
            "the GPU from the tray before playing."}
