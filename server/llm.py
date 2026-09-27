"""Language model client.

The model runs in the llama.cpp container, so this is an HTTP client rather
than a loader. Output is emitted sentence by sentence: the TTS engine has no
streaming API, so splitting on sentence boundaries is the only way to start
speaking before the whole reply exists.
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator

import httpx

from . import settings, speech

log = logging.getLogger("naka.llm")

# Replies are cut into segments by speech.Segmenter: at the end of a
# sentence as before, and also at line breaks and around code blocks, with
# the whitespace kept so the chat can show the Markdown as it was written.


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


FIRST_OUTPUT_SECONDS = 8.0

# llama.cpp's own account of the last streamed generation: how many prompt
# tokens it had to compute, how many came from its cache, and how long the
# prompt took. Logged, and read by eval/pipeline_bench.py.
last_timings: dict = {}


def _note_timings(chunk: dict) -> None:
    timings = chunk.get("timings")
    if not timings:
        return
    last_timings.clear()
    last_timings.update(timings)
    log.info("llm prompt: %s new tokens, %s cached, %.0fms",
             timings.get("prompt_n"), timings.get("cache_n"),
             timings.get("prompt_ms", 0))


# The token that opens Gemma's thinking channel, by model file. With thinking
# off the template already holds an empty thought block, and on some prompts
# the model kept opening another one, <|channel>thought<channel|> over and
# over to the length limit: llama.cpp strips those, so 3,000 tokens and 40
# seconds came back as silence. Banned only on a retry of that case, never
# by default: after a tool result the model opens the channel as a matter of
# course, and banning it everywhere broke two answers in three (bench v8).
# Looked up rather than hard-coded, since its id belongs to the model.
_channel_bias: dict[str, dict] = {}


async def _bias() -> dict:
    if settings.LLM.get("enable_thinking"):
        return {}
    model = settings.LLM.get("model_file", "")
    if model not in _channel_bias:
        try:
            response = await client().post(
                f"{settings.LLM['url'].rstrip('/')}/tokenize",
                json={"content": "<|channel>", "parse_special": True})
            tokens = response.json().get("tokens", [])
        except (httpx.HTTPError, ValueError):
            return {}
        # One token means the model has it as a special token; any other
        # model spells it out in ordinary pieces and must not lose them.
        _channel_bias[model] = {str(tokens[0]): -100} if len(tokens) == 1 else {}
    return _channel_bias[model]


async def aclose() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _body(messages: list[dict], stream: bool,
          max_tokens: int | None = None) -> dict:
    return {
        "messages": messages,
        "temperature": settings.LLM["temperature"],
        "max_tokens": max_tokens or settings.LLM["max_tokens"],
        "stream": stream,
        "chat_template_kwargs": {
            "enable_thinking": settings.LLM["enable_thinking"]
        },
    }


async def stream_sentences(messages: list[dict]) -> AsyncIterator[str]:
    """Yield complete sentences as soon as their closing punctuation arrives.

    The tool-less case of stream_with_tools, which does the same parsing.
    """
    async for kind, payload in stream_with_tools(messages, []):
        if kind == "sentence":
            yield payload


async def complete(messages: list[dict]) -> str:
    url = f"{settings.LLM['url'].rstrip('/')}/v1/chat/completions"
    response = await client().post(url, json=_body(messages, False))
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


async def stream_with_tools(messages: list[dict], tools: list[dict],
                            max_tokens: int | None = None,
                            ban_channel: bool = False):
    """One turn with tools offered, streamed.

    Yields ("sentence", str) as sentences complete and, at the end,
    ("tool_calls", list) if the model asked for any, then ("finish", reason)
    with the server's finish_reason — "length" means the reply, or a call's
    arguments, were cut off by max_tokens.

    The agentic path used to be non-streaming on the grounds that nothing can
    be spoken until it is known whether the model wants to talk or to act.
    That is true, but the answer arrives in the very first delta — content or
    tool_calls, never both — so it costs one delta to find out rather than the
    whole reply. Measured on this setup, offering tools cost 610ms of extra
    time-to-first-sentence even on turns that used no tool, purely from
    waiting for a complete response.
    """
    url = f"{settings.LLM['url'].rstrip('/')}/v1/chat/completions"
    body = _body(messages, stream=True, max_tokens=max_tokens)
    if ban_channel:
        body["logit_bias"] = await _bias()
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"

    segmenter = speech.Segmenter()
    finish = None
    # Merged by index: arguments arrive a fragment at a time across deltas.
    partial: dict[int, dict] = {}

    async with client().stream("POST", url, json=body) as response:
        response.raise_for_status()
        lines = response.aiter_lines()
        # Nothing is sent while the model loops on its thinking channel, and
        # it went on for 63 seconds. A real reply's first word or call comes
        # within a second of the prompt, so eight with nothing is that loop:
        # the request is dropped, which stops it, and reported as an empty
        # reply at the limit, which the agent knows how to retry.
        deadline = time.perf_counter() + FIRST_OUTPUT_SECONDS
        produced = False
        while True:
            try:
                if produced:
                    line = await anext(lines)
                else:
                    line = await asyncio.wait_for(
                        anext(lines), max(0.1, deadline - time.perf_counter()))
            except StopAsyncIteration:
                break
            except TimeoutError:
                log.warning("no output within %ss; dropping the request",
                            FIRST_OUTPUT_SECONDS)
                yield "finish", "length"
                return
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                break
            chunk = json.loads(payload)
            _note_timings(chunk)
            if not chunk.get("choices"):
                continue
            choice = chunk["choices"][0]
            finish = choice.get("finish_reason") or finish
            delta = choice.get("delta", {})

            for call in delta.get("tool_calls") or []:
                if call.get("index", 0) not in partial and \
                        (call.get("function") or {}).get("name"):
                    # The name arrives first; the arguments may take many
                    # seconds more to write.
                    yield "calling", call["function"]["name"]
                slot = partial.setdefault(
                    call.get("index", 0),
                    {"id": "", "function": {"name": "", "arguments": ""}},
                )
                if call.get("id"):
                    slot["id"] = call["id"]
                function = call.get("function") or {}
                if function.get("name"):
                    slot["function"]["name"] = function["name"]
                if function.get("arguments"):
                    slot["function"]["arguments"] += function["arguments"]

            piece = delta.get("content")
            if piece or delta.get("tool_calls"):
                produced = True
            if not piece:
                continue
            for segment in segmenter.feed(piece):
                yield "sentence", segment

    for segment in segmenter.flush():
        yield "sentence", segment
    if partial:
        yield "tool_calls", [partial[i] for i in sorted(partial)]
    yield "finish", finish


async def prefill(messages: list[dict], tools: list[dict]) -> None:
    """One token's generation, for the side effect of a cached prompt."""
    url = f"{settings.LLM['url'].rstrip('/')}/v1/chat/completions"
    body = _body(messages, stream=False, max_tokens=1)
    if tools:
        body["tools"] = tools
    start = time.perf_counter()
    (await client().post(url, json=body)).raise_for_status()
    log.info("prompt prefilled in %.0fms", (time.perf_counter() - start) * 1000)


def split_sentences(text: str) -> list[str]:
    """Split a finished reply the same way the streaming path splits a live one."""
    segmenter = speech.Segmenter()
    return segmenter.feed(text) + segmenter.flush()
