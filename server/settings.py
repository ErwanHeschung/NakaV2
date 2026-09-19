import tomllib
from pathlib import Path

_CONFIG = Path(__file__).resolve().parent.parent / "config"


def _load(name):
    with (_CONFIG / name).open("rb") as f:
        return tomllib.load(f)


_raw = _load("settings.toml")
VOICE = _load("voice.toml")

IDENTITY = _raw.get("identity", {"assistant": "Naka", "user": "User",
                                 "user_pronoun": "they",
                                 "user_possessive": "their"})
SERVER = _raw["server"]
LLM = _raw["llm"]
STT = _raw["stt"]
TTS = _raw["tts"]
AUDIO = _raw["audio"]
LOGS = _raw.get("logs", {"retention_days": 0})
OPS = _raw.get("ops", {"idle_unload_minutes": 0, "idle_unload_llm": True})
