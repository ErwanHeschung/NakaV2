"""Operational control: releasing the GPU and taking it back.

The machine is also used for games, and a recent one wants 10-14 GB. Naka
holds roughly 9.5 GB across two processes — the Python server's STT and TTS,
and llama-server.exe, which is the larger share. Residency is deliberate
(reloading per utterance would cost seconds every time), so releasing it has to
be an explicit action.

These are operator controls reached over HTTP, and deliberately NOT registered
as tools: the model must not be able to unload itself mid-sentence, nor be
talked into stopping its own server.

llama-server.exe is our child process. It used to be a Docker container whose
state was independent of ours and could only be inferred; now it is a process
we started, whose exit we see the moment it happens and whose output we keep.
"""

import asyncio
import logging
import os
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

import torch

from . import llamacpp, models, winjob

log = logging.getLogger("naka.ops")
server_log = logging.getLogger("naka.llm.server")

# The standard install location, for when the driver did not put nvidia-smi on
# PATH — which some driver packages do not.
_NVSMI = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) \
    / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe"


def _vram_mb() -> int:
    return _vram()[0]


def _vram() -> tuple[int, int]:
    """(used, total) in MiB, or (0, 0) if nvidia-smi cannot be read.

    The total is asked for rather than assumed: this ships to whatever card the
    user has, and a hardcoded size turns into a wrong percentage on every other
    machine. Failure is a zero rather than an exception because this is on the
    path of /ops/status, which the panel polls every two seconds — a missing
    binary used to turn that into a stream of 500s.
    """
    for exe in ("nvidia-smi", str(_NVSMI)):
        try:
            result = subprocess.run(
                [exe, "--query-gpu=memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10,
                creationflags=_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode != 0 or not result.stdout.strip():
            continue
        try:
            used, total = result.stdout.strip().splitlines()[0].split(",")
            return int(used), int(total)
        except ValueError:
            continue
    return 0, 0


# Without CREATE_NO_WINDOW every child is given a console, and with idle
# unloading at a minute that is a black window flashing up several times an
# evening. The process group lets a stop signal reach it without reaching us.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
_SPAWN_FLAGS = (_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
                if sys.platform == "win32" else 0)

# Serialises load/unload against each other and against requests. Without it,
# pre-warming from the client and a request arriving a moment later would both
# start a load, and each would pay the full cost.
_transition = asyncio.Lock()
_last_activity = time.monotonic()
# Requests currently being served. The idle watcher must not pull the models
# out from under one, and a long reload must not count as idle time.
_in_flight = 0

# The llama-server child, if one is running.
_llm: asyncio.subprocess.Process | None = None
# Whether it has answered /health since it last started. Separate from being
# alive: a process loading a 7 GB model is alive for several seconds before it
# can answer anything.
_llm_up = False
# Its last lines of output. A model it cannot load, a card it cannot use, a
# port already taken — all of it is explained here and nowhere else, and the
# container kept none of it.
_llm_tail: deque[str] = deque(maxlen=20)
_llm_exit: int | None = None
# Set while we are stopping it on purpose, so the exit is not reported as a crash.
_llm_stopping = False


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


def _llm_alive() -> bool:
    return _llm is not None and _llm.returncode is None


def _fully_up() -> bool:
    return (models.stt is not None and models.tts is not None
            and _llm_alive() and _llm_up)


async def ensure_loaded() -> float:
    """Bring up whatever is missing. Idempotent, and cheap when nothing is.

    Checks the language model as well as the local models: the two have
    separate lifetimes, and a server that has just started loads its own models
    while llama-server has not been started at all.

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
        if models.stt is None and models.tts is None and not _llm_alive():
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


# ------------------------------------------------------------ llama-server


async def _start_llm() -> bool:
    """Start llama-server. Returns whether a process is now running."""
    global _llm, _llm_up, _llm_exit, _llm_stopping
    if _llm_alive():
        return True

    why = llamacpp.missing()
    if why:
        log.error("cannot start the language model: %s", why)
        _llm_tail.clear()
        _llm_tail.append(why)
        return False

    _llm_tail.clear()
    _llm_up = False
    _llm_exit = None
    _llm_stopping = False
    _llm = await asyncio.create_subprocess_exec(
        *llamacpp.argv(),
        cwd=str(llamacpp.BIN.parent),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        creationflags=_SPAWN_FLAGS,
    )
    winjob.contain(_llm.pid)
    asyncio.create_task(_drain(_llm))
    log.info("llama-server started (pid %d, %s)", _llm.pid,
             llamacpp.model_path().name)
    return True


async def _drain(process: asyncio.subprocess.Process) -> None:
    """Keep its output, and notice the moment it exits.

    Output must be read whether or not anyone wants it: an unread pipe fills,
    and a child blocked writing to a full pipe stops answering requests.
    """
    global _llm_up, _llm_exit
    assert process.stdout is not None
    async for raw in process.stdout:
        line = raw.decode("utf-8", errors="replace").rstrip()
        if line:
            _llm_tail.append(line)
            server_log.debug("%s", line)
    code = await process.wait()
    if process is not _llm:
        return
    _llm_up = False
    _llm_exit = code
    if not _llm_stopping:
        # Under Docker a crashed container looked healthy until the next
        # request timed out, because nothing was watching it. This is the
        # watching.
        log.error("llama-server exited unexpectedly (code %s). Last output:\n  %s",
                  code, "\n  ".join(list(_llm_tail)[-8:]))


async def _stop_llm() -> bool:
    """Stop llama-server if it is running. Returns whether one was stopped."""
    global _llm_up, _llm_stopping
    process = _llm
    if process is None or process.returncode is not None:
        return False
    _llm_stopping = True
    _llm_up = False
    # terminate() is TerminateProcess on Windows: immediate, no chance to clean
    # up. llama-server has nothing to flush — the model is read-only — so there
    # is nothing to lose, and the VRAM is back the moment it returns.
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=10)
    except TimeoutError:
        process.kill()
        await process.wait()
    log.info("llama-server stopped")
    return True


async def _await_llm(timeout: float = 120.0) -> bool:
    """Wait until it answers, not merely until it has been started.

    The process exists long before the model is read off disk, and reporting
    ready before then means the first request after a reload fails. Gives up
    at once if the process exits while we wait, rather than polling a dead
    port for two minutes.
    """
    import httpx

    url = f"http://127.0.0.1:{llamacpp.port()}/health"
    deadline = time.perf_counter() + timeout
    async with httpx.AsyncClient(timeout=5.0) as client:
        while time.perf_counter() < deadline:
            if not _llm_alive():
                log.error("llama-server exited while loading")
                return False
            try:
                if (await client.get(url)).status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.5)
    log.warning("llama-server did not become healthy within %.0fs", timeout)
    return False


async def shutdown() -> None:
    """Stop llama-server as the server exits.

    The job object in winjob would kill it anyway once this process is gone;
    doing it here means it goes promptly and the stop is logged as intended.
    """
    await _stop_llm()


# ------------------------------------------------------------ load / unload


async def unload(include_llm: bool = True) -> dict:
    """Release the GPU. Models stay unloaded until something wakes them."""
    async with _transition:
        return await _unload_locked(include_llm)


async def _unload_locked(include_llm: bool) -> dict:
    before = await asyncio.to_thread(_vram_mb)
    start = time.perf_counter()

    models.unload()
    stopped = await _stop_llm() if include_llm else False

    torch.cuda.empty_cache()
    after = await asyncio.to_thread(_vram_mb)
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


async def load() -> dict:
    """Bring everything back, warmed and ready to answer."""
    async with _transition:
        return await _load_locked()


async def _load_locked() -> dict:
    global _llm_up
    before = await asyncio.to_thread(_vram_mb)
    start = time.perf_counter()

    # Each half is brought up only if it is actually missing, so waking one
    # does not needlessly reload the other.
    if not (_llm_alive() and _llm_up):
        started = await _start_llm()
        _llm_up = await _await_llm() if started else False
    llm_ready = _llm_up

    if models.stt is None or models.tts is None:
        await asyncio.to_thread(models.load)
        await asyncio.to_thread(models.warmup)

    after = await asyncio.to_thread(_vram_mb)
    elapsed = (time.perf_counter() - start) * 1000
    # A cold reload can take half a minute; without this the watcher sees the
    # load itself as idle time and unloads again immediately.
    touch()
    log.info("reloaded in %.0fms, using %d MiB", elapsed, after - before)
    return {
        "status": "ready" if llm_ready else "degraded: language model not answering",
        "llm_ready": llm_ready,
        "used_mb": after - before,
        "vram_used_mb": after,
        "ms": round(elapsed),
    }


async def status() -> dict:
    # nvidia-smi is a subprocess that regularly takes 100ms+ while the GPU is
    # busy, and the panel asks for this every two seconds. On the loop that is
    # a stutter in whatever reply is streaming at the time.
    used, total = await asyncio.to_thread(_vram)
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
        # And these are why the language model is down, when it is.
        "llm_pid": _llm.pid if _llm_alive() else None,
        "llm_exit_code": _llm_exit,
        "llm_tail": list(_llm_tail)[-5:],
    }
