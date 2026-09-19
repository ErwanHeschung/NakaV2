"""The tools themselves.

Each one is narrow on purpose. Notes are confined to NOTES_DIR and filenames
are sanitised to a single path component, so nothing here can reach the rest
of the filesystem however the model phrases its arguments.
"""

import re
import subprocess
import threading
import time
from datetime import datetime

from .registry import NOTES_DIR, tool

_timers: dict[str, dict] = {}
_timer_lock = threading.Lock()
_SAFE_NAME = re.compile(r"[^a-z0-9 _-]")


def _note_path(name: str):
    """Resolve a note name to a file inside NOTES_DIR, and nowhere else."""
    cleaned = _SAFE_NAME.sub("", name.strip().lower()).strip()
    cleaned = re.sub(r"\s+", "-", cleaned)[:60]
    if not cleaned:
        raise ValueError("that is not a usable note name")
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    path = (NOTES_DIR / f"{cleaned}.md").resolve()
    if path.parent != NOTES_DIR.resolve():
        raise ValueError("note names cannot contain paths")
    return path


@tool(
    description="Get the current date and time.",
    parameters={},
)
def get_time():
    return datetime.now().strftime("It is %H:%M on %A %d %B %Y.")


@tool(
    description="Report how much GPU memory is free, and how much the models "
                "are using. Useful before starting a game.",
    parameters={},
)
def gpu_status():
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used,memory.total",
         "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=10,
    )
    used, total = (int(x) for x in result.stdout.strip().split(","))
    return (f"{used / 1024:.1f} GB of {total / 1024:.1f} GB in use, "
            f"{(total - used) / 1024:.1f} GB free.")


@tool(
    description="Start a countdown timer.",
    parameters={
        "duration_seconds": {"type": "integer",
                             "description": "How long, in seconds."},
        "label": {"type": "string", "description": "What the timer is for."},
    },
    required=["duration_seconds"],
)
def set_timer(duration_seconds: int, label: str = "timer"):
    if duration_seconds <= 0:
        raise ValueError("a timer needs a positive duration")
    if duration_seconds > 24 * 3600:
        raise ValueError("timers are capped at 24 hours")
    with _timer_lock:
        _timers[label] = {"due": time.time() + duration_seconds, "label": label}
    minutes, seconds = divmod(duration_seconds, 60)
    spoken = f"{minutes} minutes" if not seconds else f"{minutes}m {seconds}s"
    return f"Timer '{label}' set for {spoken if minutes else f'{seconds} seconds'}."


@tool(description="List timers that are still running.", parameters={})
def list_timers():
    now = time.time()
    with _timer_lock:
        live = {k: v for k, v in _timers.items() if v["due"] > now}
        _timers.clear()
        _timers.update(live)
        if not live:
            return "No timers running."
        return "; ".join(
            f"{v['label']}: {int(v['due'] - now)}s left" for v in live.values()
        )


@tool(
    description="Cancel a running timer.",
    parameters={"label": {"type": "string", "description": "Which timer."}},
    required=["label"],
)
def cancel_timer(label: str):
    with _timer_lock:
        if label not in _timers:
            return f"There is no timer called '{label}'."
        del _timers[label]
    return f"Cancelled '{label}'."


@tool(
    description="Search the user's notes and return matching lines.",
    parameters={
        "query": {"type": "string", "description": "Text to look for."},
        "limit": {"type": "integer", "description": "Max results, default 5."},
    },
    required=["query"],
)
def search_notes(query: str, limit: int = 5):
    if not NOTES_DIR.exists():
        return "There are no notes yet."
    hits = []
    needle = query.lower()
    for path in sorted(NOTES_DIR.glob("*.md")):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if needle in line.lower():
                hits.append(f"{path.stem} line {number}: {line.strip()}")
                if len(hits) >= limit:
                    return "; ".join(hits)
    return "; ".join(hits) if hits else f"Nothing in the notes about {query}."


@tool(
    description="Read a note by name.",
    parameters={"name": {"type": "string", "description": "The note's name."}},
    required=["name"],
)
def read_note(name: str):
    path = _note_path(name)
    if not path.exists():
        return f"There is no note called '{name}'."
    return path.read_text().strip()[:2000] or "That note is empty."


@tool(
    description="Write a note, or append to it if it already exists.",
    parameters={
        "name": {"type": "string", "description": "The note's name."},
        "content": {"type": "string", "description": "What to write."},
    },
    required=["name", "content"],
)
def write_note(name: str, content: str):
    path = _note_path(name)
    existed = path.exists()
    with path.open("a") as f:
        f.write(content.rstrip() + "\n")
    return f"{'Appended to' if existed else 'Created'} note '{path.stem}'."


@tool(
    description="Delete a note permanently.",
    parameters={"name": {"type": "string", "description": "The note's name."}},
    required=["name"],
)
def delete_note(name: str):
    path = _note_path(name)
    if not path.exists():
        return f"There is no note called '{name}'."
    path.unlink()
    return f"Deleted note '{path.stem}'."
