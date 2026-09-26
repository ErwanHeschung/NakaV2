"""Endpoints that exist for the control panel rather than for the voice loop.

Notes and timers are already reachable as tools, but a tool answers in prose
because that is what goes back to the model. The panel needs the same state as
data, and needs to change it without going through a conversation.

Settings are edited through an explicit field list rather than by writing
arbitrary TOML. Two reasons: the panel can render a real control per field
because it knows the type and range, and nothing outside the list can be
reached, so a malformed save cannot take the server down with it.
"""

import logging
import re
import time
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path

import tomlkit
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import autostart, connections, events, paths, settings
from .memory import memory
from .tools import builtin, reminders
from .tools.builtin import validate_fact
from .tools.registry import FACTS

log = logging.getLogger("naka.panel")

router = APIRouter()

# KeyboardEvent.code values are identifiers — ControlRight, F13, KeyT, Digit4 —
# so the shape can be checked without keeping a list of every key in existence.
_KEY_CODE = re.compile(r"[A-Za-z][A-Za-z0-9]{0,31}")


@router.get("/client")
async def client_config():
    """What a client needs before it can listen. Small and cheap on purpose:
    the panel re-reads it whenever the settings are saved."""
    return {
        "push_to_talk_key": settings.CLIENT["push_to_talk_key"],
        "listen_when_open": settings.CLIENT["listen_when_open"],
        "sample_rate": settings.TTS["sample_rate"],
        # The panel's own choice, not the server's default. default_agentic in
        # tools.yaml decides what a request that says nothing gets; every real
        # client says, and both of these now say yes.
        "agentic": settings.CLIENT["use_tools"],
    }


class ClientStateIn(BaseModel):
    state: str


@router.post("/client/state")
async def client_state(body: ClientStateIn):
    """The tray's push-to-talk state, relayed to any open panel.

    The tray owns the hotkey and the microphone, so without this the panel's
    orb would sit still while someone was plainly talking to it — the window
    and the key would feel like two different apps.
    """
    if body.state not in ("ready", "listening", "thinking", "speaking", "off"):
        raise HTTPException(status_code=400, detail="unknown state")
    events.publish("client", state=body.state)
    return {"ok": True}


@router.post("/client/show")
async def client_show():
    """Ask the tray to bring its window forward. A second launch uses this."""
    events.publish("client", show=True)
    return {"ok": True}


# ------------------------------------------------------------------- notes


class NoteIn(BaseModel):
    content: str
    name: str | None = None


def _note_view(path: Path, body: str | None = None) -> dict:
    text = body if body is not None else path.read_text(encoding="utf-8")
    return {
        "name": path.stem,
        "modified": path.stat().st_mtime,
        "bytes": path.stat().st_size,
        "preview": " ".join(text.split())[:120],
    }


@router.get("/notes")
async def notes_list():
    return {"notes": [_note_view(p) for p in builtin.note_files()],
            "directory": str(builtin.NOTES_DIR)}


@router.get("/notes/{name}")
async def note_read(name: str):
    path = _resolve(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"no note called {name!r}")
    return _note_view(path) | {"content": path.read_text(encoding="utf-8")}


@router.put("/notes/{name}")
async def note_write(name: str, body: NoteIn):
    """Replace the note. The write_note tool appends instead — the model is
    adding to what it knows, whereas someone editing in the panel means the
    text in front of them to be what the file says afterwards."""
    path = _resolve(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body.content, encoding="utf-8")
    log.info("note %r saved from the panel (%d bytes)", path.stem, len(body.content))
    events.publish("notes")
    return _note_view(path, body.content)


@router.delete("/notes/{name}")
async def note_delete(name: str):
    path = _resolve(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"no note called {name!r}")
    path.unlink()
    log.info("note %r deleted from the panel", path.stem)
    events.publish("notes")
    return {"status": "deleted", "name": path.stem}


