"""How much of each prompt does llama-server actually have to compute?

A conversation's prompt grows by one exchange a turn and is otherwise the
same, so almost all of it should come from llama.cpp's cache. This replays a
real conversation (the person's own persona, facts and the full tool list)
straight against llama-server, with a background generation between turns as
the memory reconciler runs one, and reads llama.cpp's own counters from the
end of each stream: tokens evaluated, tokens reused, milliseconds of prompt
processing, and time to the first token.

Each configuration starts its own llama-server on a spare port, so Naka must
not be holding the GPU.

Usage:
    uv run python eval/llm_cache_bench.py [config ...]
"""

import asyncio
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from server import agent, llamacpp  # noqa: E402
from server.memory import RECONCILE_PROMPT, memory  # noqa: E402

PORT = 8081
URL = f"http://127.0.0.1:{PORT}"

CONFIGS = {
    # What Naka runs today.
    "base": ([], None),
    # Full-size sliding-window cache: Gemma's local-attention layers keep
    # their whole history, so a prefix can be reused past the window.
    "swa-full": (["--swa-full"], None),
    # Context checkpoints, taken as often as every turn: llama.cpp restores
    # the sliding-window state from one instead of recomputing, and keeps
    # them in RAM rather than VRAM. The default spacing is 8192 tokens,
    # which with an 8192 context means almost never.
    "checkpoints": (["--checkpoint-min-step", "0"], None),
    "checkpoints-256": (["--checkpoint-min-step", "256"], None),
    # A second slot for background work, sharing one KV pool, with the
    # conversation pinned to slot 0 and the reconciler to slot 1.
    "two-slots": (["--parallel", "2", "--kv-unified"], (0, 1)),
    "two-slots+swa-full": (["--parallel", "2", "--kv-unified", "--swa-full"],
                           (0, 1)),
}

TURNS = [
    "Hey, how's it going?",
    "I'm thinking of playing something tonight, any idea?",
    "What time is it right now?",
    "Remind me that my sister's birthday is on the twelfth of October.",
    "Set a timer for ten minutes for the pasta.",
    "What did I just ask you to time?",
    "Tell me a quick fact about octopuses.",
]


def argv(extra: list[str]) -> list[str]:
    args = llamacpp.argv()
    args[args.index("--port") + 1] = str(PORT)
    return args + extra


def vram_used() -> int:
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used",
                          "--format=csv,noheader,nounits"],
                         capture_output=True, text=True).stdout
    return int(out.strip().splitlines()[0])


async def stream(client: httpx.AsyncClient, messages: list[dict],
                 tools: list[dict], slot: int | None) -> dict:
    body = {"messages": messages, "tools": tools, "tool_choice": "auto",
            "temperature": 0, "max_tokens": 256, "stream": True,
            "chat_template_kwargs": {"enable_thinking": False}}
    if slot is not None:
        body["id_slot"] = slot
    start = time.perf_counter()
    first = None
    text, timings, called = "", {}, False
    async with client.stream("POST", f"{URL}/v1/chat/completions",
                             json=body) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            chunk = json.loads(line[6:])
            if chunk.get("timings"):
                timings = chunk["timings"]
            for choice in chunk.get("choices", []):
                delta = choice.get("delta", {})
                if (delta.get("content") or delta.get("tool_calls")) and first is None:
                    first = (time.perf_counter() - start) * 1000
                text += delta.get("content") or ""
                called = called or bool(delta.get("tool_calls"))
    return {"ttft": first or 0.0, "text": text or "(called a tool)",
            "called": called,
            "prompt_n": timings.get("prompt_n"),
            "cache_n": timings.get("cache_n"),
            "prompt_ms": timings.get("prompt_ms")}


async def background(client: httpx.AsyncClient, user: str, reply: str,
                     slot: int | None) -> float:
    body = {"messages": [
        {"role": "system", "content": RECONCILE_PROMPT.format(
            known="1. They like sci-fi games.")},
        {"role": "user", "content": f"User: {user}\nNaka: {reply}"}],
        "temperature": 0, "max_tokens": 64, "stream": False,
        "chat_template_kwargs": {"enable_thinking": False}}
    if slot is not None:
        body["id_slot"] = slot
    start = time.perf_counter()
    (await client.post(f"{URL}/v1/chat/completions", json=body)).raise_for_status()
    return (time.perf_counter() - start) * 1000


async def run(name: str) -> dict:
    extra, slots = CONFIGS[name]
    process = subprocess.Popen(argv(extra), cwd=str(llamacpp.BIN.parent),
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL,
                               creationflags=0x08000000)
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            for _ in range(240):
                try:
                    if (await client.get(f"{URL}/health")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.5)
            vram = vram_used()
            tools = agent._offered(agent.Turn([]))
            history: list[dict] = []
            rows = []
            for index, user in enumerate(TURNS):
                messages = memory.messages(user, "Right now it is 20:14 on "
                                                 "Saturday 26 September 2026.")
                # memory.messages carries no history here; add ours.
                messages = messages[:1] + history + messages[1:]
                result = await stream(client, messages, tools,
                                      slots[0] if slots else None)
                history += [{"role": "user", "content": user},
                            {"role": "assistant", "content": result["text"]}]
                bg = await background(client, user, result["text"],
                                      slots[1] if slots else None)
                rows.append(result | {"bg_ms": bg})
                print(f"  {name:20} turn {index + 1}: ttft {result['ttft']:6.0f}ms  "
                      f"evaluated {result['prompt_n']:>5}  cached {result['cache_n']:>5}  "
                      f"prompt {result['prompt_ms']:6.0f}ms  bg {bg:5.0f}ms")
    finally:
        process.terminate()
        process.wait(timeout=30)
    later = rows[1:]
    return {"config": name, "vram_mb": vram,
            "ttft_first": rows[0]["ttft"],
            "ttft_median": statistics.median(r["ttft"] for r in later),
            "evaluated_median": statistics.median(r["prompt_n"] for r in later),
            "cached_median": statistics.median(r["cache_n"] for r in later),
            "prompt_ms_median": statistics.median(r["prompt_ms"] for r in later),
            "turns": rows}


async def main() -> None:
    names = sys.argv[1:] or list(CONFIGS)
    results = []
    for name in names:
        print(f"== {name}")
        results.append(await run(name))
    print(f"\n{'config':22}{'VRAM':>7}{'1st ttft':>10}{'ttft p50':>10}"
          f"{'evaluated':>11}{'cached':>8}{'prompt ms':>11}")
    for r in results:
        print(f"{r['config']:22}{r['vram_mb']:>7}{r['ttft_first']:>10.0f}"
              f"{r['ttft_median']:>10.0f}{r['evaluated_median']:>11.0f}"
              f"{r['cached_median']:>8.0f}{r['prompt_ms_median']:>11.0f}")
    out = ROOT / "eval" / "bench" / "llm_cache.json"
    out.write_text(json.dumps(results, indent=1), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
