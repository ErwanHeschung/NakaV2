"""Render the Phase 0 benchmark charts used by eval/RESULTS.md.

Figures are emitted in light and dark variants so the results document reads
correctly under either theme. Numbers here are transcribed from the benchmark
runs recorded in RESULTS.md; re-run the benches and update both together.

Usage: uv run --group dev python eval/make_charts.py
"""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

OUT = Path(__file__).parent / "charts"
BUDGET_MS = 1300

THEMES = {
    "light": {
        "surface": "#fcfcfb",
        "primary": "#0b0b0b",
        "secondary": "#52514e",
        "grid": "#e4e3df",
        "neutral": "#d4d3ce",
        "series": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"],
    },
    "dark": {
        "surface": "#1a1a19",
        "primary": "#ffffff",
        "secondary": "#c3c2b7",
        "grid": "#333330",
        "neutral": "#4a4a46",
        "series": ["#3987e5", "#d95926", "#199e70", "#c98500"],
    },
}

# Stage breakdown in ms: (STT, LLM to end of first sentence, TTS).
# Both rows use the same prompt and produce the same first sentence, so STT and
# LLM are the same measured medians (n=7); only the TTS engine differs. Earlier
# single-sample runs made these stages look engine-dependent, which they aren't.
STT_MS = 216
LLM_MS = 192
E2E = [
    ("Chatterbox", STT_MS, LLM_MS, 1254),
    ("Kokoro", STT_MS, LLM_MS, 66),
]

# sentence length -> (chatterbox ms, kokoro ms)
TTS_CASES = [
    ("short\n17 chars", 570, 29),
    ("medium\n57 chars", 1063, 38),
    ("long\n121 chars", 1696, 58),
]

# VRAM, MiB, of a 16303 MiB card
VRAM = [
    ("with Chatterbox", [("Desktop", 2175), ("Gemma 4 12B", 7479),
                         ("faster-whisper", 1289), ("TTS", 3288)]),
    ("with Kokoro", [("Desktop", 2175), ("Gemma 4 12B", 7479),
                     ("faster-whisper", 1289), ("TTS", 684)]),
]
VRAM_TOTAL = 16303

# Kokoro streaming run: (wall clock when sentence audio was ready, cumulative audio seconds)
STREAM = [(0.63, 3.52), (0.77, 7.35), (0.84, 10.95)]
STREAM_FIRST_SOUND = 0.63


def style(fig, axes, t):
    fig.patch.set_facecolor(t["surface"])
    for ax in axes if isinstance(axes, (list, tuple)) else [axes]:
        ax.set_facecolor(t["surface"])
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.tick_params(colors=t["secondary"], labelsize=9, length=0)
        for lbl in ax.get_xticklabels() + ax.get_yticklabels():
            lbl.set_color(t["secondary"])


def save(fig, name, theme):
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / f"{name}-{theme}.png", dpi=160,
                facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)


def chart_e2e(theme):
    t = THEMES[theme]
    labels = [r[0] for r in E2E]
    stages = ["STT", "LLM (to end of first sentence)", "TTS"]
    fig, ax = plt.subplots(figsize=(9, 2.9))

    y = range(len(E2E))
    left = [0.0] * len(E2E)
    for idx, stage in enumerate(stages):
        widths = [row[idx + 1] for row in E2E]
        ax.barh(y, widths, left=left, height=0.52, label=stage,
                color=t["series"][idx], edgecolor=t["surface"], linewidth=2)
        for i, (w, l) in enumerate(zip(widths, left)):
            if w > 150:  # only label segments wide enough to hold text
                ax.text(l + w / 2, i, f"{w:.0f}", ha="center", va="center",
                        fontsize=9, color="#ffffff", fontweight="medium")
        left = [l + w for l, w in zip(left, widths)]

    # Totals in an aligned column so none of them collides with the budget rule.
    for i, total in enumerate(left):
        ax.text(1720, i, f"{total:.0f} ms", va="center", fontsize=10,
                color=t["primary"], fontweight="bold")

    ax.axvline(BUDGET_MS, color=t["secondary"], linestyle=(0, (4, 3)), linewidth=1.5)
    # Sits in the empty band between the last two bars, clear of both.
    ax.text(BUDGET_MS + 45, 0.62, "1300 ms budget", fontsize=9,
            color=t["secondary"], va="center", ha="left")

    ax.set_yticks(list(y))
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlim(0, 1950)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}"))
    ax.set_xlabel("milliseconds to first sound", color=t["secondary"], fontsize=9)
    ax.xaxis.grid(True, color=t["grid"], linewidth=1)
    ax.set_axisbelow(True)
    ax.set_title("Time to first sound, by configuration", color=t["primary"],
                 fontsize=12, fontweight="bold", loc="left", pad=14)
    leg = ax.legend(frameon=False, fontsize=9, ncol=3, loc="upper center",
                    bbox_to_anchor=(0.5, -0.30))
    for txt in leg.get_texts():
        txt.set_color(t["secondary"])
    style(fig, ax, t)
    save(fig, "e2e-latency", theme)


