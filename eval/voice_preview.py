"""Render the voice so it can be listened to and tuned.

Phase 2 is a tuning loop, not a benchmark: change config/voice.toml, re-render,
listen. Each phrase is written dry (TTS only) and wet (after the DSP chain) so
the chain's contribution is audible on its own.

Usage: uv run python eval/voice_preview.py [--voice "af_heart:60,am_michael:40"]
"""

import argparse
import os
import sys
import time
import wave
from pathlib import Path

os.environ.setdefault("TQDM_DISABLE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import torch  # noqa: F401  preloads CUDA libs before faster_whisper-adjacent imports

from server import dsp, models, settings, tts

OUT = Path(__file__).parent / "audio" / "voice"

PHRASES = [
    ("greeting", "Hey. I'm here — what do you need?"),
    ("answer", "It's about eighteen degrees outside, with a light breeze."),
    ("refusal", "No. I'm not going to do that one."),
    ("thinking", "Hm. That's a stranger question than it looks."),
    ("long", "I've set a timer for twenty minutes, and I'll tell you the moment "
             "it goes off, so you can forget about it until then."),
]


def write_wav(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(pcm.tobytes())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", help="override config/voice.toml for this run")
    args = ap.parse_args()

    if args.voice:
        settings.VOICE["voice"]["name"] = args.voice

    OUT.mkdir(parents=True, exist_ok=True)
    models.load()

    spec = settings.VOICE["voice"]["name"]
    rate = settings.TTS["sample_rate"]
    print(f"voice: {spec}")
    print(f"dsp:   {'on' if settings.VOICE['dsp']['enabled'] else 'off'}")
    print(f"chain: {dsp._chain(rate)}\n")

    tts.synthesize("Warming up.")
    total_dry = total_dsp = 0.0

    for name, text in PHRASES:
        start = time.perf_counter()
        dry = tts.synthesize(text, apply_dsp=False)
        dry_ms = (time.perf_counter() - start) * 1000

        start = time.perf_counter()
        wet = dsp.process(dry, rate)
        dsp_ms = (time.perf_counter() - start) * 1000

        write_wav(OUT / f"{name}_dry.wav", dry, rate)
        write_wav(OUT / f"{name}_wet.wav", wet, rate)

        total_dry += dry_ms
        total_dsp += dsp_ms
        print(f"{name:9s} {len(text):>4} chars  tts {dry_ms:>6.0f}ms  "
              f"dsp {dsp_ms:>5.1f}ms  {wet.size / rate:>5.2f}s")

    print(f"\nDSP adds {total_dsp / len(PHRASES):.1f}ms per sentence on average "
          f"({total_dsp / total_dry * 100:.1f}% on top of synthesis)")
    print(f"written to {OUT}")


if __name__ == "__main__":
    main()
