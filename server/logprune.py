"""Drop records older than the retention window from the .jsonl logs.

Simpler than size-based rotation and easier to reason about: everything from
the last N days is there, nothing older is. Runs hourly in the background.

naka.log is not pruned here. It is rotated by its handler instead (see
logsetup.py), because a handler holds the file open and rewriting it from
another thread is exactly what rotation exists to avoid.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from . import paths

log = logging.getLogger("naka.logprune")

LOGS = paths.LOGS


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
        try:
            temp.replace(path)
        except PermissionError:
            # The turn log or the audit log had the file open for an append at
            # that instant. On Linux the replace would have won and lost that
            # line; on Windows it refuses. Either way the answer is the same:
            # leave it, and prune on the next pass an hour from now.
            temp.unlink(missing_ok=True)
            return 0
    return dropped


def prune_once(days: int) -> dict:
    cutoff = datetime.now() - timedelta(days=days)
    return {
        "turns.jsonl": _prune_jsonl(LOGS / "turns.jsonl", cutoff),
        "audit.jsonl": _prune_jsonl(LOGS / "audit.jsonl", cutoff),
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
