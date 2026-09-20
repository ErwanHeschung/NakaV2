"""Naka server: /stt, /chat and /tts stand alone so each can be tested with
curl, and /converse chains them into the streaming voice loop."""

import asyncio
import json
import logging
import time
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import agent, llm, logprune, models, ops, settings, stt, tts, turnlog
from .memory import memory
from .tools.registry import AGENT, available

def turn_note(woke: float) -> str:
    """Per-turn context: the clock, and whether she has just been woken.

    Injected just before the user's message rather than into the system
    prompt, because it changes every turn and llama.cpp caches the longest
    common prefix — putting a moving timestamp at position zero would throw
    that cache away on every single reply.

    She is given the time rather than told not to guess it: telling a model
    not to invent a detail is far less reliable than removing its reason to.
    """
    now = datetime.now()
    parts = [f"Right now it is {now.strftime('%H:%M on %A %d %B %Y')}."]
    if woke:
        parts.append(WOKE_NOTE.format(seconds=woke))
    return " ".join(parts)


# Waking is slow enough to need explaining — the models have to come back off
# disk. Naka says so herself rather than reciting a canned line, so it stays in
# character and does not become the same sentence every time.
WOKE_NOTE = (
    "You were unloaded from the GPU a while ago to free it up, and have just "
    "been loaded back — which took about {seconds:.0f} seconds, and is why "
    "there was a pause before you answered. Open with one short remark about "
    "having just woken and why it took a moment, in your own voice, then "
    "answer normally. Do not apologise at length or mention this again."
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-12s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("naka")


@asynccontextmanager
async def lifespan(app: FastAPI):
    models.load()
    models.warmup()
    watcher = asyncio.create_task(ops.idle_watcher())
    pruning = asyncio.create_task(logprune.pruner())
    log.info("ready on %s:%s", settings.SERVER["host"], settings.SERVER["port"])
    yield
    watcher.cancel()
    pruning.cancel()
    models.unload()


app = FastAPI(title=settings.IDENTITY["assistant"], lifespan=lifespan)


class TextIn(BaseModel):
    text: str
    # Per request, never globally: most utterances are conversation and should
    # not have tools in reach at all.
    agentic: bool = AGENT["default_agentic"]


def reply_stream(user_text: str, agentic: bool, messages=None):
    """Choose between plain streaming and the agentic loop.

    A confirmation left pending by a previous turn takes priority: this
    utterance is the user's answer to it, not a new request.
    """
    async def generate():
        settled = await agent.resolve_pending(user_text)
        if settled is not None:
            for sentence in llm.split_sentences(settled):
                yield sentence
            return

        nonlocal messages
        if messages is None:
            messages = memory.messages(user_text)
        if agentic:
            async for sentence in agent.run(messages):
                yield sentence
        else:
            async for sentence in llm.stream_sentences(messages):
                yield sentence

    return generate()


@app.get("/health")
async def health():
    return {"status": "ok", "stt": models.stt is not None,
            "tts": models.tts is not None}


@app.post("/stt")
async def stt_endpoint(file: UploadFile):
    data = await file.read()
    start = time.perf_counter()
    async with ops.Busy():
        await ops.ensure_loaded()
        async with models.gpu_lock:
            try:
                text = await asyncio.to_thread(stt.transcribe, data)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
    elapsed = (time.perf_counter() - start) * 1000
    log.info("stt %.0fms %r", elapsed, text)
    return {"text": text, "ms": round(elapsed)}


@app.post("/chat")
async def chat_endpoint(body: TextIn):
    """Newline-delimited JSON, one object per sentence, as they are produced."""
    async with ops.Busy():
        woke = await ops.ensure_loaded()
    messages = memory.messages(body.text, turn_note(woke))

    async def generate():
        start = time.perf_counter()
        first = None
        spoken = []
        async for sentence in reply_stream(body.text, body.agentic, messages):
            now = (time.perf_counter() - start) * 1000
            if first is None:
                first = now
                log.info("llm first sentence %.0fms", now)
            spoken.append(sentence)
            yield json.dumps({"sentence": sentence, "ms": round(now)}) + "\n"
        await remember(body.text, " ".join(spoken))

    return StreamingResponse(generate(), media_type="application/x-ndjson")


async def remember(user_text: str, reply: str) -> None:
    """Record the turn, and fold older ones in when enough have accumulated.

    Summarising is a second generation, so it runs as a background task — the
    user is not made to wait for it inside the latency budget.
    """
    memory.add_turn(user_text, reply)
    # Both run off the response path: each is another generation, and neither
    # is worth making the user wait for.
    asyncio.create_task(memory.reconcile(user_text, reply))
    if memory.needs_summary():
        asyncio.create_task(memory.summarise())


@app.get("/memory")
async def memory_state():
    return {
        "facts": memory.facts,
        "summary": memory.summary,
        "recent": [{"user": u, "naka": a} for u, a in memory.recent],
        "pending_summary": len(memory.pending),
    }


@app.post("/memory/reload")
async def memory_reload():
    """Re-read persona.md and facts.json so they can be edited live."""
    memory.reload()
    return {"status": "reloaded", "facts": memory.facts}


@app.post("/memory/forget")
async def memory_forget():
    memory.forget()
    return {"status": "cleared"}


@app.get("/tools")
async def tools_state():
    return {
        "allowed": [
            {"name": t.name, "destructive": t.destructive,
             "description": t.description}
            for t in available()
        ],
        "max_steps": AGENT["max_steps"],
        "timeout_s": AGENT["timeout"],
        "awaiting_confirmation": agent.pending,
    }


@app.get("/ops/status")
async def ops_status():
    return ops.status()


@app.post("/ops/wake")
async def ops_wake():
    """Start loading without waiting for it.

    The client calls this the moment the talk key goes down, so the reload
    overlaps with the user speaking instead of following it.
    """
    ops.touch()
    if models.stt is not None and models.tts is not None:
        return {"status": "already awake"}
    asyncio.create_task(ops.ensure_loaded())
    return {"status": "waking"}


@app.post("/ops/unload")
async def ops_unload(include_llm: bool = True):
    """Release the GPU for something else — a game, usually."""
    return await ops.unload(include_llm)


@app.post("/ops/load")
async def ops_load():
    """Bring the models back, warmed."""
    return await ops.load()


@app.post("/agent/stop")
async def agent_stop():
    """Kill switch. The client binds this to a hotkey."""
    agent.kill_switch.trip()
    return {"status": "stopping"}


@app.post("/tts")
async def tts_endpoint(body: TextIn):
    start = time.perf_counter()
    async with ops.Busy():
        await ops.ensure_loaded()
        async with models.gpu_lock:
            samples = await asyncio.to_thread(tts.synthesize, body.text)
            audio = await asyncio.to_thread(tts.encode_opus, samples)
    log.info("tts %.0fms %d samples", (time.perf_counter() - start) * 1000,
             samples.size)
    return StreamingResponse(iter([audio]), media_type="audio/ogg")


@app.post("/converse")
async def converse(file: UploadFile, agentic: bool = Form(AGENT["default_agentic"])):
    """Audio in, streamed Opus out. The whole loop, which is Phase 1's point."""
    # Clock starts before the body is read: the upload crosses the WSL2
    # boundary from the Windows client, and timing from after the read hides
    # that cost entirely.
    start = time.perf_counter()
    data = await file.read()
    t_upload = (time.perf_counter() - start) * 1000

    async with ops.Busy():
        woke = await ops.ensure_loaded()
        async with models.gpu_lock:
            heard = await asyncio.to_thread(stt.transcribe, data)
    t_stt = (time.perf_counter() - start) * 1000
    log.info("upload %.0fms (%d KB) | stt %.0fms %r",
             t_upload, len(data) // 1024, t_stt - t_upload, heard)

    if not heard:
        raise HTTPException(status_code=400, detail="no speech detected")

    prompt = memory.messages(heard, turn_note(woke))

    async def generate():
        stream = tts.OpusStream()
        first_sound = None
        spoken = []

        # Its own span rather than one carried across the generator boundary,
        # so a client that hangs up mid-reply still releases the marker.
        async with ops.Busy():
            async for sentence in reply_stream(heard, agentic, prompt):
                async with models.gpu_lock:
                    samples = await asyncio.to_thread(tts.synthesize, sentence)
                    chunk = await asyncio.to_thread(stream.push, samples)
                spoken.append(sentence)
                if not chunk:
                    # The Ogg muxer emits whole pages; until one is complete
                    # there is nothing to send, so this is not yet first sound.
                    continue
                if first_sound is None:
                    first_sound = (time.perf_counter() - start) * 1000
                    log.info("FIRST SOUND %.0fms (first bytes on the wire)",
                             first_sound)
                yield chunk

            tail = await asyncio.to_thread(stream.close)
            if tail:
                yield tail

        await remember(heard, " ".join(spoken))
        total = (time.perf_counter() - start) * 1000
        turnlog.record(
            heard=heard, messages=prompt, spoken=spoken, agentic=agentic,
            timings={"upload": t_upload, "stt": t_stt - t_upload,
                     "first_sound": first_sound or 0, "total": total},
        )
        log.info("reply complete %.0fms %r", total, " ".join(spoken))

    return StreamingResponse(generate(), media_type="audio/ogg")
