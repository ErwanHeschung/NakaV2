"""Run Naka's agent on real web and PowerShell tasks, and score what she did.

The guardrail tests prove what she cannot do; this measures what she can. Each
scenario in eval/bench_tasks.py is something a person might ask, played
through the real model with the powers on, and checked against the files it
should have produced or the facts it should have found. Confirmations are
answered "yes" automatically, and writes land in a scratch folder rebuilt
before every task.

A task not done after the first request, where she ended on a question, gets
one "Yes, go ahead." — what a person would say — and scores half: needing it
means she asked a permission the harness asks anyway, or described instead
of doing.

Connection tasks (eval/connection_tasks.py) run against fake services, so
they need no accounts and change nothing real.

Usage:
    uv run python eval/agent_bench.py LABEL [web|shell|conn|apps] [task-id ...]

Writes eval/bench/LABEL.json and prints a line per task and a summary.
eval/bench_report.py compares runs.
"""

import asyncio
import json
import os
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from server import agent, ops, settings  # noqa: E402
from server.memory import memory  # noqa: E402

import bench_tasks  # noqa: E402
import app_tasks  # noqa: E402
import connection_tasks  # noqa: E402

SANDBOX = Path(tempfile.gettempdir()) / "naka-bench"
# Notes for the connection tasks, never the person's own.
NOTES = Path(tempfile.gettempdir()) / "naka-bench-notes"
KINDS = ("web", "shell", "conn", "apps")
OUT = ROOT / "eval" / "bench"
# Shared between checkouts, so a baseline run from a worktree and a run of the
# current code see the same results for the same query.
SEARCH_CACHE = Path(os.environ.get("NAKA_BENCH_CACHE")
                    or OUT / "search_cache.json")


def cache_searches() -> None:
    """Make search results the same for every run, and stop hammering.

    The free engines are scraped, and under a bench's load they refuse
    roughly one query in eight, at random: two runs would differ by which
    searches happened to fail rather than by what changed. Each query's
    results are kept the first time they come back non-empty, retried a few
    times until they do.
    """
    from server.tools import web

    cache = (json.loads(SEARCH_CACHE.read_text(encoding="utf-8"))
             if SEARCH_CACHE.exists() else {})
    live = web._duckduckgo

    def cached(query: str, count: int) -> list[dict]:
        key = f"{query.strip().lower()}|{count}"
        if key not in cache:
            for attempt in range(4):
                found = live(query, count)
                if found:
                    cache[key] = found
                    SEARCH_CACHE.write_text(json.dumps(cache, ensure_ascii=False),
                                            encoding="utf-8")
                    break
                time.sleep(5 * (attempt + 1))
        return cache.get(key, [])

    web._duckduckgo = cached


async def play(text: str, record: dict, origin: str = "pc") -> list[str]:
    """One spoken request, confirmations answered yes."""
    note = f"Right now it is {datetime.now().strftime('%H:%M on %A %d %B %Y')}."
    actions: list[dict] = []
    if origin == "telegram":
        # As server/main.py plays a message from the phone.
        from server.main import TELEGRAM_NOTE
        note += TELEGRAM_NOTE
        run = agent.run(memory.messages(text, note), actions, tainted=True,
                        withhold=frozenset({"shell"}), origin="telegram")
    else:
        run = agent.run(memory.messages(text, note), actions)
    spoken = [s async for s in run]
    while agent.pending is not None and record["confirmations"] < 8:
        record["confirmations"] += 1
        spoken += [s async for s in agent.resolve_pending("yes", actions)]
    memory.add_turn(text, " ".join(spoken), actions)
    for a in actions:
        try:
            arguments = json.loads(a["arguments"] or "{}")
        except json.JSONDecodeError:
            arguments = {"raw": a["arguments"]}
        record["calls"].append({"name": a["name"], "arguments": arguments,
                                "result": str(a["result"])[:400]})
    return spoken


