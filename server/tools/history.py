"""Looking back through the conversation: "what did we say about the trip?"

Memory holds a summary and the last few turns; conversation.jsonl holds every
exchange ever had. This searches the latter by words, or lists one day's
exchanges, so "yesterday" and "last week" have an answer the summary may have
folded away.
"""

import re
from datetime import date, datetime, timedelta

from .. import conversation
from .registry import tool

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday",
             "saturday", "sunday"]
CLIP = 220


def _day(text: str) -> str | None:
    word = text.strip().lower()
    if not word:
        return None
    today = date.today()
    if word == "today":
        return today.isoformat()
    if word == "yesterday":
        return (today - timedelta(days=1)).isoformat()
    if word in _WEEKDAYS:
        back = (today.weekday() - _WEEKDAYS.index(word)) % 7 or 7
        return (today - timedelta(days=back)).isoformat()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", word):
        return word
    raise ValueError(f"{text!r} is not a day: use YYYY-MM-DD, today, "
                     "yesterday or a weekday")


def _clip(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= CLIP else text[:CLIP].rsplit(" ", 1)[0] + "…"


def _line(turn: dict) -> str:
    try:
        when = datetime.fromisoformat(turn["at"]).strftime("%A %d %B %H:%M")
    except (KeyError, ValueError):
        when = "some time ago"
    return f"{when}. They said: {_clip(turn.get('user', ''))} You said: {_clip(turn.get('naka', ''))}"


@tool(
    description=(
        "Search everything said in past conversations with the user, beyond "
        "what you remember right now: 'what did we say about the trip', "
        "'what did I ask you yesterday', 'what was that game you "
        "recommended'. Give words to look for, a day, or both."),
    parameters={
        "query": {"type": "string",
                  "description": "Words to look for. Leave empty to list a "
                                 "day's conversation."},
        "day": {"type": "string",
                "description": "Optional: YYYY-MM-DD, 'today', 'yesterday' "
                               "or a weekday."},
    },
    label="Search past conversations",
    summary="Finds what was said before, by words or by day.",
)
def search_conversations(query: str = "", day: str = ""):
    turns = conversation.search(query, _day(day), limit=6 if query else 12)
    if not turns:
        what = f"about {query!r}" if query else "then"
        return f"Nothing in past conversations {what}."
    return "\n".join(_line(t) for t in turns)
