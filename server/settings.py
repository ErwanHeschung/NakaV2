"""Config, read once at import and refreshable in place.

Every consumer holds a reference to one of the dicts below rather than copying
values out of it, so `reload()` mutates them in place: rebinding the names here
would leave `from .settings import VOICE` holding the old object, and the panel
could then save a setting that nothing ever reads.
"""

import tomllib
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "config"

SETTINGS_FILE = CONFIG / "settings.toml"
VOICE_FILE = CONFIG / "voice.toml"

_DEFAULTS = {
    "identity": {"assistant": "Naka", "user": "User", "user_pronoun": "they",
                 "user_possessive": "their"},
    "logs": {"retention_days": 0},
    "ops": {"idle_unload_minutes": 0, "idle_unload_llm": True},
}

IDENTITY: dict = {}
SERVER: dict = {}
LLM: dict = {}
STT: dict = {}
TTS: dict = {}
AUDIO: dict = {}
LOGS: dict = {}
OPS: dict = {}
VOICE: dict = {}


def _read(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def reload() -> None:
    raw = _read(SETTINGS_FILE)
    sections = {
        "identity": IDENTITY, "server": SERVER, "llm": LLM, "stt": STT,
        "tts": TTS, "audio": AUDIO, "logs": LOGS, "ops": OPS,
    }
    for name, target in sections.items():
        target.clear()
        target.update(_DEFAULTS.get(name, {}))
        target.update(raw.get(name, {}))

    VOICE.clear()
    VOICE.update(_read(VOICE_FILE))


reload()
