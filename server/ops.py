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
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=10,
    )
    return int(result.stdout.strip().splitlines()[0])


async def _compose(verb: str) -> str:
    process = await asyncio.create_subprocess_exec(
        *_DOCKER, verb, "llm",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await process.communicate()
    return out.decode().strip()


# Serialises load/unload against each other and against requests. Without it,
# pre-warming from the client and a request arriving a moment later would both
# start a load, and each would pay the full cost.
_transition = asyncio.Lock()
_last_activity = time.monotonic()
# Requests currently being served. The idle watcher must not pull the models
# out from under one, and a long reload must not count as idle time.
_in_flight = 0


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


async def ensure_loaded() -> None:
    """Load if needed, or wait for a load already in flight. Idempotent."""
    if models.stt is not None and models.tts is not None:
        return
    async with _transition:
        if models.stt is None or models.tts is None:
            log.info("waking on demand")
            await _load_locked()


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

    models.unload()
    if include_llm:
        await _compose("stop")

    torch.cuda.empty_cache()
    after = _vram_mb()
    elapsed = (time.perf_counter() - start) * 1000
    log.info("unloaded in %.0fms, freed %d MiB", elapsed, before - after)
    return {
        "status": "unloaded",
        "llm_stopped": include_llm,
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
    before = _vram_mb()
    start = time.perf_counter()

    await _compose("start")
    llm_ready = await _await_llm()
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
    used = _vram_mb()
    return {
        "models_loaded": models.stt is not None and models.tts is not None,
        "vram_used_mb": used,
        "vram_free_mb": 16303 - used,
    }
