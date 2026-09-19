"""Tool registry, allowlist enforcement and audit log.

Two gates stand between the model and any effect on the machine: a tool must
be registered in code, and it must be enabled in config/tools.yaml. Registering
alone does nothing. Everything that runs is appended to logs/audit.jsonl with
its arguments, whether it succeeded or not.
"""

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger("naka.tools")

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG = ROOT / "config" / "tools.yaml"
AUDIT = ROOT / "logs" / "audit.jsonl"

_config = yaml.safe_load(CONFIG.read_text())
AGENT = _config["agent"]
PATHS = _config["paths"]
FACTS = _config.get("facts", {})
_allowlist = _config["tools"]

NOTES_DIR = Path(PATHS["notes_dir"]).expanduser()


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable
    required: list[str] = field(default_factory=list)

    @property
    def enabled(self) -> bool:
        return _allowlist.get(self.name, {}).get("enabled", False)

    @property
    def destructive(self) -> bool:
        return _allowlist.get(self.name, {}).get("destructive", True)

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


def tool(description: str, parameters: dict, required: list[str] | None = None):
    def decorator(fn):
        name = fn.__name__
        if name not in _allowlist:
            # Refusing to register is deliberate: a tool that exists in code but
            # not in the allowlist should be a visible mistake, not a silent one.
            log.warning("tool %r is not in tools.yaml and will not be available",
                        name)
        _registry[name] = Tool(name, description, parameters, fn,
                               required or [])
        return fn
    return decorator


def available() -> list[Tool]:
    return [t for t in _registry.values() if t.enabled]


def schemas() -> list[dict]:
    return [t.schema() for t in available()]


def get(name: str) -> Tool | None:
    tool_obj = _registry.get(name)
    return tool_obj if tool_obj and tool_obj.enabled else None


def audit(name: str, arguments: dict, status: str, detail: str = "") -> None:
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tool": name,
        "arguments": arguments,
        "status": status,
        "detail": detail[:500],
    }
    with AUDIT.open("a") as f:
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

    audit(name, arguments, "ok", result)
    return result