def _resolve(name: str) -> Path:
    """Same sanitising as the tools use, so the panel cannot reach further
    into the filesystem than the model can."""
    try:
        return builtin.note_path(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# ------------------------------------------------------------------- facts


class FactIn(BaseModel):
    text: str
    # What the caller believes is there now. The reconciler rewrites this list
    # in a background task after every turn, so a number the panel read a
    # moment ago can already point at a different fact — and an edit that
    # silently lands on the wrong line is worse than one that is refused.
    expect: str | None = None


def _facts_view() -> dict:
    return {"facts": memory.facts, "max": FACTS.get("max", 25),
            "max_chars": FACTS.get("max_chars", 120)}


def _check(number: int, expect: str | None) -> int:
    """Validate a 1-based fact number and return its index.

    Numbered from one because that is what the persona shows her and what
    forget_fact takes, so the panel and the model count the same way.
    """
    index = number - 1
    if not 0 <= index < len(memory.facts):
        raise HTTPException(status_code=404, detail=f"there is no fact {number}")
    if expect is not None and memory.facts[index] != expect:
        raise HTTPException(
            status_code=409,
            detail=f"fact {number} changed underneath you — it now reads "
                   f"{memory.facts[index]!r}",
        )
    return index


def _usable(text: str) -> str:
    cleaned = " ".join(text.split())
    why = validate_fact(cleaned)
    if why:
        raise HTTPException(status_code=400, detail=f"Not a usable fact: {why}.")
    return cleaned


@router.get("/memory/facts")
async def facts_list():
    return _facts_view()


@router.post("/memory/facts")
async def fact_add(body: FactIn):
    cap = FACTS.get("max", 25)
    if len(memory.facts) >= cap:
        raise HTTPException(
            status_code=400,
            detail=f"That is the {cap}th fact — drop one first. The cap is "
                   f"deliberate: every fact is in the prompt on every turn.",
        )
    memory.facts.append(_usable(body.text))
    memory.save_facts()
    events.publish("memory")
    return _facts_view()


@router.put("/memory/facts/{number}")
async def fact_edit(number: int, body: FactIn):
    index = _check(number, body.expect)
    memory.facts[index] = _usable(body.text)
    memory.save_facts()
    events.publish("memory")
    return _facts_view()


@router.delete("/memory/facts/{number}")
async def fact_delete(number: int, expect: str | None = None):
    index = _check(number, expect)
    dropped = memory.facts.pop(index)
    memory.save_facts()
    events.publish("memory")
    log.info("fact %d deleted from the panel: %r", number, dropped)
    return _facts_view()


# ------------------------------------------------------------------ timers


class TimerIn(BaseModel):
    label: str
    duration_seconds: int


@router.get("/timers")
async def timers_list():
    return {
        "timers": builtin.live_timers(),
        "now": time.time(),
        # Whether anything is actually listening, not whether the feature
        # exists. A timer with no panel open still expires silently, and that
        # is worth saying rather than leaving to be discovered at dinner.
        "can_fire": events.hub.listeners > 0,
        "listeners": events.hub.listeners,
        "note": "" if events.hub.listeners else
                "No panel is open to ring, so a timer that comes due now will "
                "pass silently.",
    }


@router.post("/timers")
async def timer_create(body: TimerIn):
    try:
        builtin.set_timer(body.duration_seconds, body.label)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    events.publish("timers")
    return {"timers": builtin.live_timers(), "now": time.time()}


@router.delete("/timers/{label}")
async def timer_cancel(label: str):
    if not builtin.drop_timer(label):
        raise HTTPException(status_code=404, detail=f"no timer called {label!r}")
    events.publish("timers")
    return {"timers": builtin.live_timers(), "now": time.time()}


# --------------------------------------------------------------- reminders


class ReminderIn(BaseModel):
    text: str
    at: str = ""
    day: str = ""
    in_minutes: int | None = None


def _reminders_view() -> dict:
    return {"reminders": [reminders.view(r) for r in reminders.pending()],
            "now": time.time()}


@router.get("/reminders")
async def reminders_list():
    return _reminders_view()


@router.post("/reminders")
async def reminder_create(body: ReminderIn):
    try:
        reminders.add(body.text, reminders.due_at(body.day, body.at,
                                                  body.in_minutes))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    events.publish("timers")
    return _reminders_view()


@router.delete("/reminders/{ident}")
async def reminder_cancel(ident: str):
    if reminders.drop(ident) is None:
        raise HTTPException(status_code=404, detail=f"no reminder {ident!r}")
    events.publish("timers")
    return _reminders_view()


# ---------------------------------------------------------------- settings


@dataclass(frozen=True)
class Field:
    """One editable setting.

    `key` is "<file>.<dotted path>". `applies` is what the panel tells the
    user about when the change bites: most settings are read afresh on every
    request, but the two that decide how a model is built only take hold the
    next time one is loaded.
    """

    key: str
    label: str
    kind: str  # text | int | float | bool | choice | key
    group: str
    help: str = ""
    applies: str = "live"  # live | models
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    options: list[str] = dataclass_field(default_factory=list)


def _model_options() -> list[str]:
    """The .gguf files in the models folder, plus the current model if it is
    one the person pointed at elsewhere on their machine."""
    options = sorted(p.name for p in paths.MODELS.glob("*.gguf"))
    current = settings.LLM.get("model_file", "")
    if current and current not in options:
        options.append(current)
    return options


FIELDS: list[Field] = [
    Field("settings.client.push_to_talk_key", "Push to talk", "key", "Talking",
          "Hold this to speak. Named as the browser names it, so it follows "
          "the physical key rather than the character it types."),
    *([Field("app.autostart", "Start Naka when you sign in", "bool", "Talking",
             "Puts Naka in the tray at sign-in, with push-to-talk ready. The "
             "speech and language models stay off the graphics card until "
             "you first speak.")] if autostart.available() else []),
    Field("settings.client.use_tools", "Let her use her tools", "bool",
          "Talking",
          "Timers, notes, the clock, GPU status. Costs about 600ms a reply, "
          "because she has to finish deciding whether to call one before she "
          "can start speaking. Off, she can only talk."),
    Field("settings.client.listen_when_open", "Listen while the panel is open",
          "bool", "Talking",
          "The browser holds the microphone for as long as the tab is open, "
          "which is what the recording dot in the tab strip means. Off means "
          "asking for it each time you first speak."),

    Field("settings.powers.web", "Let her use the web", "bool", "Powers",
          "Web searches, and reading the pages they find. The models stay on "
          "this machine; only the searches and pages go out. Needs her tools "
          "on."),
    Field("settings.powers.shell", "Let her use PowerShell", "bool", "Powers",
          "Commands on this PC, as you. Ones that only look — listing a "
          "folder, reading a file, git status — run straight away; anything "
          "that changes something waits for you to say yes. After she has "
          "read a web page, every command waits, because a page can be "
          "written to talk her into things."),
    Field("settings.powers.workspace", "Workspace", "text", "Powers",
          "Where commands start unless she is told otherwise. She can still "
          "reach the rest of the disk."),
    Field("settings.powers.command_timeout", "Command time limit", "int",
          "Powers",
          "Seconds before a command, and everything it started, is killed.",
          minimum=5, maximum=600),
    Field("settings.powers.brave_api_key", "Brave Search key", "text",
          "Powers",
          "Optional. With a key (free tier: 2000 searches a month) searches "
          "go through Brave; empty uses DuckDuckGo, which needs no account "
          "but is less dependable."),

    Field("settings.identity.assistant", "Assistant name", "text", "Identity",
          "What she is called, everywhere — the persona reads it from here."),
    Field("settings.identity.user", "Your name", "text", "Identity",
          "Used in the persona and when she writes a fact about you."),
    Field("settings.identity.user_pronoun", "Your pronoun", "text", "Identity",
          "Subject form: he, she, they."),
    Field("settings.identity.user_possessive", "Your possessive", "text",
          "Identity", "his, her, their."),

    Field("voice.voice.name", "Kokoro voice", "text", "Voice",
          'One voice, or a blend like "af_heart:60,am_michael:40".'),
    Field("voice.dsp.enabled", "Voice processing", "bool", "Voice",
          "The character on top of the raw speech. Off is the plain model."),
    Field("voice.dsp.pitch.semitones", "Pitch", "float", "Voice",
          "Shifts formants too, which is what stops it sounding like a "
          "person. Past 3 it becomes a chipmunk.",
          minimum=-6, maximum=6, step=0.5),
    Field("voice.dsp.chorus.enabled", "Chorus", "bool", "Voice",
          "Short doubling: suggests more than one source."),
    Field("voice.dsp.compressor.ratio", "Compression", "float", "Voice",
          "Flattens the breath-driven loudness of a human talker.",
          minimum=1, maximum=20, step=0.5),
    Field("voice.dsp.crusher.enabled", "Bit crusher", "bool", "Voice",
          "The fastest way to sound cheap rather than synthetic. Usually off."),

    Field("settings.llm.model_file", "Model", "choice", "Language model",
          "Which model file to run. Switching takes effect the next time the "
          "language model starts.", applies="models",
          options=_model_options()),
    Field("settings.llm.ctx_size", "Context", "int", "Language model",
          "Tokens she can hold at once: persona, facts, recent turns, tool "
          "schemas and every result in a chain. 8192 is enough to talk; with "
          "the web or PowerShell on, 16384 leaves room to read a few pages. "
          "More costs VRAM.", applies="models",
          minimum=2048, maximum=32768, step=1024),
    Field("settings.llm.url", "Server", "text", "Language model",
          "Where llama.cpp is listening."),
    Field("settings.llm.temperature", "Temperature", "float", "Language model",
          "0 is deterministic. Higher wanders.", minimum=0, maximum=2, step=0.05),
    Field("settings.llm.max_tokens", "Reply cap", "int", "Language model",
          "Tokens per reply. Spoken answers rarely need many.",
          minimum=32, maximum=2048, step=32),
    Field("settings.llm.enable_thinking", "Reasoning tokens", "bool",
          "Language model",
          "Reasoning never reaches the speakers, so it is pure latency. "
          "Leaving this on cost every spoken word in testing."),

    Field("settings.stt.model", "Whisper model", "choice", "Hearing",
          "Larger hears better and takes longer.", applies="models",
          options=["tiny", "base", "small", "medium", "large-v3",
                   "large-v3-turbo"]),
    Field("settings.stt.compute_type", "Precision", "choice", "Hearing",
          "How the model is quantised in VRAM.", applies="models",
          options=["int8", "int8_float16", "float16", "float32"]),
    Field("settings.stt.language", "Language", "choice", "Hearing",
          "Forced, not detected: detection degrades mixed-language speech.",
          options=["en", "fr", "de", "es", "it", "pt", "nl", "ja", "zh"]),
    Field("settings.stt.beam_size", "Beam size", "int", "Hearing",
          "Wider searches harder for the right words.", minimum=1, maximum=10),

    Field("settings.ops.idle_unload_minutes", "Idle unload", "int", "Resources",
          "Minutes without a request before the GPU is released. 0 never "
          "releases it.", minimum=0, maximum=240),
    Field("settings.ops.idle_unload_llm", "Also stop the language model",
          "bool", "Resources",
          "It holds most of the VRAM, so leaving it running defeats the point."),

    Field("settings.logs.retention_days", "Keep logs for", "int", "Logs",
          "Days of conversation and tool-call records to keep. This also "
          "bounds how far back the record of tool calls goes. 0 keeps "
          "everything. The text log rotates by size and is not affected.",
          minimum=0, maximum=365),
]

def _connection_fields() -> list[Field]:
    """Each connection's switch and settings. Accepted by PATCH /settings but
    kept out of the Settings drawer: they are shown on their own cards."""
    out = []
    for c in connections.ALL.values():
        out.append(Field(f"settings.connections.{c.name}.enabled", c.label,
                         "bool", "Connections"))
        for s in c.settings:
            out.append(Field(f"settings.connections.{c.name}.{s.key}",
                             s.label, s.kind, "Connections", s.help,
                             options=list(s.options)))
    return out


BY_KEY = {f.key: f for f in FIELDS + _connection_fields()}

_FILES = {"settings": settings.SETTINGS_FILE, "voice": settings.VOICE_FILE}


def _split(key: str) -> tuple[Path, list[str]]:
    file, _, rest = key.partition(".")
    if file not in _FILES or not rest:
        raise HTTPException(status_code=400, detail=f"unknown setting {key!r}")
    return _FILES[file], rest.split(".")


def _current(key: str):
    """The value in effect: the person's file if it says, else the shipped one.

    Reading only the person's file showed any key it did not mention as empty
    in the panel — including every setting added after it was first written,
    which the server was meanwhile happily using from the shipped defaults.
    """
    if key == "app.autostart":
        return autostart.enabled()
    path, parts = _split(key)
    for source in (path, paths.DEFAULTS / path.name):
        if not source.exists():
            continue
        node = tomlkit.parse(source.read_text(encoding="utf-8"))
        for part in parts:
            if part not in node:
                break
            node = node[part]
        else:
            return node.unwrap() if hasattr(node, "unwrap") else node
    return None


def _coerce(f: Field, value):
    """Take the field at its word about its own type, and reject the rest.

    The panel sends JSON, so an int arrives as a float often enough that
    coercing is kinder than refusing; a string where a number belongs is a
    real mistake and is refused.
    """
    try:
        if f.kind == "bool":
            if not isinstance(value, bool):
                raise ValueError("expected true or false")
            return value
        if f.kind == "int":
            # int("hot") explains itself in Python's words, not the user's.
            if not isinstance(value, (int, float)) or int(value) != float(value):
                raise ValueError("expected a whole number")
            coerced = int(value)
        elif f.kind == "float":
            if not isinstance(value, (int, float)):
                raise ValueError("expected a number")
            coerced = float(value)
        else:
            coerced = str(value).strip()
            if not coerced:
                raise ValueError("cannot be empty")
            if f.kind == "choice" and coerced not in f.options:
                raise ValueError(f"must be one of {', '.join(f.options)}")
            if f.kind == "key" and not _KEY_CODE.fullmatch(coerced):
                raise ValueError("not a key name the browser would produce")
            return coerced
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400,
                            detail=f"{f.label}: {e}") from e

    if f.minimum is not None and coerced < f.minimum:
        raise HTTPException(status_code=400,
                            detail=f"{f.label}: must be at least {f.minimum}")
    if f.maximum is not None and coerced > f.maximum:
        raise HTTPException(status_code=400,
                            detail=f"{f.label}: must be at most {f.maximum}")
    return coerced


