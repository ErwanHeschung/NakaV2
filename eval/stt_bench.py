"""Phase 0 bench: faster-whisper transcription latency and VRAM cost.

Budget is 300 ms for a typical spoken command. A long clip is also timed to
show how latency scales, since it is the short utterance that decides whether
the assistant feels responsive.

Usage: uv run python eval/stt_bench.py
"""

import argparse
import time
from pathlib import Path

import torch
from faster_whisper import WhisperModel

AUDIO = Path(__file__).parent / "audio"


def vram_mb():
    torch.cuda.synchronize()
    free, total = torch.cuda.mem_get_info()
    return (total - free) / 1024**2


def transcribe(model, path):
    start = time.perf_counter()
    segments, info = model.transcribe(str(path), language="en", beam_size=5)
    text = " ".join(s.text.strip() for s in segments)  # generator: forces the work
    return time.perf_counter() - start, text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="large-v3-turbo")
    ap.add_argument("--compute-type", default="int8_float16")
    args = ap.parse_args()

    before = vram_mb()
    load_start = time.perf_counter()
    model = WhisperModel(args.model, device="cuda", compute_type=args.compute_type)
    load_s = time.perf_counter() - load_start

    # First transcription compiles kernels; Naka warms up at boot for this reason.
    transcribe(model, AUDIO / "short.wav")
    after = vram_mb()

    print(f"\n=== faster-whisper {args.model} ({args.compute_type}) ===")
    print(f"load: {load_s:.1f} s   vram: {after - before:.0f} MB")
    print(f"\n{'clip':>10} {'audio_s':>8} {'latency':>9} {'xRT':>6}")

    for name, audio_s in (("short.wav", 3.0), ("jfk.wav", 11.0)):
        runs = [transcribe(model, AUDIO / name) for _ in range(3)]
        best = min(r[0] for r in runs)
        print(f"{name:>10} {audio_s:>8.1f} {best * 1000:>8.0f}ms {audio_s / best:>5.0f}x")

    _, text = transcribe(model, AUDIO / "short.wav")
    print(f"\ntranscript: {text!r}")

    budget = min(transcribe(model, AUDIO / "short.wav")[0] for _ in range(3))
    print(f"budget: 300 ms on a 3 s command — {'PASS' if budget < 0.3 else 'OVER'}")


if __name__ == "__main__":
    main()
