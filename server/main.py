"""Naka server: /stt, /chat and /tts stand alone so each can be tested with
curl, and /converse chains them into the streaming voice loop."""

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import llm, models, settings, stt, tts

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-12s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("naka")


@asynccontextmanager
async def lifespan(app: FastAPI):
    models.load()
    models.warmup()
    log.info("ready on %s:%s", settings.SERVER["host"], settings.SERVER["port"])
    yield
    models.unload()


app = FastAPI(title="Naka", lifespan=lifespan)


class TextIn(BaseModel):
    text: str


@app.get("/health")
async def health():
    return {"status": "ok", "stt": models.stt is not None,
            "tts": models.tts is not None}


@app.post("/stt")
async def stt_endpoint(file: UploadFile):
    data = await file.read()
    start = time.perf_counter()
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
    messages = llm.build_messages(body.text)

    async def generate():
        start = time.perf_counter()
        first = None
        async for sentence in llm.stream_sentences(messages):
            now = (time.perf_counter() - start) * 1000
            if first is None:
                first = now
                log.info("llm first sentence %.0fms", now)
            yield json.dumps({"sentence": sentence, "ms": round(now)}) + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@app.post("/tts")
async def tts_endpoint(body: TextIn):
    start = time.perf_counter()
    async with models.gpu_lock:
        samples = await asyncio.to_thread(tts.synthesize, body.text)
        audio = await asyncio.to_thread(tts.encode_opus, samples)
    log.info("tts %.0fms %d samples", (time.perf_counter() - start) * 1000,
             samples.size)
    return StreamingResponse(iter([audio]), media_type="audio/ogg")


@app.post("/converse")
async def converse(file: UploadFile):
    """Audio in, streamed Opus out. The whole loop, which is Phase 1's point."""
    # Clock starts before the body is read: the upload crosses the WSL2
    # boundary from the Windows client, and timing from after the read hides
    # that cost entirely.
    start = time.perf_counter()
    data = await file.read()
    t_upload = (time.perf_counter() - start) * 1000

    async with models.gpu_lock:
        heard = await asyncio.to_thread(stt.transcribe, data)
    t_stt = (time.perf_counter() - start) * 1000
    log.info("upload %.0fms (%d KB) | stt %.0fms %r",
             t_upload, len(data) // 1024, t_stt - t_upload, heard)

    if not heard:
        raise HTTPException(status_code=400, detail="no speech detected")

    async def generate():
        stream = tts.OpusStream()
        first_sound = None
        spoken = []

        async for sentence in llm.stream_sentences(llm.build_messages(heard)):
            async with models.gpu_lock:
                samples = await asyncio.to_thread(tts.synthesize, sentence)
                chunk = await asyncio.to_thread(stream.push, samples)
            spoken.append(sentence)
            if not chunk:
                # The Ogg muxer emits whole pages; until one is complete there
                # is genuinely nothing to send, so this is not yet first sound.
                continue
            if first_sound is None:
                first_sound = (time.perf_counter() - start) * 1000
                log.info("FIRST SOUND %.0fms (first bytes on the wire)",
                         first_sound)
            yield chunk

        tail = await asyncio.to_thread(stream.close)
        if tail:
            yield tail
        log.info("reply complete %.0fms %r",
                 (time.perf_counter() - start) * 1000, " ".join(spoken))

    return StreamingResponse(generate(), media_type="audio/ogg")
