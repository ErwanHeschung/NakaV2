"""Push-to-talk client. Runs on Windows natively, not inside WSL.

Mic capture in WSL is unreliable, so the client lives on the Windows side and
talks to the server over localhost, which WSL2 forwards automatically.

Hold the hotkey (right ctrl by default) to speak, release to send. The reply
is decoded and played as it arrives rather than after it completes.

    pip install -r client/requirements.txt
    python client/ptt.py --url http://127.0.0.1:8000
"""

import argparse
import io
import queue
import sys
import threading
import time
import wave

import av
import httpx
import numpy as np
import sounddevice as sd
from pynput import keyboard

CAPTURE_RATE = 16000
PLAYBACK_RATE = 48000
BLOCK = 1024


class ResponseReader(io.RawIOBase):
    """File-like view over the streaming HTTP body, for the Opus decoder.

    PyAV pulls bytes synchronously; the HTTP response arrives in chunks on
    another thread. This blocks on an empty queue so decoding starts on the
    first packet instead of waiting for the whole reply.
    """

    def __init__(self):
        self._chunks = queue.Queue()
        self._buffer = b""
        self._done = False

    def feed(self, data):
        self._chunks.put(data)

    def finish(self):
        self._chunks.put(None)

    def readable(self):
        return True

    def readinto(self, target):
        while not self._buffer:
            if self._done:
                return 0
            chunk = self._chunks.get()
            if chunk is None:
                self._done = True
                return 0
            self._buffer = chunk
        take = min(len(target), len(self._buffer))
        target[:take] = self._buffer[:take]
        self._buffer = self._buffer[take:]
        return take


def record(held):
    """Capture while `held` is set. Returns mono int16 at 16 kHz."""
    frames = []
    with sd.InputStream(samplerate=CAPTURE_RATE, channels=1, dtype="int16",
                        blocksize=BLOCK) as stream:
        while held.is_set():
            block, _ = stream.read(BLOCK)
            frames.append(block.copy())

    if not frames:
        return np.zeros(0, dtype=np.int16)
    return np.concatenate(frames).reshape(-1)


def to_wav(samples):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(CAPTURE_RATE)
        f.writeframes(samples.tobytes())
    return buffer.getvalue()


def speak(client, url, wav_bytes, out, agentic):
    """POST the utterance and play the Opus reply as it streams back."""
    reader = ResponseReader()
    marks = {}
    start = time.perf_counter()

    def pump():
        try:
            with client.stream("POST", f"{url.rstrip('/')}/converse",
                               files={"file": ("speech.wav", wav_bytes,
                                               "audio/wav")},
                               data={"agentic": str(agentic).lower()}) as response:
                marks["response"] = time.perf_counter()
                if response.status_code != 200:
                    response.read()
                    print(f"server said {response.status_code}: "
                          f"{response.text[:200]}", file=sys.stderr)
                    return
                for chunk in response.iter_bytes():
                    marks.setdefault("first_byte", time.perf_counter())
                    reader.feed(chunk)
        finally:
            reader.finish()

    threading.Thread(target=pump, daemon=True).start()

    # ffmpeg otherwise buffers several seconds of input deciding what the
    # stream is; the format is known, so let it commit on the first packets.
    container = av.open(reader, mode="r", format="ogg",
                        options={"probesize": "4096", "analyzeduration": "0"})
    marks["opened"] = time.perf_counter()
    resampler = av.AudioResampler(format="flt", layout="mono",
                                  rate=PLAYBACK_RATE)
    for frame in container.decode(audio=0):
        for resampled in resampler.resample(frame):
            marks.setdefault("first_audio", time.perf_counter())
            out.write(resampled.to_ndarray().reshape(-1).astype(np.float32))

    def ms(key):
        return f"{(marks[key] - start) * 1000:.0f}ms" if key in marks else "n/a"

    print(f"  upload+think {ms('response')} | first byte {ms('first_byte')} | "
          f"first audio {ms('first_audio')}")


def main():
    ap = argparse.ArgumentParser()
    # 127.0.0.1, never "localhost": on Windows that name resolves to ::1 as
    # well, and the server is IPv4-only, so every new connection stalls on the
    # IPv6 attempt before falling back — a flat ~2.4s per reply.
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    # Tools cost roughly 600ms of extra latency, because the model must
    # finish deciding whether to call one before anything can be spoken.
    # Worth it to be able to set a timer; --no-tools buys the speed back.
    ap.add_argument("--no-tools", action="store_true",
                    help="disable tools for faster, conversation-only replies")
    ap.add_argument("--key", default="ctrl_r",
                    help="pynput key name to hold, e.g. ctrl_r, alt_r, f13")
    args = ap.parse_args()

    hotkey = getattr(keyboard.Key, args.key)

    # One client for the session, and a keepalive long enough to survive a
    # pause in the conversation: reconnecting across the WSL2 boundary is the
    # expensive part, and the default 5s expiry meant most replies paid it.
    client = httpx.Client(
        timeout=300.0,
        limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=600.0),
    )
    try:
        client.get(f"{args.url.rstrip('/')}/health", timeout=5.0).raise_for_status()
    except httpx.HTTPError as e:
        sys.exit(f"cannot reach Naka at {args.url}: {e}")

    # One listener for the whole session. Starting a second one to watch for
    # the release would race a quick tap: the release could land between the
    # two listeners and never be seen, recording forever.
    held = threading.Event()
    keyboard.Listener(
        on_press=lambda key: held.set() if key == hotkey else None,
        on_release=lambda key: held.clear() if key == hotkey else None,
    ).start()

    # Opened once and held: opening a WASAPI device costs hundreds of
    # milliseconds, which would land squarely in the time-to-first-sound path.
    out = sd.OutputStream(samplerate=PLAYBACK_RATE, channels=1,
                          dtype="float32", latency="low")
    out.start()

    print(f"hold {args.key} to talk, ctrl-c to quit")
    try:
        while True:
            held.wait()
            print("listening...", end="", flush=True)
            samples = record(held)
            seconds = len(samples) / CAPTURE_RATE
            if seconds < 0.3:
                print(" too short, ignored")
                continue
            print(f" {seconds:.1f}s, thinking...")
            speak(client, args.url, to_wav(samples), out, not args.no_tools)
    except KeyboardInterrupt:
        pass
    finally:
        out.stop()
        out.close()


if __name__ == "__main__":
    main()