class SettingsIn(BaseModel):
    changes: dict[str, object]


@router.get("/settings")
async def settings_read():
    return {
        "fields": [
            {
                "key": f.key, "label": f.label, "kind": f.kind,
                "group": f.group, "help": f.help, "applies": f.applies,
                "minimum": f.minimum, "maximum": f.maximum, "step": f.step,
                "options": f.options, "value": _current(f.key),
            }
            for f in FIELDS
        ],
        "groups": list(dict.fromkeys(f.group for f in FIELDS)),
        "files": {name: str(path) for name, path in _FILES.items()},
    }


@router.patch("/settings")
async def settings_write(body: SettingsIn):
    """Validate everything, then write — so a bad value in a batch leaves the
    file exactly as it was rather than half-applied."""
    if not body.changes:
        raise HTTPException(status_code=400, detail="nothing to change")

    planned: dict[Path, list[tuple[list[str], object]]] = {}
    app_changes: dict[str, object] = {}
    for key, value in body.changes.items():
        f = BY_KEY.get(key)
        if f is None:
            raise HTTPException(status_code=400, detail=f"unknown setting {key!r}")
        if key.startswith("app."):
            app_changes[key] = _coerce(f, value)
            continue
        path, parts = _split(key)
        planned.setdefault(path, []).append((parts, _coerce(f, value)))

    for path, edits in planned.items():
        # tomlkit round-trips the file, so the comments explaining each knob
        # survive being edited from a web page.
        doc = tomlkit.parse(path.read_text(encoding="utf-8"))
        for parts, value in edits:
            node = doc
            for part in parts[:-1]:
                if part not in node:
                    node[part] = tomlkit.table()
                node = node[part]
            node[parts[-1]] = value
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(tomlkit.dumps(doc), encoding="utf-8")
        tmp.replace(path)
        log.info("saved %d setting(s) to %s", len(edits), path.name)

    if "app.autostart" in app_changes:
        autostart.set_enabled(bool(app_changes["app.autostart"]))

    settings.reload()
    # The persona is a template filled from identity, so a name change only
    # shows up once it is rebuilt.
    memory.reload()
    events.publish("settings")
    if any(k.startswith("settings.connections.") for k in body.changes):
        events.publish("connections")

    return {
        "saved": sorted(body.changes),
        "needs_model_reload": sorted(
            k for k in body.changes if BY_KEY[k].applies == "models"
        ),
        "fields": (await settings_read())["fields"],
    }
