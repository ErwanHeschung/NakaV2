import tomllib
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.toml"

with _PATH.open("rb") as f:
    _raw = tomllib.load(f)

SERVER = _raw["server"]
LLM = _raw["llm"]
STT = _raw["stt"]
TTS = _raw["tts"]
AUDIO = _raw["audio"]
