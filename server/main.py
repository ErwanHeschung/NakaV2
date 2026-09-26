"""Naka server: /stt, /chat and /tts stand alone so each can be tested with
curl, and /converse chains them into the streaming voice loop."""

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (agent, connections, conversation, events, llm, logprune,
               logsetup, models, ops, panel, paths, settings, stt, tts,
               turnlog)
from .connections import routes as connection_routes
from .memory import memory
from .tools import apps, builtin, reminders
from .tools.registry import AGENT, listed

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
#
# The wording matters more than it looks. This said "Open with one short remark
# about having just woken... then answer normally", and a model asked to open
# with a remark does exactly that: it returns spoken words, the agent loop sees
# no tool call, and whatever was actually asked for is dropped. With idle
# unloading at a minute, almost every real request arrived just after a wake,
# so "set a timer" and "write a note" reliably produced a warm sentence and no
# timer and no note. Acting comes first here, and the remark is offered for
# whenever she next speaks — which is either this turn or the one after the
# tool result comes back.
WOKE_NOTE = (
    "You were unloaded from the GPU a while ago to free it up and have just "
    "been loaded back, which took about {seconds:.0f} seconds — that is why "
    "there was a pause before this reply. Do what was asked first, using a "
    "tool if that is what it takes. When you next speak, you may work in one "
    "short remark about having just woken. Do not apologise at length and do "
    "not mention it twice."
)

logsetup.configure()
log = logging.getLogger("naka")


TELEGRAM_NOTE = (
    " This message was typed on the user's phone and sent through Telegram, "
    "and your reply goes back there as text: they are away from the PC and "
    "will not hear you. Keep it short."
)


def _forward(text: str, kind: str) -> None:
    """Send a ring to Telegram as well, off the loop. Quiet if not connected."""
    spawn(asyncio.to_thread(connections.telegram().notify, text, kind))


async def timer_watcher():
    """Ring timers and reminders as they come due.

    Half a second is the resolution: finer buys nothing a person can perceive
    in a kitchen timer, and the loop is otherwise free. Both are taken, not
    read, so a slow fan-out cannot ring the same one twice.
    """
    while True:
        await asyncio.sleep(0.5)
        try:
            for timer in builtin.take_due_timers():
                log.info("timer %r came due", timer["label"])
                events.publish("timers", rang=timer["label"], at=time.time())
                _forward(f"Timer: {timer['label']} is up.", "timers")
            for reminder in reminders.take_due():
                log.info("reminder %r came due", reminder["text"])
                late = (" (late: Naka was not running when it was due, at "
                        f"{datetime.fromtimestamp(reminder['due']):%H:%M})"
                        if reminder.get("late") else "")
                events.publish("timers", rang=reminder["text"] + late,
                               kind="reminder", at=time.time())
                _forward(f"Reminder: {reminder['text']}{late}", "reminders")
        except asyncio.CancelledError:
            raise
        except Exception:
            # A watcher that dies takes every future timer with it silently.
            log.exception("timer watcher stumbled, continuing")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Publishing is safe from any thread once this is set, which matters
    # because tools run in a worker.
    events.bind(asyncio.get_running_loop())
    # The speech models load in the background: the server answers from the
    # first second, so the panel and tray can show the load's progress rather
    # than a dead connection, and a failure leaves it running degraded with
    # /health saying why (most often, no usable CUDA device).
    speech = asyncio.create_task(ops.load_speech())
    watcher = asyncio.create_task(ops.idle_watcher())
    pruning = asyncio.create_task(logprune.pruner())
    ringing = asyncio.create_task(timer_watcher())
    # Reading the Start menu and the game libraries takes a second or two;
    # done now, the first "open Fortnite" does not wait for it.
    apps.warm()
    connections.telegram().on_message = telegram_turn
    polling = asyncio.create_task(connections.telegram().run())
    log.info("ready on %s:%s", settings.SERVER["host"], settings.SERVER["port"])
    yield
    speech.cancel()
    watcher.cancel()
    pruning.cancel()
    ringing.cancel()
    polling.cancel()
    await llm.aclose()
    await ops.shutdown()
    models.unload()


app = FastAPI(title=settings.IDENTITY["assistant"], lifespan=lifespan)