def chart_tts(theme):
    t = THEMES[theme]
    fig, ax = plt.subplots(figsize=(8, 3.4))
    x = range(len(TTS_CASES))
    width = 0.36

    cb = [r[1] for r in TTS_CASES]
    ko = [r[2] for r in TTS_CASES]
    b1 = ax.bar([i - width / 2 for i in x], cb, width, label="Chatterbox",
                color=t["series"][0], edgecolor=t["surface"], linewidth=2)
    b2 = ax.bar([i + width / 2 for i in x], ko, width, label="Kokoro",
                color=t["series"][1], edgecolor=t["surface"], linewidth=2)

    for bars in (b1, b2):
        for rect in bars:
            h = rect.get_height()
            ax.text(rect.get_x() + rect.get_width() / 2, h + 35, f"{h:.0f} ms",
                    ha="center", fontsize=9, color=t["primary"])

    ax.set_xticks(list(x))
    ax.set_xticklabels([r[0] for r in TTS_CASES], fontsize=9)
    ax.set_ylim(0, 1950)
    ax.set_ylabel("synthesis time (ms)", color=t["secondary"], fontsize=9)
    ax.yaxis.grid(True, color=t["grid"], linewidth=1)
    ax.set_axisbelow(True)
    ax.set_title("TTS synthesis time by sentence length", color=t["primary"],
                 fontsize=12, fontweight="bold", loc="left", pad=14)
    leg = ax.legend(frameon=False, fontsize=9)
    for txt in leg.get_texts():
        txt.set_color(t["secondary"])
    style(fig, ax, t)
    save(fig, "tts-latency", theme)


def chart_vram(theme):
    t = THEMES[theme]
    fig, ax = plt.subplots(figsize=(9, 2.9))
    y = range(len(VRAM))

    for row_idx, (label, parts) in enumerate(VRAM):
        left = 0.0
        for part_idx, (name, value) in enumerate(parts):
            ax.barh(row_idx, value, left=left, height=0.5,
                    color=t["series"][part_idx], edgecolor=t["surface"], linewidth=2,
                    label=name if row_idx == 0 else None)
            if value > 900:
                ax.text(left + value / 2, row_idx, f"{value / 1024:.1f}",
                        ha="center", va="center", fontsize=9, color="#ffffff",
                        fontweight="medium")
            left += value
        free = VRAM_TOTAL - left
        ax.barh(row_idx, free, left=left, height=0.5, color=t["neutral"],
                edgecolor=t["surface"], linewidth=2,
                label="free" if row_idx == 0 else None)
        ax.text(left + free / 2, row_idx, f"{free / 1024:.1f} GB free",
                ha="center", va="center", fontsize=9, color=t["primary"])

    ax.set_yticks(list(y))
    ax.set_yticklabels([r[0] for r in VRAM], fontsize=9)
    ax.invert_yaxis()
    ax.set_xlim(0, VRAM_TOTAL)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v / 1024:.0f} GB"))
    ax.xaxis.grid(True, color=t["grid"], linewidth=1)
    ax.set_axisbelow(True)
    ax.set_title("VRAM footprint of the resident stack (16.3 GB card)",
                 color=t["primary"], fontsize=12, fontweight="bold", loc="left", pad=14)
    leg = ax.legend(frameon=False, fontsize=9, ncol=5, loc="lower center",
                    bbox_to_anchor=(0.5, -0.42))
    for txt in leg.get_texts():
        txt.set_color(t["secondary"])
    style(fig, ax, t)
    save(fig, "vram", theme)


def chart_stream(theme):
    t = THEMES[theme]
    fig, ax = plt.subplots(figsize=(8, 3.4))

    # Playback runs 1 s per second from first sound until the reply is spoken
    # out; stopping there keeps it from looking like it overtakes synthesis.
    total_audio = STREAM[-1][1]
    end = STREAM_FIRST_SOUND + total_audio
    ax.plot([STREAM_FIRST_SOUND, end], [0, total_audio],
            color=t["series"][1], linewidth=2, label="audio played back")

    xs, ys = [STREAM_FIRST_SOUND], [0.0]
    for ready, total in STREAM:
        xs += [ready, ready]
        ys += [ys[-1], total]
    xs.append(end)
    ys.append(ys[-1])
    ax.plot(xs, ys, color=t["series"][0], linewidth=2, label="audio synthesised")
    ax.fill_between(xs, ys, [max(0.0, x - STREAM_FIRST_SOUND) for x in xs],
                    color=t["series"][0], alpha=0.12, linewidth=0)

    for ready, total in STREAM:
        ax.plot([ready], [total], "o", markersize=8, color=t["series"][0],
                markeredgecolor=t["surface"], markeredgewidth=2)

    ax.set_xlim(0, end + 0.4)
    ax.set_ylim(0, 12.4)
    ax.set_xlabel("seconds after the user stopped speaking", color=t["secondary"], fontsize=9)
    ax.set_ylabel("seconds of audio", color=t["secondary"], fontsize=9)
    ax.yaxis.grid(True, color=t["grid"], linewidth=1)
    ax.set_axisbelow(True)
    ax.set_title("Kokoro keeps ahead of playback — the gap never closes",
                 color=t["primary"], fontsize=12, fontweight="bold", loc="left", pad=14)
    ax.text(2.1, 7.6, "margin — synthesis stays\nahead, so no stall",
            fontsize=9, color=t["secondary"])
    leg = ax.legend(frameon=False, fontsize=9, loc="lower right")
    for txt in leg.get_texts():
        txt.set_color(t["secondary"])
    style(fig, ax, t)
    save(fig, "streaming-margin", theme)


if __name__ == "__main__":
    for theme in THEMES:
        chart_e2e(theme)
        chart_tts(theme)
        chart_vram(theme)
        chart_stream(theme)
    print(f"wrote {len(list(OUT.glob('*.png')))} charts to {OUT}")
