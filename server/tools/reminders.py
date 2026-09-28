"""Reminders at a time of day: "remind me tomorrow at nine to call Paul".

Unlike a timer, a reminder outlives the server. It is written to disk as soon
as it is set, rings in the panel and the tray when it comes due, and goes to
Telegram too when that is connected. One that came due while the PC was off
rings as soon as Naka is back, marked late: a kitchen timer that rings an
hour late is useless, a reminder to call someone mostly is not.
"""

import json
import re
import secrets
import threading
import time
from datetime import date, datetime, timedelta

from .. import paths
from .registry import tool

FILE = paths.DATA / "reminders.json"
# A reminder further out than this is a calendar entry.
HORIZON_DAYS = 366

_lock = threading.Lock()
# Read from disk once, then kept here: the watcher looks every half second,
# and nothing but this module writes the file.
_items: list[dict] | None = None
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday",
             "saturday", "sunday"]


def _load() -> list[dict]:
    global _items
    if _items is None:
        try:
            loaded = json.loads(FILE.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            loaded = []
        _items = [r for r in loaded if isinstance(r, dict) and "due" in r]
    return list(_items)


def _save(reminders: list[dict]) -> None:
    global _items
    _items = list(reminders)
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reminders, indent=1, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(FILE)


def _parse_day(text: str, today: date) -> date:
    word = text.strip().lower()
    if word in ("", "today"):
        return today
    if word == "tomorrow":
        return today + timedelta(days=1)
    if word in _WEEKDAYS:
        ahead = (_WEEKDAYS.index(word) - today.weekday()) % 7 or 7
        return today + timedelta(days=ahead)
    try:
        return date.fromisoformat(word)
    except ValueError:
        raise ValueError(f"{text!r} is not a date: use YYYY-MM-DD, today, "
                         "tomorrow or a weekday") from None


def _parse_time(text: str) -> tuple[int, int]:
    found = re.fullmatch(r"\s*(\d{1,2})(?:[:h.](\d{2}))?\s*(am|pm)?\s*",
                         text.lower())
    if not found:
        raise ValueError(f"{text!r} is not a time: use HH:MM, 24-hour")
    hour, minute = int(found[1]), int(found[2] or 0)
    if found[3] == "pm" and hour < 12:
        hour += 12
    if found[3] == "am" and hour == 12:
        hour = 0
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"{text!r} is not a time of day")
    return hour, minute


def due_at(day: str = "", at: str = "", in_minutes: int | None = None,
           now: datetime | None = None) -> datetime:
    """When a reminder asked for this way should ring."""
    now = now or datetime.now()
    if in_minutes is not None:
        if in_minutes <= 0:
            raise ValueError("in_minutes must be positive")
        return (now + timedelta(minutes=in_minutes)).replace(microsecond=0)
    if not at:
        raise ValueError("a reminder needs a time, or in_minutes")
    hour, minute = _parse_time(at)
    when = datetime.combine(_parse_day(day, now.date()),
                            datetime.min.time()).replace(hour=hour,
                                                         minute=minute)
    if when <= now and not day:
        # "At nine" said at ten means tomorrow's nine.
        when += timedelta(days=1)
    if when <= now:
        raise ValueError(f"{when:%A %d %B at %H:%M} has already passed")
    if when > now + timedelta(days=HORIZON_DAYS):
        raise ValueError("that is more than a year away")
    return when


def _spoken(when: datetime) -> str:
    today = datetime.now().date()
    if when.date() == today:
        prefix = "today"
    elif when.date() == today + timedelta(days=1):
        prefix = "tomorrow"
    else:
        prefix = f"{when:%A %d %B}"
    return f"{prefix} at {when:%H:%M}"


def add(text: str, when: datetime) -> dict:
    text = " ".join(text.split())
    if not text:
        raise ValueError("a reminder needs to say what it is for")
    reminder = {"id": secrets.token_hex(3), "text": text[:200],
                "due": when.timestamp(), "created": time.time()}
    with _lock:
        reminders = _load()
        reminders.append(reminder)
        _save(reminders)
    return reminder


def pending() -> list[dict]:
    with _lock:
        return sorted(_load(), key=lambda r: r["due"])


def drop(ident: str) -> dict | None:
    """Remove one by id, or failing that by what it says."""
    needle = ident.strip().lower()
    with _lock:
        reminders = _load()
        match = next((r for r in reminders if r["id"] == needle), None) or \
            next((r for r in reminders if needle and needle in r["text"].lower()),
                 None)
        if match is None:
            return None
        reminders.remove(match)
        _save(reminders)
        return match


def take_due() -> list[dict]:
    """Remove and return every reminder that has come due. Taken, not read,
    so the watcher cannot ring one twice."""
    now = time.time()
    with _lock:
        reminders = _load()
        due = [r for r in reminders if r["due"] <= now]
        if due:
            _save([r for r in reminders if r["due"] > now])
    for r in due:
        r["late"] = now - r["due"] > 120
    return due


def view(r: dict) -> dict:
    return {"id": r["id"], "text": r["text"], "due": r["due"],
            "when": _spoken(datetime.fromtimestamp(r["due"]))}


@tool(
    description=(
        "Set a reminder for a time of day, e.g. 'remind me tomorrow at 9 to "
        "call Paul'. It survives restarts, rings on the PC, and is sent to "
        "the user's phone when Telegram is connected. For a countdown of "
        "minutes use set_timer; use this for anything with a clock time or "
        "a day."),
    parameters={
        "text": {"type": "string",
                 "description": "What to remind them of, e.g. 'call Paul'."},
        "at": {"type": "string",
               "description": "Time of day, 24-hour HH:MM, e.g. '09:00'."},
        "day": {"type": "string",
                "description": "YYYY-MM-DD, 'today', 'tomorrow' or a weekday "
                               "name. Leave out for the next time 'at' comes "
                               "round."},
        "in_minutes": {"type": "integer",
                       "description": "Instead of at/day: this many minutes "
                                      "from now."},
    },
    required=["text"],
    label="Set a reminder",
    summary="At a time or on a day. Kept across restarts; sent to Telegram "
            "when connected.",
)
def set_reminder(text: str, at: str = "", day: str = "",
                 in_minutes: int | None = None):
    when = due_at(day, at, in_minutes)
    reminder = add(text, when)
    return f"Reminder set for {_spoken(when)}: {reminder['text']}."


@tool(
    description="List the reminders that have not rung yet.",
    parameters={},
    label="List reminders",
    summary="What is still to come.",
)
def list_reminders():
    waiting = pending()
    if not waiting:
        return "No reminders set."
    return "; ".join(f"[{r['id']}] {view(r)['when']}: {r['text']}"
                     for r in waiting)


@tool(
    description="Cancel a reminder, by its id from list_reminders or by "
                "words from what it says.",
    parameters={"reminder": {"type": "string",
                             "description": "Its id, or words from it."}},
    required=["reminder"],
    label="Cancel a reminder",
    summary="Removes one before it rings.",
)
def cancel_reminder(reminder: str):
    dropped = drop(reminder)
    if dropped is None:
        return f"No reminder matches {reminder!r}."
    return f"Cancelled the reminder to {dropped['text']}."
