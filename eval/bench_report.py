"""Compare agent_bench runs, and set Naka's answers beside the reference ones.

    uv run python eval/bench_report.py baseline v1 [v2 ...]

Prints the score per kind for every run, the tasks that changed between the
first and the last, and every task the last run still fails, with what she
said next to what Claude found. Also writes the same to eval/bench/REPORT.md.
"""

import json
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parent / "bench"
MARK = {1.0: "pass", 0.5: "half", 0.0: "fail"}


def load(label: str) -> dict:
    data = json.loads((BENCH / f"{label}.json").read_text(encoding="utf-8"))
    return rescore({t["id"]: t for t in data["tasks"]})


def rescore(tasks: dict) -> dict:
    """Re-judge answers against the current checks.

    Web tasks, and shell tasks that only ask a question, are judged on what
    she said and which tools she called, both saved: a reference corrected
    after a run then applies to every run alike. Tasks that change files are
    judged on the files a run left behind, which are gone, so they keep the
    score they had.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import bench_tasks

    import tempfile
    root = Path(tempfile.gettempdir()) / "naka-bench"
    bench_tasks.build(root)
    checks = {t["id"]: t["check"] for t in bench_tasks.WEB}
    checks |= {t["id"]: t["check"] for t in bench_tasks.shell_tasks(root)
               if t["offline"]}
    for task in tasks.values():
        check = checks.get(task["id"])
        if check is not None and not task.get("error") and task["score"] != 0.5:
            task["score"] = 1.0 if check(task) else 0.0
    return tasks


def score(tasks: dict, kind: str) -> tuple[float, int]:
    part = [t for t in tasks.values() if t["kind"] == kind]
    return sum(t["score"] for t in part), len(part)


def pct(got: float, total: int) -> str:
    return f"{100 * got / total:.0f}%" if total else "n/a"


def main() -> None:
    labels = sys.argv[1:]
    runs = {label: load(label) for label in labels}
    out: list[str] = ["# Agent bench", ""]

    out += ["| Run | Web | Shell | Mean time | Tool errors |", "|:-|:-|:-|:-|:-|"]
    for label, tasks in runs.items():
        web, nw = score(tasks, "web")
        shell, ns = score(tasks, "shell")
        seconds = sum(t["seconds"] for t in tasks.values()) / max(len(tasks), 1)
        errors = sum(t["tool_errors"] for t in tasks.values())
        out.append(f"| {label} | {web:g}/{nw} ({pct(web, nw)}) | "
                   f"{shell:g}/{ns} ({pct(shell, ns)}) | {seconds:.1f}s | {errors} |")
    out.append("")

    first, last = runs[labels[0]], runs[labels[-1]]
    if len(labels) > 1:
        changed = [(i, first[i]["score"], last[i]["score"]) for i in last
                   if i in first and first[i]["score"] != last[i]["score"]]
        fixed = [c for c in changed if c[2] > c[1]]
        broke = [c for c in changed if c[2] < c[1]]
        out += [f"## {labels[0]} to {labels[-1]}", "",
                f"Better on {len(fixed)}, worse on {len(broke)}.", ""]
        if broke:
            out.append("Worse: " + ", ".join(f"`{i}`" for i, _, _ in broke))
            out.append("")

    failing = [t for t in last.values() if t["score"] < 1]
    out += [f"## Still not right in {labels[-1]} ({len(failing)})", "",
            "| Task | Asked | Naka | Reference | Tools |", "|:-|:-|:-|:-|:-|"]
    for t in sorted(failing, key=lambda t: t["id"]):
        tools = ", ".join(c["name"] for c in t["calls"]) or "none"
        said = " ".join(t["said"].split())[:220].replace("|", "/")
        if t.get("error"):
            said = f"**{t['error'][:120]}**"
        out.append(f"| `{t['id']}` {MARK[t['score']]} | {t['ask'][0]} | {said} | "
                   f"{t['reference']} | {tools} |")

    text = "\n".join(out) + "\n"
    (BENCH / "REPORT.md").write_text(text, encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
