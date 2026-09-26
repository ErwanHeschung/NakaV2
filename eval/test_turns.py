"""The three ways a turn arrives, end to end through the server, without models.

Typed (/chat), spoken (/converse) and from Telegram all hear, wake, answer,
remember and record. This drives each with a scripted model and stand-ins
for Whisper and Kokoro, and checks what reached the conversation history and
memory, including a reply cut off by the client and one that failed halfway.

Nothing touches the person's history, logs or facts: every path is moved to
a scratch folder first.

Usage: uv run python eval/test_turns.py
"""

import asyncio
import io
import json
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server import (conversation, llm, main, models,  # noqa: E402
                    ops, stt, tts, turnlog)
from server.memory import memory  # noqa: E402

PASS, FAIL = "  ok  ", " FAIL "
failures = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global failures
    print(f"{PASS if condition else FAIL} {label}")
    if not condition:
        failures += 1
        if detail:
            print(f"        {detail}")


scratch = Path(tempfile.mkdtemp(prefix="naka-turns-"))
conversation.PATH = scratch / "conversation.jsonl"
conversation._turns = []
turnlog.PATH = scratch / "turns.jsonl"
memory.save_facts = lambda: None
memory.facts = []
memory.recent.clear()
memory.summary = ""

script: list = []
agent_calls: list[dict] = []


async def fake_stream(messages, tools, max_tokens=None):
    reply = script.pop(0) if script else ["Fine."]
    for part in reply:
        if isinstance(part, Exception):
            raise part
        yield "sentence", part
    yield "finish", "stop"


async def fake_complete(messages):
    return "NOOP"


real_run = main.agent.run


def spy_run(messages, actions=None, **options):
    agent_calls.append(options)
    return real_run(messages, actions, **options)


async def awake():
    return 0.0


llm.stream_with_tools = fake_stream
llm.complete = fake_complete
main.agent.run = spy_run
ops.ensure_loaded = awake
stt.transcribe = lambda data: "what time is it"
tts.synthesize = lambda text, apply_dsp=True: np.zeros(2400, dtype=np.float32)
models.stt = models.tts = object()


def wav() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b"\0\0" * 1600)
    return buffer.getvalue()


def last() -> dict:
    return conversation._turns[-1]


async def settle() -> None:
    # remember() spawns the reconciler; let it finish before looking.
    for _ in range(20):
        await asyncio.sleep(0)


def run() -> None:
    client = TestClient(main.app)

    script[:] = [["It is noon. ", "Anything else?"]]
    lines = [json.loads(line) for line in client.post(
        "/chat", json={"text": "hello", "agentic": True}).text.splitlines()]
    check("/chat streams each sentence with the turn's id",
          [l["sentence"] for l in lines] == ["It is noon. ", "Anything else?"]
          and len({l["id"] for l in lines}) == 1, str(lines))
    check("/chat records the exchange as text",
          last()["user"] == "hello" and last()["via"] == "text"
          and last()["naka"] == "It is noon. Anything else?"
          and not last()["interrupted"], str(last()))
    check("/chat remembers it", memory.recent[-1].user == "hello")

    script[:] = [["Twelve o'clock."]]
    response = client.post("/converse", files={"file": ("a.wav", wav())},
                           data={"agentic": "true", "format": "pcm"})
    check("/converse returns audio", response.status_code == 200
          and len(response.content) == 4800, str(response.status_code))
    check("/converse records the exchange as voice",
          last()["user"] == "what time is it" and last()["via"] == "voice"
          and last()["naka"] == "Twelve o'clock.", str(last()))
    logged = json.loads(turnlog.PATH.read_text(encoding="utf-8").splitlines()[-1])
    check("/converse writes the turn log with its timings",
          logged["heard"] == "what time is it"
          and {"stt", "first_sound", "total"} <= set(logged["timings_ms"]),
          str(logged.get("timings_ms")))

    agent_calls.clear()
    script[:] = [["Sent from ", "the phone."]]
    reply = asyncio.run(main.telegram_turn("hi from telegram"))
    check("a Telegram message is answered with the whole reply",
          reply == "Sent from the phone.", reply)
    check("and recorded as telegram", last()["via"] == "telegram")
    check("it runs tainted, without PowerShell, from telegram",
          agent_calls and agent_calls[-1].get("tainted") is True
          and "shell" in agent_calls[-1].get("withhold", ())
          and agent_calls[-1].get("origin") == "telegram", str(agent_calls))

    script[:] = [["Half a reply. ", RuntimeError("llama-server went away")]]
    try:
        asyncio.run(main.telegram_turn("this one breaks"))
        raised = False
    except RuntimeError:
        raised = True
    check("a Telegram turn that fails still says so to the caller", raised)
    check("and is kept, as far as it got, marked interrupted",
          last()["user"] == "this one breaks" and last()["interrupted"]
          and last()["naka"] == "Half a reply.", str(last()))

    async def cut_off() -> None:
        script[:] = [["First part. ", "Second part. ", "Third part."]]
        response = await main.chat_endpoint(main.TextIn(text="cut me off",
                                                        agentic=True))
        body = response.body_iterator
        await anext(body)
        await body.aclose()  # the client hung up after one sentence
        await settle()

    asyncio.run(cut_off())
    check("a reply cut off by the client is kept as far as it got",
          last()["user"] == "cut me off" and last()["interrupted"]
          and last()["naka"] == "First part.", str(last()))


if __name__ == "__main__":
    run()
    print()
    if failures:
        print(f"{failures} turn check(s) FAILED")
        sys.exit(1)
    print("all turn checks pass")
