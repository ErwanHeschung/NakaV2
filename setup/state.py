"""What setup has already done, so it can pick up where it stopped.

A first run downloads around 20 GB. Closing the window, losing the network or
restarting halfway must not mean starting again, so every step records its
outcome in state.json as it finishes, and a later run skips what is recorded
as done.
"""

import json
import os
import time

from server import paths


def load() -> dict:
    try:
        return json.loads(paths.STATE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {"steps": {}, "choices": {}}


def save(state: dict) -> None:
    paths.STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = paths.STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, paths.STATE)


def mark(state: dict, step: str, status: str, detail: str = "") -> None:
    state.setdefault("steps", {})[step] = {
        "status": status, "detail": detail,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    save(state)


def done(state: dict, step: str) -> bool:
    return state.get("steps", {}).get(step, {}).get("status") == "ok"


def complete(state: dict) -> bool:
    """True once every step has succeeded — what the tray checks at launch."""
    from .steps import STEPS

    return all(done(state, step.id) for step in STEPS)
