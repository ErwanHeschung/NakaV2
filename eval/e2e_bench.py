"""Phase 0 bench: end-to-end latency, end of speech to first sound.

Chains the real components — faster-whisper, the Gemma container, Kokoro — and
reports where the 1.3 s budget actually goes. The user utterance is synthesised
once up front (not timed) so the pipeline runs on real audio.

Every stage is measured over several runs and reported as a median. A single
sample is not enough: an earlier version of this script took one, and the noise
was large enough to make the LLM stage look like it depended on the TTS engine,
which it cannot.

Usage: uv run python eval/e2e_bench.py [--runs 5]
"""

import argparse
import json
import os
import re
import statistics
import time
import urllib.request
import wave
from pathlib import Path

os.environ.setdefault("TQDM_DISABLE", "1")

import torch  # must precede faster_whisper: preloads the CUDA libs ctranslate2 dlopens
from faster_whisper import WhisperModel
from kokoro import KPipeline

LLM_URL = "http://localhost:8080/v1/chat/completions"
UTTERANCE = "What's the weather like in Paris today?"
SYSTEM = ("You are Naka, a voice assistant. Reply in one or two short spoken "
          "sentences. Never use lists or markdown.")
AUDIO = Path(__file__).parent / "audio"
SENTENCE_END = re.compile(r"[.!?]")
KOKORO_VOICE = "af_heart"
SR = 24000
BUDGET_MS = 1300


def synth(pipeline, text):
    chunks = [audio for _, _, audio in pipeline(text, voice=KOKORO_VOICE)]
    return torch.cat(chunks) if len(chunks) > 1 else chunks[0]


def write_wav(path, wav, sample_rate):
    pcm = (wav.float().cpu().clamp(-1, 1) * 32767).to(torch.int16).numpy()
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(pcm.tobytes())


def llm_first_sentence(heard, timeout):
    """Stream until the first sentence closes. Returns (ttft_s, stage_s, text)."""
    body = {
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": heard}],
        "temperature": 0.0,
        "max_tokens": 128,
        "stream": True,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(LLM_URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    start = time.perf_counter()
    acc, ttft = "", None
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode().strip()
            if not line.startswith("data: ") or line[6:] == "[DONE]":
                continue
            piece = json.loads(line[6:])["choices"][0].get("delta", {}).get("content")
            if not piece:
                continue
            if ttft is None:
                ttft = time.perf_counter() - start
            acc += piece
            m = SENTENCE_END.search(acc)
            if m:
                return ttft, time.perf_counter() - start, acc[: m.end()]
    return ttft, time.perf_counter() - start, acc


def one_pass(stt, tts, prompt_wav, timeout):
    t0 = time.perf_counter()
    segments, _ = stt.transcribe(str(prompt_wav), language="en")
    heard = " ".join(s.text.strip() for s in segments)
    t_stt = time.perf_counter()

    _, _, sentence = llm_first_sentence(heard, timeout)
    t_llm = time.perf_counter()

    audio = synth(tts, sentence)
    t_tts = time.perf_counter()

    return {
        "stt": (t_stt - t0) * 1000,
        "llm": (t_llm - t_stt) * 1000,
        "tts": (t_tts - t_llm) * 1000,
        "total": (t_tts - t0) * 1000,
        "heard": heard,
        "sentence": sentence.strip(),
        "audio_s": audio.shape[-1] / SR,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--timeout", type=float, default=300.0)
    args = ap.parse_args()

    stt = WhisperModel("large-v3-turbo", device="cuda", compute_type="int8_float16")
    tts = KPipeline(lang_code="a", device="cuda")

    prompt_wav = AUDIO / "request.wav"
    if not prompt_wav.exists():
        write_wav(prompt_wav, synth(tts, UTTERANCE), SR)

    # Warm every stage on the real payload: the first pass compiles kernels and
    # fills the server's prompt cache, and would otherwise dominate run 1.
    one_pass(stt, tts, prompt_wav, args.timeout)

    runs = [one_pass(stt, tts, prompt_wav, args.timeout) for _ in range(args.runs)]
    med = {k: statistics.median(r[k] for r in runs)
           for k in ("stt", "llm", "tts", "total")}

    print(f"\nheard:  {runs[0]['heard']!r}")
    print(f"spoke:  {runs[0]['sentence']!r}")
    print(f"\n{'stage':<28}{'median':>9}{'min':>8}{'max':>8}   (n={args.runs})")
    for key, label in (("stt", "STT (transcribe)"),
                       ("llm", "LLM (to end of sentence)"),
                       ("tts", "TTS synthesis")):
        vals = [r[key] for r in runs]
        print(f"{label:<28}{med[key]:>8.0f}ms{min(vals):>7.0f}{max(vals):>8.0f}")
    print("-" * 53)
    totals = [r["total"] for r in runs]
    print(f"{'TIME TO FIRST SOUND':<28}{med['total']:>8.0f}ms"
          f"{min(totals):>7.0f}{max(totals):>8.0f}")
    print(f"\nbudget {BUDGET_MS} ms — "
          f"{'PASS' if med['total'] < BUDGET_MS else 'OVER'}")
    print(f"(first sentence is {runs[0]['audio_s']:.1f}s of audio, so the rest of "
          f"the reply synthesises while it plays)")


if __name__ == "__main__":
    main()
