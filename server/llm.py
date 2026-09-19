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

SENTENCE_END = re.compile(r"[.!?]")


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


def build_messages(user_text: str) -> list[dict]:
    return [
        {"role": "system", "content": settings.LLM["system_prompt"]},
        {"role": "user", "content": user_text},
    ]


async def stream_sentences(messages: list[dict]) -> AsyncIterator[str]:
    """Yield complete sentences as soon as their closing punctuation arrives."""
    url = f"{settings.LLM['url'].rstrip('/')}/v1/chat/completions"
    buffer = ""

    async with httpx.AsyncClient(timeout=300.0) as client:
        async with client.stream("POST", url, json=_body(messages, True)) as response:
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
    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.post(url, json=_body(messages, False))
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
