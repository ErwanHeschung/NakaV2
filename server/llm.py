"""Language model client.

The model runs in the llama.cpp container, so this is an HTTP client rather
than a loader. Output is emitted sentence by sentence: the TTS engine has no
streaming API, so splitting on sentence boundaries is the only way to start
speaking before the whole reply exists.
"""

import json
import logging
import re
from collections.abc import AsyncIterator

import httpx

from . import settings

log = logging.getLogger("naka.llm")

# A run of terminators followed by whitespace. The run keeps "..." intact
# instead of splitting it into three empty sentences, and requiring the
# trailing space means a decimal ("18.5") or a mid-stream token that merely
# ends in a dot is not mistaken for the end of a sentence.
SENTENCE_END = re.compile(r"[.!?]+[\"')\]]*\s")


# One client for the process, not one per call.
#
# Every generation used to build and tear down its own client and pool, so no
# connection was ever reused. A single agentic turn opens one per step, plus
# one to phrase a confirmation, plus the background reconcile and summary —
# a handful of fresh TCP connections on the path this project measures in
# milliseconds. Created lazily so importing this module needs no loop.
_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=300.0,
            limits=httpx.Limits(max_keepalive_connections=8,
                                keepalive_expiry=600.0),
        )
    return _client


async def aclose() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _body(messages: list[dict], stream: bool) -> dict:
    return {
        "messages": messages,
        "temperature": settings.LLM["temperature"],
        "max_tokens": settings.LLM["max_tokens"],
        "stream": stream,
        "chat_template_kwargs": {
            "enable_thinking": settings.LLM["enable_thinking"]
        },
    }


async def stream_sentences(messages: list[dict]) -> AsyncIterator[str]:
    """Yield complete sentences as soon as their closing punctuation arrives."""
    url = f"{settings.LLM['url'].rstrip('/')}/v1/chat/completions"
    buffer = ""

    async with client().stream("POST", url, json=_body(messages, True)) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                break
            delta = json.loads(payload)["choices"][0].get("delta", {})
            piece = delta.get("content")
            if not piece:
                continue
            buffer += piece
            while (match := SENTENCE_END.search(buffer)) is not None:
                sentence, buffer = buffer[: match.end()], buffer[match.end():]
                sentence = sentence.strip()
                if sentence:
                    yield sentence

    tail = buffer.strip()
    if tail:
        yield tail


async def complete(messages: list[dict]) -> str:
    url = f"{settings.LLM['url'].rstrip('/')}/v1/chat/completions"
    response = await client().post(url, json=_body(messages, False))
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


async def complete_with_tools(messages: list[dict], tools: list[dict]) -> dict:
    """One non-streaming turn with tools offered. Returns the whole message.

    Tool calls cannot be streamed usefully — nothing can be spoken until it is
    known whether the model wants to talk or to act — so the agentic path
    trades streaming for that decision.
    """
    url = f"{settings.LLM['url'].rstrip('/')}/v1/chat/completions"
    body = _body(messages, stream=False)
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    response = await client().post(url, json=body)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]


def split_sentences(text: str) -> list[str]:
    """Split a finished reply the same way the streaming path splits a live one."""
    out, buffer = [], text
    while (match := SENTENCE_END.search(buffer)) is not None:
        sentence, buffer = buffer[: match.end()].strip(), buffer[match.end():]
        if sentence:
            out.append(sentence)
    if buffer.strip():
        out.append(buffer.strip())
    return out
