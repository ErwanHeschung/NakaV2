"""Chart agent_bench runs side by side, in the style of eval/make_charts.py.

    uv run --group dev python eval/bench_chart.py baseline v1 v2

Writes eval/charts/agent-bench-{light,dark}.png: how much of each kind of
task every run got right, and how often it misbehaved on the way.
"""

import json
import re
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from make_charts import THEMES, save, style

BENCH = Path(__file__).parent / "bench"

# Claude's own score, set beside Naka's. On the web it is a real one: the
# references are the answers Claude found with its own search on 2026-09-24,
# and one of them (the year's biggest film) was wrong until Naka's answer
# prompted a second search. On PowerShell there is no run to score: the
# references are computed from the fixture, so they are drawn as a ceiling,
# hatched, rather than as a result.
CLAUDE_WEB = (109, 110)
PROMISE = re.compile(r"\b(i'll|let me|i'm going to|one sec|one moment|right now)\b",
                     re.IGNORECASE)


def load(label: str) -> list[dict]:
    # Through the report's loader, so web tasks are judged by current checks.
    from bench_report import load as judged
    return list(judged(label).values())


def accuracy(tasks: list[dict], kind: str | None) -> float:
    part = [t for t in tasks if kind is None or t["kind"] == kind]
    return 100 * sum(t["score"] for t in part) / len(part)


def misbehaviour(tasks: list[dict]) -> list[int]:
    return [
        sum(1 for t in tasks if t["error"]),
        sum(1 for t in tasks if t["kind"] == "shell" and not t["calls"]
            and PROMISE.search(t["said"])),
        sum(1 for t in tasks if t["score"] == 0.5),
        sum(1 for t in tasks if len(t["calls"]) >= 8),
    ]


def chart(labels: list[str], theme: str) -> None:
    t = THEMES[theme]
    runs = {label: load(label) for label in labels}
    width = 0.8 / (len(labels) + 1)
    fig, (left, right) = plt.subplots(
        1, 2, figsize=(11.5, 4.6), gridspec_kw={"width_ratios": [3, 2]})

    # --- accuracy -------------------------------------------------------
    groups = [("Web search", "web"), ("PowerShell", "shell"), ("All", None)]
    slots = len(labels) + 1
    for i, (label, tasks) in enumerate(runs.items()):
        values = [accuracy(tasks, kind) for _, kind in groups]
        xs = [g + (i - (slots - 1) / 2) * width for g in range(len(groups))]
        bars = left.bar(xs, values, width - 0.03, color=t["series"][i],
                        label=label, zorder=2)
        for bar, value in zip(bars, values):
            left.text(bar.get_x() + bar.get_width() / 2, value + 1.2,
                      f"{value:.0f}%", ha="center", va="bottom", fontsize=8.5,
                      color=t["primary"])
    # Claude, last in each group.
    first = next(iter(runs.values()))
    shell_n = sum(1 for x in first if x["kind"] == "shell")
    reference = [100 * CLAUDE_WEB[0] / CLAUDE_WEB[1], 100.0,
                 100 * (CLAUDE_WEB[0] + shell_n) / (CLAUDE_WEB[1] + shell_n)]
    xs = [g + (slots - 1 - (slots - 1) / 2) * width for g in range(len(groups))]
    for g, (x, value) in enumerate(zip(xs, reference)):
        measured = g == 0
        left.bar(x, value, width - 0.03, zorder=2,
                 color=t["neutral"] if measured else "none",
                 edgecolor=t["secondary"], linewidth=0 if measured else 1,
                 hatch=None if measured else "////",
                 label="Claude (reference)" if g == 0 else None)
        left.text(x, value + 1.2, f"{value:.0f}%" + ("" if measured else "*"),
                  ha="center", va="bottom", fontsize=8.5, color=t["secondary"])
    left.set_xticks(range(len(groups)),
                    [f"{name}\n{sum(1 for x in next(iter(runs.values())) if kind is None or x['kind'] == kind)} tasks"
                     for name, kind in groups])
    left.set_ylim(0, 108)
    left.set_yticks([0, 25, 50, 75, 100], ["0%", "25%", "50%", "75%", "100%"])
    left.yaxis.grid(True, color=t["grid"], linewidth=0.8, zorder=0)
    left.set_title("Tasks done right", loc="left", fontsize=11,
                   color=t["primary"], pad=12)

    # --- misbehaviour ---------------------------------------------------
    kinds = ["Crashed", "Promised,\ndid nothing", "Needed a\nsecond yes",
             "Looped\n(8+ calls)"]
    narrow = 0.8 / len(labels)
    for i, (label, tasks) in enumerate(runs.items()):
        values = misbehaviour(tasks)
        xs = [k + (i - (len(labels) - 1) / 2) * narrow for k in range(len(kinds))]
        bars = right.bar(xs, values, narrow - 0.03, color=t["series"][i], zorder=2)
        for bar, value in zip(bars, values):
            right.text(bar.get_x() + bar.get_width() / 2, value + 0.08,
                       str(value), ha="center", va="bottom", fontsize=8.5,
                       color=t["primary"])
    right.set_xticks(range(len(kinds)), kinds)
    top = max(max(misbehaviour(tasks)) for tasks in runs.values())
    right.set_ylim(0, max(top + 1, 3))
    right.yaxis.grid(True, color=t["grid"], linewidth=0.8, zorder=0)
    right.set_yticks(range(0, int(max(top + 1, 3)) + 1))
    right.set_title("Went wrong along the way", loc="left", fontsize=11,
                    color=t["primary"], pad=12)

    legend = fig.legend(loc="upper right", ncol=len(labels) + 1, frameon=False,
                        fontsize=9, bbox_to_anchor=(1.0, 1.04))
    for text in legend.get_texts():
        text.set_color(t["secondary"])
    style(fig, [left, right], t)
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    fig.text(0.01, 0.01,
             "Claude on the web: its own answers from its own searches, one of "
             "110 wrong.  * On PowerShell, a ceiling: the right answers "
             "computed from the test files, not a run.",
             fontsize=8, color=t["secondary"], ha="left", va="bottom")
    save(fig, "agent-bench", theme)


if __name__ == "__main__":
    runs = sys.argv[1:] or ["baseline", "v1"]
    for theme in THEMES:
        chart(runs, theme)
    print(f"wrote agent-bench-light.png and agent-bench-dark.png for {', '.join(runs)}")
