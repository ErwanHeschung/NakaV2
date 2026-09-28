"""Replay the reference phrases through the running server.

Run this after any change that could affect the conversation: a new model, a
prompt edit, a DSP tweak, a dependency bump. It will not tell you whether the
answers are good — that needs ears — but it catches the failures that matter
and are easy to miss: something stopped answering, something got slow, the
voice stopped coming out.

Usage: uv run python eval/regression.py [--agentic] [--baseline]
"""

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
PHRASES = HERE / "phrases.txt"
BASELINE = HERE / "regression_baseline.json"
URL = "http://127.0.0.1:8000"
BUDGET_MS = 1300
# Flag anything more than this much slower than the recorded baseline.
REGRESSION_FACTOR = 1.5


def phrases() -> list[str]:
    return [line.strip() for line in PHRASES.read_text().splitlines()
            if line.strip() and not line.startswith("#")]


def ask(text: str, agentic: bool, timeout: float) -> tuple[float, list[str]]:
    body = json.dumps({"text": text, "agentic": agentic}).encode()
    request = urllib.request.Request(
        f"{URL}/chat", data=body, headers={"Content-Type": "application/json"})
    start = time.perf_counter()
    first = None
    spoken = []
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for line in response:
            line = line.decode().strip()
            if not line:
                continue
            if first is None:
                first = (time.perf_counter() - start) * 1000
            spoken.append(json.loads(line)["sentence"])
    return first or 0.0, spoken


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agentic", action="store_true",
                    help="offer tools, exercising the agent loop")
    ap.add_argument("--baseline", action="store_true",
                    help="record this run as the baseline to compare against")
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args()

    try:
        urllib.request.urlopen(f"{URL}/health", timeout=5).read()
    except (urllib.error.URLError, TimeoutError):
        sys.exit(f"Naka is not answering on {URL}")

    # Start from a clean conversation so earlier turns cannot change answers.
    urllib.request.urlopen(urllib.request.Request(
        f"{URL}/memory/forget", method="POST"), timeout=10).read()

    old = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    results, failures, slower = {}, [], []

    print(f"{'phrase':<46}{'ms':>7}  reply")
    for text in phrases():
        try:
            ms, spoken = ask(text, args.agentic, args.timeout)
        except Exception as e:
            failures.append((text, repr(e)))
            print(f"{text[:44]:<46}{'ERR':>7}  {e}")
            continue

        results[text] = round(ms)
        reply = " ".join(s.strip() for s in spoken)
        if not reply.strip():
            failures.append((text, "empty reply"))

        mark = ""
        if text in old and ms > old[text] * REGRESSION_FACTOR:
            mark = f"  <- was {old[text]}ms"
            slower.append((text, old[text], round(ms)))
        print(f"{text[:44]:<46}{ms:>7.0f}  {reply[:50]}{mark}")

    values = list(results.values())
    print(f"\n{len(results)}/{len(phrases())} answered   "
          f"median {statistics.median(values):.0f}ms   "
          f"max {max(values):.0f}ms")

    over = [t for t, ms in results.items() if ms > BUDGET_MS]
    if over:
        print(f"over the {BUDGET_MS}ms budget: {len(over)}")
    for text, reason in failures:
        print(f"  FAILED  {text[:50]}: {reason}")
    for text, was, now in slower:
        print(f"  SLOWER  {text[:40]}: {was} -> {now}ms")

    if args.baseline:
        BASELINE.write_text(json.dumps(results, indent=2, ensure_ascii=False))
        print(f"\nbaseline written to {BASELINE.name}")

    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
