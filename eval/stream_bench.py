"""Phase 0 bench: does sentence-by-sentence streaming actually hold up?

Time-to-first-sound only proves the assistant starts talking quickly. The
harder question is whether it keeps talking: the LLM must stay ahead of the
speakers while TTS competes with it for the same GPU. An underrun here is a
gap in the middle of a spoken reply, which sounds worse than a slow start.

A producer thread consumes the LLM stream and emits complete sentences; the
main thread synthesises them and compares audio produced against audio a
speaker would have consumed by then.

Usage: uv run python eval/stream_bench.py
"""

import json
import os
import queue
import re
import threading
import time
import urllib.request

os.environ.setdefault("TQDM_DISABLE", "1")

import torch
from kokoro import KPipeline
from faster_whisper import WhisperModel

LLM_URL = "http://localhost:8080/v1/chat/completions"
AUDIO = os.path.join(os.path.dirname(__file__), "audio", "request.wav")
SENTENCE_END = re.compile(r"[.!?]")


KOKORO_VOICE = "af_heart"
SR = 24000


def synth(pipeline, text):
    chunks = [audio for _, _, audio in pipeline(text, voice=KOKORO_VOICE)]
    return torch.cat(chunks) if len(chunks) > 1 else chunks[0]


SYSTEM = ("You are Naka, a voice assistant. Answer in three or four short "
          "spoken sentences. Never use lists or markdown.")


def sentence_producer(heard, out, started):
    body = {
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": heard}],
        "temperature": 0.0,
        "max_tokens": 256,
        "stream": True,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(LLM_URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    acc = ""
    with urllib.request.urlopen(req, timeout=300) as resp:
        for raw in resp:
            line = raw.decode().strip()
            if not line.startswith("data: ") or line[6:] == "[DONE]":
                continue
            piece = json.loads(line[6:])["choices"][0].get("delta", {}).get("content")
            if not piece:
                continue
            acc += piece
            while True:
                m = SENTENCE_END.search(acc)
                if not m:
                    break
                sentence, acc = acc[: m.end()].strip(), acc[m.end():]
                if sentence:
                    out.put((sentence, time.perf_counter() - started))
    if acc.strip():
        out.put((acc.strip(), time.perf_counter() - started))
    out.put(None)


def main():
    stt = WhisperModel("large-v3-turbo", device="cuda", compute_type="int8_float16")
    tts = KPipeline(lang_code="a", device="cuda")
    list(stt.transcribe(AUDIO, language="en")[0])
    synth(tts, "Warm up.")

    start = time.perf_counter()
    segments, _ = stt.transcribe(AUDIO, language="en")
    heard = " ".join(s.text.strip() for s in segments)

    sentences = queue.Queue()
    threading.Thread(target=sentence_producer, args=(heard, sentences, start),
                     daemon=True).start()

    rows = []
    audio_total = 0.0
    first_sound = None

    while True:
        item = sentences.get()
        if item is None:
            break
        sentence, ready_at = item
        wav = synth(tts, sentence)
        done = time.perf_counter() - start
        duration = wav.shape[-1] / SR

        if first_sound is None:
            first_sound = done
        # Where the speaker would be by now, versus how much audio exists.
        played = max(0.0, done - first_sound)
        audio_total += duration
        rows.append((sentence, ready_at, done, duration, audio_total, played))

    print(f"\nheard: {heard!r}\n")
    print(f"{'#':>2} {'llm_ready':>10} {'tts_done':>9} {'audio_s':>8} "
          f"{'buffered':>9} {'needed':>8} {'margin':>8}")
    underruns = 0
    for i, (sentence, ready, done, dur, total, played) in enumerate(rows, 1):
        margin = total - played
        if margin < 0:
            underruns += 1
        print(f"{i:>2} {ready:>9.2f}s {done:>8.2f}s {dur:>7.2f}s "
              f"{total:>8.2f}s {played:>7.2f}s {margin:>7.2f}s")

    print()
    for i, (sentence, *_rest) in enumerate(rows, 1):
        print(f"  {i}. {sentence}")

    speech_end = first_sound + audio_total
    print(f"\nfirst sound:        {first_sound * 1000:.0f} ms")
    print(f"spoken audio:       {audio_total:.2f} s")
    print(f"finishes speaking:  {speech_end:.2f} s after end of user speech")
    print(f"underruns:          {underruns} "
          f"({'continuous playback' if not underruns else 'AUDIO WOULD STALL'})")


if __name__ == "__main__":
    main()
