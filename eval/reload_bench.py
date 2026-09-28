"""The first turn after the language model has been reloaded.

Naka stops llama-server after a minute of quiet to free the GPU, so the next
turn meets an empty prompt cache. This restarts it a few times and times the
first sentence of the first turn, with and without agent.prefill() having
run in between, as it now does while the speech models load.

Needs the GPU to itself: Naka must not be running.

Usage: uv run python eval/reload_bench.py [--runs 5] [--llm-flag FLAG ...]
"""

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from server import agent, llamacpp, llm, ops  # noqa: E402
from server.memory import memory  # noqa: E402

ASK = "What time is it right now?"
NOTE = "Right now it is 20:14 on Saturday 26 September 2026."


async def first_sentence() -> tuple[float, int]:
    start = time.perf_counter()
    stream = agent.run(memory.messages(ASK, NOTE), [])
    await anext(stream)
    elapsed = (time.perf_counter() - start) * 1000
    async for _ in stream:
        pass
    return elapsed, llm.last_timings.get("prompt_n", 0)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--llm-flag", action="append", default=[])
    args = ap.parse_args()
    if args.llm_flag:
        base = llamacpp.argv
        llamacpp.argv = lambda: base() + args.llm_flag

    results: dict[str, list[tuple[float, int]]] = {"cold": [], "prefilled": []}
    for _ in range(args.runs):
        for mode in results:
            await ops._stop_llm()
            assert await ops._start_llm()
            assert await ops._await_llm()
            if mode == "prefilled":
                await agent.prefill()
            results[mode].append(await first_sentence())
            ms, new = results[mode][-1]
            print(f"  {mode:9} first sentence {ms:5.0f}ms, {new} prompt tokens computed")
    await ops._stop_llm()
    for mode, rows in results.items():
        print(f"{mode:9} median {statistics.median(r[0] for r in rows):5.0f}ms"
              f"  ({statistics.median(r[1] for r in rows):.0f} tokens computed)")


if __name__ == "__main__":
    asyncio.run(main())
