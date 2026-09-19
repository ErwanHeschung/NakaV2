"""Phase 0 bench: time-to-first-token and generation rate.

Naka's latency budget allows 300 ms to the LLM's first token, so TTFT is the
number that decides whether a candidate is viable — average tokens/s is a poor
proxy for perceived latency in a streaming voice pipeline.

Usage: python eval/llm_bench.py --name qwen3-14b [--no-think]
"""

import argparse
import json
import time
import urllib.request

PROMPTS = [
    "What's the weather usually like in Lisbon in spring?",
    "Remind me what a GGUF file is.",
    "Tell me something interesting about octopuses.",
]


def stream(url, prompt, max_tokens, no_think, timeout):
    body = {
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if no_think:
        body["chat_template_kwargs"] = {"enable_thinking": False}

    req = urllib.request.Request(
        f"{url.rstrip('/')}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )

    start = time.perf_counter()
    ttft = None
    reasoning = 0
    visible = 0

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode().strip()
            if not line.startswith("data: "):
                continue
            chunk = line[6:]
            if chunk == "[DONE]":
                break
            delta = json.loads(chunk)["choices"][0].get("delta", {})
            # Reasoning tokens cost latency but never reach the speakers.
            if delta.get("reasoning_content"):
                reasoning += 1
                if ttft is None:
                    ttft = time.perf_counter() - start
            if delta.get("content"):
                visible += 1
                if ttft is None:
                    ttft = time.perf_counter() - start

    return ttft, reasoning, visible, time.perf_counter() - start


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8080")
    ap.add_argument("--name", default="model")
    ap.add_argument("--max-tokens", type=int, default=256)
    ap.add_argument("--no-think", action="store_true")
    ap.add_argument("--timeout", type=float, default=300.0)
    args = ap.parse_args()

    print(f"\n=== {args.name}{' (thinking off)' if args.no_think else ''} ===")
    # The first call compiles kernels; measuring it would report boot cost, not
    # steady-state latency. Naka warms up at boot for the same reason.
    stream(args.url, "hi", 8, args.no_think, args.timeout)
    print(f"{'ttft':>8} {'reason':>8} {'visible':>8} {'total_s':>8} {'tok/s':>8}")

    rows = []
    for prompt in PROMPTS:
        ttft, reasoning, visible, total = stream(
            args.url, prompt, args.max_tokens, args.no_think, args.timeout
        )
        rate = (reasoning + visible) / total if total else 0
        rows.append((ttft, rate))
        ttft_s = f"{ttft * 1000:.0f}ms" if ttft else "n/a"
        print(f"{ttft_s:>8} {reasoning:>8} {visible:>8} {total:>8.2f} {rate:>8.1f}")

    ttfts = [t for t, _ in rows if t]
    print(f"\nmean ttft: {sum(ttfts) / len(ttfts) * 1000:.0f} ms   "
          f"mean rate: {sum(r for _, r in rows) / len(rows):.1f} tok/s")
    print(f"budget: 300 ms to first token — "
          f"{'PASS' if sum(ttfts) / len(ttfts) < 0.3 else 'OVER'}")


if __name__ == "__main__":
    main()
