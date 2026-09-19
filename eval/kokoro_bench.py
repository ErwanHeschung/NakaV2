"""Phase 0 bench: Kokoro as an alternative to Chatterbox.

Chatterbox is the sole latency bottleneck in the pipeline, so the question is
whether Kokoro's much smaller model buys enough speed to matter. Same
sentences and the same metrics as tts_bench.py, so the two are comparable.

Usage: uv run python eval/kokoro_bench.py
"""

import os
import time

os.environ.setdefault("TQDM_DISABLE", "1")

import torch
from kokoro import KPipeline

# Matches tts_bench.py so the numbers line up.
SENTENCES = [
    ("short", "Sure, one moment."),
    ("medium", "It's about eighteen degrees outside, with a light breeze."),
    ("long", "I've set a timer for twenty minutes, and I'll let you know as soon "
             "as it goes off, so you can get on with something else."),
]
VOICE = "af_heart"
SR = 24000


def vram_mb():
    torch.cuda.synchronize()
    free, total = torch.cuda.mem_get_info()
    return (total - free) / 1024**2


def synth(pipeline, text):
    chunks = [audio for _, _, audio in pipeline(text, voice=VOICE)]
    return torch.cat(chunks) if len(chunks) > 1 else chunks[0]


def main():
    before = vram_mb()
    start = time.perf_counter()
    pipeline = KPipeline(lang_code="a", device="cuda")
    load_s = time.perf_counter() - start

    synth(pipeline, "Warming up.")
    after = vram_mb()

    print(f"\n=== Kokoro ({VOICE}) ===")
    print(f"load: {load_s:.1f} s   vram: {after - before:.0f} MB   sr: {SR} Hz")
    print(f"\n{'case':>8} {'chars':>6} {'latency':>9} {'audio_s':>8} {'RTF':>7}")

    first_chunk = None
    for name, text in SENTENCES:
        runs = []
        for _ in range(3):
            t = time.perf_counter()
            wav = synth(pipeline, text)
            runs.append((time.perf_counter() - t, wav))
        latency = min(r[0] for r in runs)
        audio_s = runs[0][1].shape[-1] / SR
        print(f"{name:>8} {len(text):>6} {latency * 1000:>8.0f}ms "
              f"{audio_s:>8.2f} {audio_s / latency:>6.1f}x")
        if name == "short":
            first_chunk = latency

    print(f"\nbudget: 500 ms first chunk — "
          f"{'PASS' if first_chunk < 0.5 else 'OVER'}")


if __name__ == "__main__":
    main()