async def run_task(task: dict) -> dict:
    bench_tasks.build(SANDBOX)
    if task["kind"] == "apps":
        app_tasks.setup()
    if task["kind"] == "conn":
        connection_tasks.setup(NOTES)
        if task.get("before"):
            task["before"]()
    memory.recent.clear()
    memory.pending.clear()
    memory.summary = ""
    agent.pending = None
    record = {"id": task["id"], "kind": task["kind"], "ask": task["ask"],
              "calls": [], "confirmations": 0, "turns": 0, "said": "",
              "reference": task["reference"], "error": None}
    started = time.perf_counter()
    spoken: list[str] = []
    try:
        origin = task.get("origin", "pc")
        for text in task["ask"]:
            record["turns"] += 1
            spoken = await play(text, record, origin)
            record["said"] += " ".join(spoken) + " "
        score = 1.0 if task["check"](record) else 0.0
        if not score and spoken and spoken[-1].rstrip().endswith("?"):
            record["turns"] += 1
            record["said"] += " ".join(await play("Yes, go ahead.", record,
                                                  origin))
            score = 0.5 if task["check"](record) else 0.0
    except Exception as e:  # a crash is a result too: it is what she did
        record["error"] = f"{type(e).__name__}: {e}"
        traceback.print_exc()
        score = 0.0
    record["score"] = score
    record["seconds"] = round(time.perf_counter() - started, 1)
    record["tool_errors"] = sum(1 for c in record["calls"]
                                if c["result"].startswith("Error"))
    return record


def select(args: list[str]) -> list[dict]:
    bench_tasks.build(SANDBOX)
    tasks = (bench_tasks.WEB + bench_tasks.shell_tasks(SANDBOX)
             + connection_tasks.tasks() + app_tasks.tasks())
    kinds = {a for a in args if a in KINDS}
    ids = {a for a in args if a not in KINDS}
    if kinds:
        tasks = [t for t in tasks if t["kind"] in kinds]
    if ids:
        tasks = [t for t in tasks if t["id"] in ids]
    return tasks


async def main() -> None:
    label, rest = sys.argv[1], sys.argv[2:]
    tasks = select(rest)
    settings.POWERS.update(web=True, shell=True, workspace=str(SANDBOX),
                           brave_api_key="")
    from server.tools import builtin, registry
    registry.NOTES_DIR = builtin.NOTES_DIR = NOTES
    OUT.mkdir(exist_ok=True)
    SEARCH_CACHE.parent.mkdir(parents=True, exist_ok=True)
    cache_searches()

    started_llm = not ops._llm_alive()
    if started_llm:
        assert await ops._start_llm(), "llama-server did not start"
    assert await ops._await_llm(), "llama-server is not answering"

    OUT.mkdir(exist_ok=True)
    results = []
    try:
        for n, task in enumerate(tasks, 1):
            r = await run_task(task)
            results.append(r)
            mark = {1.0: "PASS", 0.5: "HALF", 0.0: "FAIL"}[r["score"]]
            print(f"[{n}/{len(tasks)}] {mark} {r['id']:22} {r['seconds']:5.1f}s "
                  f"calls={len(r['calls'])} err={r['tool_errors']} "
                  f"ok?={r['confirmations']} | {r['said'][:100]!r}")
            # Written as it goes, so a crash or an interruption keeps what ran.
            (OUT / f"{label}.json").write_text(json.dumps(
                {"label": label, "tasks": results}, indent=1,
                ensure_ascii=False), encoding="utf-8")
    finally:
        if started_llm:
            await ops._stop_llm()

    for kind in KINDS:
        part = [r for r in results if r["kind"] == kind]
        if part:
            print(f"{label} {kind}: {sum(r['score'] for r in part):g} / "
                  f"{len(part)} ({100 * sum(r['score'] for r in part) / len(part):.0f}%)")


if __name__ == "__main__":
    asyncio.run(main())
