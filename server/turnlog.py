"""One JSON line per exchange, for debugging a conversation after the fact.

The human-readable log is for watching it work; this is for answering "why did
it say that" a day later. It records what was heard, what the model was
actually given, what it said, which tools ran, and where the time went.
"""

import json
import logging
from pathlib import Path
from datetime import datetime

from . import paths

log = logging.getLogger("naka.turnlog")

PATH = paths.LOGS / "turns.jsonl"


def record(*, heard: str, messages: list[dict], spoken: list[str],
           timings: dict, agentic: bool, tools: list[dict] | None = None) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "heard": heard,
        "agentic": agentic,
        # The full prompt, so a surprising answer can be traced to what the
        # model was actually told rather than what it was assumed to know.
        "prompt": messages,
        "spoken": spoken,
        "tools": tools or [],
        "timings_ms": {k: round(v) for k, v in timings.items()},
    }
    try:
        with PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        # Logging must never take the conversation down with it.
        log.warning("could not write turn log: %s", e)
