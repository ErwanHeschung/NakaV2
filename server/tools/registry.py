"""Tool registry, allowlist enforcement and audit log.

Two gates stand between the model and any effect on the machine: a tool must
be registered in code, and it must be enabled in config/tools.yaml. Registering
alone does nothing. Everything that runs is appended to logs/audit.jsonl with
its arguments, whether it succeeded or not.
"""

import json
import os
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .. import events, paths, settings

log = logging.getLogger("naka.tools")

# Which tools change something the panel is showing. Announced from here
# rather than from inside each handler, so a new tool that forgets to say so
# is the exception rather than the rule.
TOPICS = {
    "write_note": "notes",
    "delete_note": "notes",
    "set_timer": "timers",
    "cancel_timer": "timers",
    "set_reminder": "timers",
    "cancel_reminder": "timers",
}

CONFIG = paths.CONFIG / "tools.yaml"
AUDIT = paths.LOGS / "audit.jsonl"


def _read_config(path: Path) -> dict | None:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except yaml.YAMLError as e:
        log.error("%s is not valid YAML (%s); ignoring it", path, e)
        return None
    if isinstance(loaded, dict) and "tools" in loaded:
        return loaded
    log.error("%s has no tools section; ignoring it", path)
    return None


def _load_config() -> dict:
    """The person's tools.yaml laid over the shipped one.

    Read at import, which is when every tool registers — so a file that is
    missing or malformed used to kill the process with a bare traceback. In
    an app with no console, that is a server that silently never starts.

    Overlaid rather than chosen between, one level deep: the person's copy is
    seeded once and never rewritten, so without this a tool added in a later
    version would be missing from their allowlist, and therefore disabled,
    forever. Whatever they did set still wins, tool by tool.
    """
    shipped = _read_config(paths.DEFAULTS / "tools.yaml")
    theirs = _read_config(CONFIG)
    if shipped is None and theirs is None:
        raise RuntimeError(
            f"no usable tools.yaml in {CONFIG} or {paths.DEFAULTS}")
    merged = dict(shipped or {})
    for key, value in (theirs or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


_config = _load_config()
AGENT = _config.get("agent", {"max_steps": 5, "timeout": 30,
                              "default_agentic": False})
PATHS = _config.get("paths", {})
FACTS = _config.get("facts", {})
_allowlist = _config["tools"]

# expandvars as well as expanduser, so the shipped default can say
# "~/Documents/Naka Notes" and a person can point it at %OneDrive% themselves.
NOTES_DIR = Path(os.path.expandvars(
    PATHS.get("notes_dir", "~/Documents/Naka Notes"))).expanduser()


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable
    required: list[str] = field(default_factory=list)
    # A switch this tool also answers to: a key of settings.toml's [powers],
    # or "conn.<name>" for a connection, which must be both switched on and
    # set up. Read on every call rather than at import, so flipping it in the
    # panel offers or withdraws the tool from the very next turn.
    power: str | None = None
    # Decides per call whether to ask first, for tools where that depends on
    # what is being asked — a shell listing a folder is not a shell deleting
    # one. Given the arguments and whether untrusted content has entered the
    # turn. Without it, the yaml's static `destructive` decides.
    confirm: Callable[[dict, bool], bool] | None = None
    # For the panel. `description` is written to steer the model, which makes
    # it an odd thing to show a person; these are written for them.
    label: str = ""
    summary: str = ""
    # Whether what it returns was written by someone else — a web page, a
    # calendar invitation. Once one of these has answered, the rest of the
    # turn is treated as possibly steered by that text.
    untrusted: bool = False

    @property
    def enabled(self) -> bool:
        if not _allowlist.get(self.name, {}).get("enabled", False):
            return False
        return self.power is None or power_on(self.power)

    @property
    def allowlisted(self) -> bool:
        return _allowlist.get(self.name, {}).get("enabled", False)

    @property
    def confirms(self) -> str:
        """When it asks first: "always", "changes" or "never"."""
        if self.destructive:
            return "always"
        return "changes" if self.confirm is not None else "never"

    @property
    def destructive(self) -> bool:
        return _allowlist.get(self.name, {}).get("destructive", True)

    def needs_confirmation(self, arguments: dict, tainted: bool = False) -> bool:
        if self.destructive:
            return True
        return self.confirm is not None and self.confirm(arguments, tainted)

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters,
                    "required": self.required,
                },
            },
        }


_registry: dict[str, Tool] = {}


def power_on(power: str) -> bool:
    if power.startswith("conn."):
        # Imported here: connections register tools, so they import this.
        from ..connections import ready
        return ready(power.removeprefix("conn."))
    return bool(settings.POWERS.get(power))


def tool(description: str, parameters: dict, required: list[str] | None = None,
         power: str | None = None,
         confirm: Callable[[dict, bool], bool] | None = None,
         label: str = "", summary: str = "", untrusted: bool = False):
    def decorator(fn):
        name = fn.__name__
        if name not in _allowlist:
            # Refusing to register is deliberate: a tool that exists in code but
            # not in the allowlist should be a visible mistake, not a silent one.
            log.warning("tool %r is not in tools.yaml and will not be available",
                        name)
        _registry[name] = Tool(name, description, parameters, fn,
                               required or [], power, confirm,
                               label or name.replace("_", " ").capitalize(),
                               summary or description, untrusted)
        return fn
    return decorator


def listed() -> list[Tool]:
    """Every allowlisted tool, including those whose power is switched off."""
    return [t for t in _registry.values() if t.allowlisted]


def available() -> list[Tool]:
    return [t for t in _registry.values() if t.enabled]


def schemas() -> list[dict]:
    return [t.schema() for t in available()]


def get(name: str) -> Tool | None:
    tool_obj = _registry.get(name)
    return tool_obj if tool_obj and tool_obj.enabled else None


def audit(name: str, arguments: dict, status: str, detail: str = "",
          limit: int = 500) -> None:
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tool": name,
        "arguments": arguments,
        "status": status,
        "detail": detail[:limit],
    }
    with AUDIT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    log.info("tool %s(%s) -> %s", name, json.dumps(arguments), status)


def call(name: str, arguments: dict) -> str:
    """Run an allowlisted tool. Never raises: the model gets the error back."""
    tool_obj = get(name)
    if tool_obj is None:
        audit(name, arguments, "refused", "not allowlisted")
        return f"Error: no tool named {name!r} is available."

    unexpected = set(arguments) - set(tool_obj.parameters)
    if unexpected:
        audit(name, arguments, "refused", f"unexpected args {unexpected}")
        return f"Error: {name} does not take {', '.join(sorted(unexpected))}."

    missing = [key for key in tool_obj.required if key not in arguments]
    if missing:
        audit(name, arguments, "refused", f"missing args {missing}")
        return f"Error: {name} needs {', '.join(missing)}."

    try:
        result = str(tool_obj.handler(**arguments))
    except Exception as e:  # handlers touch the filesystem and clocks
        audit(name, arguments, "error", repr(e))
        return f"Error running {name}: {e}"

    # A command's output is the whole record of what it did, so it gets more
    # room than a timer's one-line confirmation.
    audit(name, arguments, "ok", result,
          limit=2000 if tool_obj.power == "shell" else 500)
    topic = TOPICS.get(name)
    if topic:
        events.publish(topic)
    return result
