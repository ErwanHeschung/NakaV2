"""Operational control: releasing the GPU and taking it back.

The machine is also used for games, and a recent one wants 10-14 GB. Naka
holds roughly 9.5 GB across two processes — the Python server's STT and TTS,
and the llama.cpp container, which is the larger share. Residency is
deliberate (reloading per utterance would cost seconds every time), so
releasing it has to be an explicit action.

These are operator controls reached over HTTP, and deliberately NOT registered
as tools: the model must not be able to unload itself mid-sentence, nor be
talked into stopping its own container.
"""

import asyncio
import logging
import subprocess
import time
from pathlib import Path

import torch

from . import models

log = logging.getLogger("naka.ops")

COMPOSE = Path(__file__).resolve().parent.parent / "compose.yml"
# Fixed argv, no shell, no caller input. The only variable is the verb, and it
# comes from this module rather than from a request.
_DOCKER = ["docker", "compose", "-f", str(COMPOSE)]


def _vram_mb() -> int:
    return _vram()[0]


def _vram() -> tuple[int, int]:
    """(used, total) in MiB. The total is asked for rather than assumed: this
    ships to whatever card the user has, and a hardcoded size turns into a
    wrong percentage on every other machine."""
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used,memory.total",
         "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=10,
    )
    used, total = result.stdout.strip().splitlines()[0].split(",")
    return int(used), int(total)


async def _compose(verb: str) -> bool:
    """Run one compose verb. Returns whether it worked, and says so if not.

    The result used to be discarded, which meant a failed stop was reported as
    a successful unload and the container quietly kept its 7.4 GB.
    """
    process = await asyncio.create_subprocess_exec(
        *_DOCKER, verb, "llm",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await process.communicate()
    if process.returncode != 0:
        log.error("docker compose %s failed (%d): %s",
                  verb, process.returncode, out.decode().strip()[:300])
        return False
    return True


# Serialises load/unload against each other and against requests. Without it,
# pre-warming from the client and a request arriving a moment later would both
# start a load, and each would pay the full cost.
_transition = asyncio.Lock()
_last_activity = time.monotonic()
# Requests currently being served. The idle watcher must not pull the models
# out from under one, and a long reload must not count as idle time.
_in_flight = 0
# Whether the llama.cpp container is believed to be answering. None means we
# have not checked since this process started — the container's state is
# independent of ours, so it cannot be assumed.
_llm_up: bool | None = None


def touch() -> None:
    """Mark the assistant as in use, deferring the idle unload."""
    global _last_activity
    _last_activity = time.monotonic()


def idle_seconds() -> float:
    return time.monotonic() - _last_activity


class Busy:
    """Marks a request in flight, and refreshes the idle timer when it ends."""

    async def __aenter__(self):
        global _in_flight
        _in_flight += 1
        touch()
        return self

    async def __aexit__(self, *exc):
        global _in_flight
        _in_flight -= 1
        touch()
        return False


def _fully_up() -> bool:
    return models.stt is not None and models.tts is not None and _llm_up is True


async def ensure_loaded() -> float:
    """Bring up whatever is missing. Idempotent, and cheap when nothing is.

    Checks the container as well as the local models: the two are separate
    processes with separate lifetimes, and a restarted server loads its own
    models at startup while the container may still be stopped. Checking only
    the local side meant requests went out to a container that was not there.

    Returns the seconds spent waking, or 0 if nothing was needed.
    """
    if _fully_up():
        return 0.0
    start = time.perf_counter()
    async with _transition:
        if not _fully_up():
            log.info("waking on demand")
            await _load_locked()
    return time.perf_counter() - start


async def idle_watcher() -> None:
    """Release the GPU after a quiet spell.

    The card is shared with games, so holding 9.3 GB through an evening of not
    talking is the wrong default.
    """
    from . import settings

    minutes = settings.OPS.get("idle_unload_minutes", 0)
    if not minutes:
        log.info("idle unloading disabled")
        return

    limit = minutes * 60
    log.info("idle unload after %g minutes", minutes)
    while True:
        await asyncio.sleep(min(30.0, limit / 2))
        if models.stt is None and models.tts is None:
            continue
        # _transition held means a load or unload is already running; a load
        # in progress is emphatically not idleness.
        if _in_flight or _transition.locked() or idle_seconds() < limit:
            continue
        log.info("idle for %.0fs — releasing the GPU", idle_seconds())
        try:
            await unload(settings.OPS.get("idle_unload_llm", True))
        except Exception as e:
            log.warning("idle unload failed: %s", e)


async def unload(include_llm: bool = True) -> dict:
    """Release the GPU. Models stay unloaded until something wakes them."""
    async with _transition:
        return await _unload_locked(include_llm)


async def _unload_locked(include_llm: bool) -> dict:
    before = _vram_mb()
    start = time.perf_counter()

    global _llm_up
    models.unload()
    stopped = await _compose("stop") if include_llm else False
    if stopped:
        _llm_up = False

    torch.cuda.empty_cache()
    after = _vram_mb()
    elapsed = (time.perf_counter() - start) * 1000
    log.info("unloaded in %.0fms, freed %d MiB (llm %s)", elapsed, before - after,
             "stopped" if stopped else "left running")
    return {
        "status": "unloaded",
        "llm_stopped": stopped,
        "freed_mb": before - after,
        "vram_used_mb": after,
        "ms": round(elapsed),
    }


async def _await_llm(timeout: float = 120.0) -> bool:
    """Wait until the container answers, not merely until it has been started.

    `docker compose start` returns as soon as the process exists; the model
    still has to be read off disk. Reporting ready before then means the first
    request after a reload fails.
    """
    import httpx

    from . import settings

    url = f"{settings.LLM['url'].rstrip('/')}/health"
    deadline = time.perf_counter() + timeout
    async with httpx.AsyncClient(timeout=5.0) as client:
        while time.perf_counter() < deadline:
            try:
                if (await client.get(url)).status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1.0)
    log.warning("llm container did not become healthy within %.0fs", timeout)
    return False


async def load() -> dict:
    """Bring everything back, warmed and ready to answer."""
    async with _transition:
        return await _load_locked()


async def _load_locked() -> dict:
    global _llm_up
    before = _vram_mb()
    start = time.perf_counter()

    # Each half is brought up only if it is actually missing, so waking one
    # does not needlessly reload the other.
    if _llm_up is not True:
        started = await _compose("start")
        llm_ready = await _await_llm() if started else False
        _llm_up = llm_ready
    else:
        llm_ready = True

    if models.stt is None or models.tts is None:
        await asyncio.to_thread(models.load)
        await asyncio.to_thread(models.warmup)

    after = _vram_mb()
    elapsed = (time.perf_counter() - start) * 1000
    # A cold reload can take half a minute; without this the watcher sees the
    # load itself as idle time and unloads again immediately.
    touch()
    log.info("reloaded in %.0fms, using %d MiB", elapsed, after - before)
    return {
        "status": "ready" if llm_ready else "degraded: llm not answering",
        "llm_ready": llm_ready,
        "used_mb": after - before,
        "vram_used_mb": after,
        "ms": round(elapsed),
    }


def status() -> dict:
    used, total = _vram()
    return {
        "models_loaded": models.stt is not None and models.tts is not None,
        "vram_used_mb": used,
        "vram_total_mb": total,
        "vram_free_mb": total - used,
        # Exposed because an idle unload that never fires is otherwise opaque:
        # these are the three things the watcher checks before acting.
        "llm_up": _llm_up,
        "idle_seconds": round(idle_seconds()),
        "in_flight": _in_flight,
        "transition_locked": _transition.locked(),
    }