# The control panel, served from the same origin as the API it calls — which
# keeps it on loopback and means no CORS. Mounted only if it has been built,
# so the server still starts on a machine where the UI was never compiled.
_UI = paths.UI


class Panel(StaticFiles):
    """Static files that are always revalidated.

    Without Cache-Control, a browser is free to guess how long a file stays
    fresh — roughly a tenth of its age — and serve it from cache without
    asking. The panel's own files change constantly, so that guess produces a
    page assembled from different builds: new main.js against a style.css
    from ten minutes ago, which is how a stylesheet rule that was definitely
    in the file on disk was definitely not in the browser.

    no-cache does not mean do not store. The browser keeps the file and asks
    whether it changed; the ETag makes the answer a 304 with no body. Over
    loopback that costs nothing and removes the whole class of problem.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


# Gated on the built entry point, not index.html: index.html is tracked in git
# and always present, while js/ only exists after `npm run build`. Checking the
# wrong one mounted an unbuilt panel that then 404'd in the browser with no
# sign of it here — the opposite of what the comment above promises.
if (_UI / "js" / "main.js").exists():
    app.mount("/ui", Panel(directory=_UI, html=True), name="ui")
    log.info("control panel at http://%s:%s/ui",
             settings.SERVER["host"], settings.SERVER["port"])


class TextIn(BaseModel):
    text: str
    # Per request, never globally: most utterances are conversation and should
    # not have tools in reach at all.
    agentic: bool = AGENT["default_agentic"]


def reply_stream(user_text: str, agentic: bool, messages=None, actions=None,
                 *, tainted: bool = False,
                 withhold: frozenset[str] = frozenset(), origin: str = "pc"):
    """Choose between plain streaming and the agentic loop.

    A confirmation left pending by a previous turn takes priority: this
    utterance is the user's answer to it, not a new request.
    """
    async def generate():
        if agent.answers_pending(origin):
            async for sentence in agent.resolve_pending(user_text, actions):
                yield sentence
            return

        nonlocal messages
        if messages is None:
            messages = memory.messages(user_text)
        if agentic:
            async for sentence in agent.run(messages, actions,
                                            tainted=tainted,
                                            withhold=withhold,
                                            origin=origin):
                yield sentence
        else:
            async for sentence in llm.stream_sentences(messages):
                yield sentence

    return generate()


app.include_router(panel.router)
app.include_router(connection_routes.router)


@app.get("/events")
async def events_stream():
    """Open for as long as the panel is. Timers ring down this."""
    return StreamingResponse(
        events.stream(),
        media_type="text/event-stream",
        # Buffering anywhere in between would hold a ring until the next
        # event pushed it out, which for a timer is the whole point missed.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/health")
async def health():
    return {"status": "ok" if models.load_error is None else "degraded",
            "loading": ops.loading(),
            "stt": models.stt is not None, "tts": models.tts is not None,
            "error": models.load_error}


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
    # Shown in the chat before the models are woken, which can take seconds.
    turn_id = conversation.next_id()
    conversation.heard(turn_id, body.text, "text")
    async with ops.Busy():
        woke = await ops.ensure_loaded()
    messages = memory.messages(body.text, turn_note(woke))

    async def generate():
        start = time.perf_counter()
        first = None
        spoken = []
        actions: list[dict] = []
        finished = False
        try:
            # The whole reply, not just the load, the way /converse does it.
            # Marked only around ensure_loaded, a reply was in flight with
            # nothing saying so: an agentic turn may run for the full tool
            # timeout, and the idle watcher was free to pull the models out
            # from under it.
            async with ops.Busy():
                async for sentence in reply_stream(body.text, body.agentic,
                                                   messages, actions):
                    now = (time.perf_counter() - start) * 1000
                    if first is None:
                        first = now
                        log.info("llm first sentence %.0fms", now)
                    spoken.append(sentence)
                    conversation.said(turn_id, sentence)
                    yield json.dumps({"id": turn_id, "sentence": sentence,
                                      "ms": round(now)}) + "\n"
            finished = True
        finally:
            if not finished:
                interrupted(turn_id, body.text, spoken, actions, "text")
        await remember(body.text, " ".join(spoken), actions)
        conversation.record(turn_id, body.text, " ".join(spoken), via="text",
                            tools=actions)

    return StreamingResponse(generate(), media_type="application/x-ndjson")


async def telegram_turn(text: str) -> str:
    """A message from the paired Telegram chat, answered as a text turn.

    Tainted from the start, like a turn that has read a web page: it was
    typed on a phone, not said at the PC, so anything that changes something
    asks first, and PowerShell is not offered at all. A confirmation it asks
    for is answered by the next message, from wherever it comes.
    """
    turn_id = conversation.next_id()
    conversation.heard(turn_id, text, "telegram")
    async with ops.Busy():
        woke = await ops.ensure_loaded()
    messages = memory.messages(text, turn_note(woke) + TELEGRAM_NOTE)
    spoken: list[str] = []
    actions: list[dict] = []
    async with ops.Busy():
        async for sentence in reply_stream(
                text, settings.CLIENT["use_tools"], messages, actions,
                tainted=True, withhold=frozenset({"shell"}),
                origin="telegram"):
            spoken.append(sentence)
            conversation.said(turn_id, sentence)
    reply = " ".join(spoken)
    await remember(text, reply, actions)
    conversation.record(turn_id, text, reply, via="telegram", tools=actions)
    return reply


def interrupted(turn_id: int, user_text: str, spoken: list[str],
                actions: list[dict], via: str) -> None:
    """Keep a reply that was cut off, as far as it got.

    Runs while the response is being torn down — the person pressed the talk
    key again, or closed the panel — so nothing here may await. Without it
    the exchange vanished: the model would not know what it had started
    saying, and the chat would not show that anything was asked.
    """
    reply = " ".join(spoken)
    log.info("turn %d interrupted after %d sentences", turn_id, len(spoken))
    memory.add_turn(user_text, (reply + " [interrupted]").strip(), actions)
    events.publish("conversation")
    conversation.record(turn_id, user_text, reply, via=via, tools=actions,
                        interrupted=True)


# Background tasks, held so they are not collected mid-flight.
#
# asyncio keeps only a weak reference to a bare create_task, so a task
# suspended on an LLM call can be garbage-collected and cancelled before it
# finishes — a remembered fact quietly lost. Holding them also gives
# somewhere to attach the error handler that was missing when summarise()
# started raising on every turn and nothing said a word.
_background: set[asyncio.Task] = set()


def spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)
    task.add_done_callback(_report)


def _report(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        log.exception("background task failed", exc_info=error)


async def remember(user_text: str, reply: str,
                   actions: list[dict] | None = None) -> None:
    """Record the turn, and fold older ones in when enough have accumulated.

    Summarising is a second generation, so it runs as a background task — the
    user is not made to wait for it inside the latency budget.
    """
    memory.add_turn(user_text, reply, actions)
    # So the conversation appears as it happens rather than on the next poll.
    events.publish("conversation")
    # Both run off the response path: each is another generation, and neither
    # is worth making the user wait for.
    spawn(_reconcile_then_announce(user_text, reply))
    if memory.needs_summary():
        spawn(memory.summarise())


async def _reconcile_then_announce(user_text: str, reply: str) -> None:
    """Facts change after the reply is already being spoken, so the panel is
    told when it happens rather than being left to notice."""
    await memory.reconcile(user_text, reply)
    events.publish("memory")


@app.get("/conversation")
async def conversation_page(before: int | None = None, limit: int = 20):
    """The chat history, a page at a time, newest last."""
    return conversation.page(before, limit)


@app.get("/memory")
async def memory_state():
    return {
        "facts": memory.facts,
        "summary": memory.summary,
        "recent": [{"user": t.user, "naka": t.reply} for t in memory.recent],
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
    events.publish("memory")
    events.publish("conversation")
    return {"status": "cleared"}


@app.get("/tools")
async def tools_state():
    return {
        # Every allowlisted tool, not only those offered right now: a power
        # that is switched off is shown as off, rather than its tools
        # silently missing from the list.
        "allowed": [
            {"name": t.name, "label": t.label, "summary": t.summary,
             "power": t.power, "enabled": t.enabled, "confirms": t.confirms,
             "destructive": t.destructive, "description": t.description}
            for t in listed()
        ],
        "powers": {p: bool(settings.POWERS.get(p)) for p in agent.POWERS},
        "connections": {name: {"label": c.label, "on": c.on(),
                               "ready": c.ready()}
                        for name, c in connections.ALL.items()},
        "max_steps": agent.limits()[0],
        "timeout_s": agent.limits()[1],
        "awaiting_confirmation": agent.pending_view(),
    }


@app.get("/ops/status")
async def ops_status():
    return await ops.status()


@app.post("/ops/wake")
async def ops_wake():
    """Start loading without waiting for it.

    The client calls this the moment the talk key goes down, so the reload
    overlaps with the user speaking instead of following it.
    """
    ops.touch()
    # All three, not just the speech models: checking only those meant a key
    # press after the language model had been stopped on its own answered
    # "already awake" and left the reload to land in the reply's latency.
    if ops._fully_up():
        return {"status": "already awake"}
    spawn(ops.ensure_loaded())
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
    # Whoever is playing the answer stops too: the tray, when the stop came
    # from the panel's button rather than its own talk key.
    events.publish("client", interrupt=True)
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
async def converse(file: UploadFile,
                   agentic: bool = Form(AGENT["default_agentic"]),
                   format: str = Form("opus")):
    """Audio in, streamed audio out. The whole loop, which is Phase 1's point.

    Two output formats, for two clients. The Python client takes Ogg Opus,
    which is what you want over a socket. The browser cannot stream Ogg — no
    MediaSource support for it — so the panel takes raw mono 16-bit PCM at the
    TTS rate and schedules the chunks itself. Over loopback the bandwidth
    difference is irrelevant, and it keeps the reply streaming sentence by
    sentence in both, which is the part that actually decides felt latency.
    """
    if format not in ("opus", "pcm"):
        raise HTTPException(status_code=400, detail="format must be opus or pcm")
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
    # Announced now, before a word of the answer exists: the chat shows what
    # was heard while she is still thinking about it.
    turn_id = conversation.next_id()
    conversation.heard(turn_id, heard, "voice")

    async def generate():
        stream = tts.OpusStream() if format == "opus" else None
        first_sound = None
        spoken = []
        actions: list[dict] = []
        finished = False

        try:
            # Its own span rather than one carried across the generator
            # boundary, so a client that hangs up mid-reply still releases
            # the marker.
            async with ops.Busy():
                async for sentence in reply_stream(heard, agentic, prompt, actions):
                    async with models.gpu_lock:
                        samples = await asyncio.to_thread(tts.synthesize, sentence)
                        chunk = (await asyncio.to_thread(stream.push, samples)
                                 if stream else tts.to_pcm16(samples))
                    spoken.append(sentence)
                    conversation.said(turn_id, sentence)
                    if not chunk:
                        # The Ogg muxer emits whole pages; until one is
                        # complete there is nothing to send, so this is not
                        # yet first sound.
                        continue
                    if first_sound is None:
                        first_sound = (time.perf_counter() - start) * 1000
                        log.info("FIRST SOUND %.0fms (first bytes on the wire)",
                                 first_sound)
                    yield chunk

                if stream:
                    tail = await asyncio.to_thread(stream.close)
                    if tail:
                        yield tail
            finished = True
        finally:
            # The client hung up: pressed the talk key to interrupt, most
            # likely. What was said so far is kept, marked as cut off.
            if not finished:
                interrupted(turn_id, heard, spoken, actions, "voice")

        await remember(heard, " ".join(spoken), actions)
        conversation.record(turn_id, heard, " ".join(spoken), via="voice",
                            tools=actions)
        total = (time.perf_counter() - start) * 1000
        turnlog.record(
            heard=heard, messages=prompt, spoken=spoken, agentic=agentic,
            tools=actions,
            timings={"upload": t_upload, "stt": t_stt - t_upload,
                     "first_sound": first_sound or 0, "total": total},
        )
        log.info("reply complete %.0fms %r", total, " ".join(spoken))

    if format == "pcm":
        return StreamingResponse(
            generate(), media_type="audio/L16",
            # The panel needs the rate to schedule the chunks, and reading it
            # from a header keeps it from being hardcoded in two places.
            headers={"X-Sample-Rate": str(settings.TTS["sample_rate"])},
        )
    return StreamingResponse(generate(), media_type="audio/ogg")


