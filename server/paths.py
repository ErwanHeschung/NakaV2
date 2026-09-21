"""Where everything lives. The only module that knows.

Two roots, because the panel writes back to its own config:

  APP   what the installer ships and replaces wholesale on upgrade — the code,
        the built panel, and the default config. Never written to at runtime.
  DATA  what belongs to the person — their settings, facts, logs, models, the
        downloaded runtime. Survives upgrades and, unless they ask, uninstall.

Everything used to resolve from this file's parent, which assumed the process
ran from a checkout that it could also scribble in. An installed copy lives
somewhere read-only-in-spirit, and an upgrade that replaced settings.toml would
throw away every change made in the panel.

This module imports nothing from the project: settings imports it, not the
other way round, so it is safe to import first from anywhere.
"""

import os
import shutil
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def _data_default() -> Path:
    # Local, never Roaming: this directory holds 20+ GB of models and runtime,
    # and a roaming profile would try to sync it.
    local = os.environ.get("LOCALAPPDATA")
    return Path(local) / "Naka" if local else _REPO / ".data"


APP = Path(os.environ.get("NAKA_APP_DIR") or _REPO)
DATA = Path(os.environ.get("NAKA_DATA_DIR") or _data_default())

DEFAULTS = APP / "config"
UI = APP / "ui" / "public"

CONFIG = DATA / "config"
LOGS = DATA / "logs"
MODELS = DATA / "models"
RUNTIME = DATA / "runtime"
CACHE = DATA / "cache"
STATE = DATA / "state.json"

# The files the panel and the reconciler write to. Each is seeded from the
# shipped default the first time, then left alone for good.
SEEDED = ("settings.toml", "voice.toml", "persona.md", "facts.json", "tools.yaml")

# Pinned at import, which is the earliest point anything can reach the network.
# Whisper and Kokoro fetch their weights from HuggingFace on first use unless
# told not to; setup downloads them deliberately, and at runtime a missing file
# should fail loudly rather than quietly pull 1.6 GB mid-conversation.
os.environ.setdefault("HF_HOME", str(CACHE / "hf"))
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ.setdefault("TQDM_DISABLE", "1")


def seed_config() -> list[str]:
    """Copy each default into the data directory if it is not there yet.

    Never overwrites. That is the whole upgrade story for config: a new version
    ships new defaults, and they only land for files the person never had.
    Keys added to an existing file are handled by the defaults in settings.py,
    not by rewriting what they already have.
    """
    CONFIG.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    seeded = []
    for name in SEEDED:
        target = CONFIG / name
        source = DEFAULTS / name
        if not target.exists() and source.exists():
            shutil.copyfile(source, target)
            seeded.append(name)
    return seeded


SEEDED_NOW = seed_config()
