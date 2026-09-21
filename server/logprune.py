"""Drop log entries older than the retention window.

Simpler than size-based rotation and easier to reason about: everything from
the last N days is there, nothing older is. Runs hourly in the background.

naka.log is rewritten in place rather than replaced, because the shell
redirect that feeds it holds an open descriptor — swapping the file would
leave the server writing to an unlinked inode and the visible log frozen.
"""

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import paths

log = logging.getLogger("naka.logprune")

LOGS = paths.LOGS
LEADING_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def _prune_jsonl(path: Path, cutoff: datetime) -> int:
    """Drop records whose `at` predates the cutoff. Replaced atomically."""
    if not path.exists():
        return 0
    kept, dropped = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            when = datetime.fromisoformat(json.loads(line)["at"])
        except (json.JSONDecodeError, KeyError, ValueError):
            kept.append(line)  # unreadable: keep rather than silently destroy
            continue
        if when >= cutoff:
            kept.append(line)
        else:
            dropped += 1
    if dropped:
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        temp.replace(path)
    return dropped


def _prune_text(path: Path, cutoff: datetime) -> int:
    """Drop dated lines older than the cutoff, truncating in place.

    Lines without their own date — tracebacks, library warnings — belong to
    the entry above them and are kept or dropped with it.
    """
    if not path.exists():
        return 0
    kept, dropped, keeping = [], 0, True
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = LEADING_DATE.match(line)
        if match:
            try:
                keeping = datetime.strptime(match.group(1), "%Y-%m-%d") >= cutoff
            except ValueError:
                keeping = True
        if keeping:
            kept.append(line)
        else:
            dropped += 1
    if dropped:
        with path.open("r+", encoding="utf-8") as f:
            f.write("\n".join(kept) + ("\n" if kept else ""))
            f.truncate()
    return dropped


def prune_once(days: int) -> dict:
    cutoff = datetime.now() - timedelta(days=days)
    return {
        "turns.jsonl": _prune_jsonl(LOGS / "turns.jsonl", cutoff),
        "audit.jsonl": _prune_jsonl(LOGS / "audit.jsonl", cutoff),
        "naka.log": _prune_text(LOGS / "naka.log", cutoff),
    }


async def pruner() -> None:
    from . import settings

    days = settings.LOGS.get("retention_days", 0)
    if not days:
        log.info("log retention disabled — logs grow without limit")
        return

    log.info("keeping %d days of logs", days)
    while True:
        try:
            dropped = await asyncio.to_thread(prune_once, days)
            if any(dropped.values()):
                log.info("pruned older than %d days: %s", days, dropped)
        except Exception as e:
            log.warning("log prune failed: %s", e)
        await asyncio.sleep(3600)
