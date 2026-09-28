"""Config, read once at import and refreshable in place.

Every consumer holds a reference to one of the dicts below rather than copying
values out of it, so `reload()` mutates them in place: rebinding the names here
would leave `from .settings import VOICE` holding the old object, and the panel
could then save a setting that nothing ever reads.
"""

import tomllib
from pathlib import Path

from . import paths

# The person's copies, which the panel writes to.
SETTINGS_FILE = paths.CONFIG / "settings.toml"
VOICE_FILE = paths.CONFIG / "voice.toml"

# The shipped copies are the defaults, key for key. Reading them as the base
# and laying the person's file over the top means a setting added in a later
# version exists the moment it ships, with no migration: their file simply
# does not mention it, so the shipped value shows through. A hand-written
# defaults table here used to cover four of nine sections and none of
# voice.toml, while dsp.py indexes voice settings three levels deep — so every
# key missing from an older file was a KeyError waiting for an upgrade.
_SHIPPED_SETTINGS = paths.DEFAULTS / "settings.toml"
_SHIPPED_VOICE = paths.DEFAULTS / "voice.toml"

CLIENT: dict = {}
IDENTITY: dict = {}
SERVER: dict = {}
LLM: dict = {}
STT: dict = {}
TTS: dict = {}
AUDIO: dict = {}
LOGS: dict = {}
OPS: dict = {}
POWERS: dict = {}
# One table per connection, [connections.<name>]. Secrets are never here:
# see server/connections/secrets.py.
CONNECTIONS: dict = {}
VOICE: dict = {}


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def _merged(base: dict, over: dict) -> dict:
    """Recursive overlay: `over` wins, but only where it says something."""
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merged(out[key], value)
        else:
            out[key] = value
    return out


def reload() -> None:
    raw = _merged(_read(_SHIPPED_SETTINGS), _read(SETTINGS_FILE))
    sections = {
        "client": CLIENT, "identity": IDENTITY, "server": SERVER, "llm": LLM, "stt": STT,
        "tts": TTS, "audio": AUDIO, "logs": LOGS, "ops": OPS, "powers": POWERS,
        "connections": CONNECTIONS,
    }
    for name, target in sections.items():
        target.clear()
        target.update(raw.get(name, {}))

    VOICE.clear()
    VOICE.update(_merged(_read(_SHIPPED_VOICE), _read(VOICE_FILE)))


reload()


def save(changes: dict[str, object]) -> None:
    """Write dotted keys into the person's settings.toml, then reload.

    For values the server works out itself rather than ones typed into a
    field: a paired Telegram chat, a city's coordinates. Round-tripped with
    tomlkit, like the panel's saves, so the comments in the file survive.
    """
    import tomlkit

    doc = (tomlkit.parse(SETTINGS_FILE.read_text(encoding="utf-8"))
           if SETTINGS_FILE.exists() else tomlkit.document())
    for key, value in changes.items():
        *parents, last = key.split(".")
        node = doc
        for part in parents:
            if part not in node:
                node[part] = tomlkit.table()
            node = node[part]
        node[last] = value
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".toml.tmp")
    tmp.write_text(tomlkit.dumps(doc), encoding="utf-8")
    tmp.replace(SETTINGS_FILE)
    reload()
