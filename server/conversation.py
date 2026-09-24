"""The conversation as the person sees it: every exchange, kept, and live.

Memory holds three turns for the model; this holds all of them for the panel,
one JSON line each in the data folder, so the chat can scroll back past what
the model still remembers and survive a restart. It is read a page at a time
from the end, newest last, which is what a chat that loads older messages as
you scroll up asks for.

A turn is also announced while it happens, on the "turn" topic: what was
heard the moment it is transcribed, each sentence as it is spoken, and how it
ended. The panel used to learn about a turn only once it was over, so what
had been heard stayed invisible until the answer had finished.
"""

import json
import logging
import threading
from datetime import datetime

from . import events, paths

log = logging.getLogger("naka.conversation")

PATH = paths.DATA / "conversation.jsonl"
PAGE = 20

_lock = threading.Lock()
_turns: list[dict] | None = None
# Handed out when a turn starts, not when it is saved: a typed message sent
# while a spoken one is still being answered must not get the same id.
_last_id = 0


def _load() -> list[dict]:
    """Every recorded turn, read once and then kept in step with the file."""
    global _turns
    if _turns is not None:
        return _turns
    turns: list[dict] = []
    if not PATH.exists():
        _backfill()
    try:
        with PATH.open(encoding="utf-8") as f:
            for line in f:
                try:
                    turns.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    _turns = turns
    return turns


def _backfill() -> None:
    """Start the history from the turn log, so it does not begin empty.

    turns.jsonl is the debugging record of every spoken exchange; it holds
    what this needs among a great deal it does not. Done once, when this
    file does not exist yet.
    """
    source = paths.LOGS / "turns.jsonl"
    if not source.exists():
        return
    written = 0
    try:
        with source.open(encoding="utf-8") as src, \
                PATH.open("w", encoding="utf-8") as out:
            for line in src:
                try:
                    old = json.loads(line)
                except json.JSONDecodeError:
                    continue
                spoken = old.get("spoken") or []
                turn = {
                    "id": written + 1,
                    "at": old.get("at", ""),
                    "user": old.get("heard", ""),
                    "naka": " ".join(spoken) if isinstance(spoken, list) else str(spoken),
                    "via": "voice",
                    "tools": [t.get("name", "") for t in old.get("tools") or []],
                    "interrupted": False,
                }
                out.write(json.dumps(turn, ensure_ascii=False) + "\n")
                written += 1
    except OSError as e:
        log.warning("could not start the history from the turn log: %s", e)
        return
    log.info("history started from %d logged turns", written)


def next_id() -> int:
    global _last_id
    with _lock:
        turns = _load()
        _last_id = max(_last_id, turns[-1]["id"] if turns else 0) + 1
        return _last_id


def record(turn_id: int, user: str, naka: str, *, via: str,
           tools: list[dict] | None = None, interrupted: bool = False) -> None:
    turn = {
        "id": turn_id,
        "at": datetime.now().isoformat(timespec="seconds"),
        "user": user,
        "naka": naka,
        "via": via,
        "tools": [t.get("name", "") for t in tools or []],
        "interrupted": interrupted,
    }
    with _lock:
        _load().append(turn)
        try:
            with PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(turn, ensure_ascii=False) + "\n")
        except OSError as e:
            # The history must never take the conversation down with it.
            log.warning("could not save the turn: %s", e)
    events.publish("turn", id=turn_id, phase="done", interrupted=interrupted)


def page(before: int | None = None, limit: int = PAGE) -> dict:
    """Up to `limit` turns older than `before` (or the newest), oldest first."""
    limit = max(1, min(int(limit), 100))
    with _lock:
        turns = _load()
        end = len(turns)
        if before is not None:
            # Ids only ever grow, so the first one at or past `before` is where
            # the page ends.
            end = next((i for i, t in enumerate(turns) if t["id"] >= before), end)
        start = max(0, end - limit)
        return {"turns": turns[start:end], "has_more": start > 0}


# ----------------------------------------------------------------- live

def heard(turn_id: int, text: str, via: str) -> None:
    events.publish("turn", id=turn_id, phase="heard", text=text, via=via)


def said(turn_id: int, sentence: str) -> None:
    events.publish("turn", id=turn_id, phase="sentence", text=sentence)

