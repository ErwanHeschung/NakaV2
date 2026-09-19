import tomllib
from pathlib import Path

_CONFIG = Path(__file__).resolve().parent.parent / "config"


def _load(name):
    with (_CONFIG / name).open("rb") as f:
        return tomllib.load(f)


_raw = _load("settings.toml")
VOICE = _load("voice.toml")

SERVER = _raw["server"]
LLM = _raw["llm"]
STT = _raw["stt"]
TTS = _raw["tts"]
AUDIO = _raw["audio"]
OPS = _raw.get("ops", {"idle_unload_minutes": 0, "idle_unload_llm": True})
