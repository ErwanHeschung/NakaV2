"""Where the time goes between the end of speech and the first sound.

Plays a spoken conversation through the server's own code, stage by stage in
the order /converse runs them: speech to text, the agent loop up to its
first sentence, Kokoro, the DSP chain, PCM. Each utterance is synthesised
once, in a different voice from Naka's, so the pipeline runs on real audio.
The memory reconciler runs between turns as it does after a real one, so the
language model's prompt cache is in the state a real conversation leaves it.

Needs the GPU to itself: Naka must not be running.

Usage:
    uv run python eval/pipeline_bench.py LABEL [--llm-flag FLAG ...]
"""

import argparse
import asyncio
import io
import json
import statistics
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from server import agent, llamacpp, llm, models, ops, settings, stt, tts  # noqa: E402
from server import dsp  # noqa: E402
from server.memory import memory  # noqa: E402

# Long enough for the memory to do what it does in a real session: turns
# leave the recent window, get folded into a summary, and facts are learnt.
UTTERANCES = [
    "Hey Naka, how's it going?",
    "What time is it right now?",
    "I'm thinking of playing something tonight, any idea?",
    "Set a timer for ten minutes for the pasta.",
    "What did I just ask you to time?",
    "By the way, my sister's birthday is on the twelfth of October.",
    "Tell me a quick fact about octopuses.",
    "Why do they have blue blood?",
    "I'm allergic to peanuts, remember that.",
    "What should I cook tonight then?",
    "How long does risotto take?",
    "What was the fact about octopuses again?",
    "Is it late already?",
    "What's my sister's birthday?",
    "Thanks, that's all for now.",
    "Actually, one more thing: what's a good co-op game?",
]
OUT = ROOT / "eval" / "bench"


def wav_bytes(samples: np.ndarray, rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return buffer.getvalue()


def speak(text: str) -> bytes:
    """The user's side, in a voice that is not Naka's, without her DSP."""
    voice = settings.VOICE["voice"]["name"]
    settings.VOICE["voice"]["name"] = "am_michael"
    try:
        samples = tts.synthesize(text, apply_dsp=False)
    finally:
        settings.VOICE["voice"]["name"] = voice
    return wav_bytes(samples, settings.TTS["sample_rate"])


def ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000


async def turn(audio: bytes) -> dict:
    row: dict = {}
    t = time.perf_counter()
    heard = stt.transcribe(audio)
    row["stt"] = ms(t)
    row["heard"] = heard

    t = time.perf_counter()
    messages = memory.messages(heard, "Right now it is 20:14 on Saturday 26 "
                                      "September 2026.")
    actions: list[dict] = []
    spoken: list[str] = []
    stream = agent.run(messages, actions)
    first = await anext(stream)
    row["llm_first_sentence"] = ms(t)
    spoken.append(first)

    t = time.perf_counter()
    raw = tts.synthesize(first, apply_dsp=False)
    row["tts"] = ms(t)
    t = time.perf_counter()
    shaped = dsp.process(raw, settings.TTS["sample_rate"])
    row["dsp"] = ms(t)
    t = time.perf_counter()
    tts.to_pcm16(shaped)
    row["pcm"] = ms(t)
    row["first_sound"] = sum(row[k] for k in
                             ("stt", "llm_first_sentence", "tts", "dsp", "pcm"))
    row["first_sentence_chars"] = len(first)

    async for sentence in stream:
        spoken.append(sentence)
    reply = " ".join(s.strip() for s in spoken)
    row["reply"] = reply
    row["tools"] = [a["name"] for a in actions]
    row["prompt_new"] = llm.last_timings.get("prompt_n")
    row["prompt_cached"] = llm.last_timings.get("cache_n")
    # What main.remember does after a real turn, awaited here so the next
    # turn meets the cache in the state these leave it.
    memory.add_turn(heard, reply, actions)
    await memory.reconcile(heard, reply)
    if memory.needs_summary():
        await memory.summarise()
    return row


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("label")
    ap.add_argument("--llm-flag", action="append", default=[],
                    help="extra llama-server argument, repeatable")
    args = ap.parse_args()

    if args.llm_flag:
        base = llamacpp.argv
        llamacpp.argv = lambda: base() + args.llm_flag

    models.load()
    models.warmup()
    assert await ops._start_llm(), "llama-server did not start"
    assert await ops._await_llm(), "llama-server is not answering"

    memory.recent.clear()
    memory.pending.clear()
    memory.summary = ""
    # The reconciler learns facts from this conversation ("allergic to
    # peanuts"); they must stay in this process, not reach the person's
    # facts.json. An earlier version of this bench wrote two there.
    memory.facts = list(memory.facts)
    memory.save_facts = lambda: None
    audio = [speak(u) for u in UTTERANCES]
    # Warm the language model on something unrelated, as a real session
    # would have been by its second turn.
    await anext(agent.run(memory.messages("Hello.")))
    memory.recent.clear()

    rows = []
    try:
        for index, clip in enumerate(audio):
            row = await turn(clip)
            rows.append(row)
            print(f"  turn {index + 1}: stt {row['stt']:4.0f}  llm {row['llm_first_sentence']:5.0f}"
                  f"  tts {row['tts']:4.0f}  dsp {row['dsp']:4.0f}  pcm {row['pcm']:3.0f}"
                  f"  = {row['first_sound']:5.0f}ms  new {row['prompt_new']!s:>5}"
                  f"  | {row['heard'][:40]!r}"
                  f" -> {row['reply'][:50]!r}")
    finally:
        await ops._stop_llm()

    stages = ("stt", "llm_first_sentence", "tts", "dsp", "pcm", "first_sound",
              "prompt_new")
    later = rows[1:]
    medians = {k: statistics.median(r[k] or 0 for r in later) for k in stages}
    print(f"\n{args.label} medians over turns 2-{len(rows)}: " +
          "  ".join(f"{k} {v:.0f}" for k, v in medians.items()))
    OUT.mkdir(exist_ok=True)
    (OUT / f"pipeline-{args.label}.json").write_text(json.dumps(
        {"label": args.label, "llm_flags": args.llm_flag, "medians": medians,
         "turns": rows}, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
