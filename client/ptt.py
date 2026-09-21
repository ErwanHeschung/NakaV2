"""Push-to-talk client. Runs on Windows natively, not inside WSL.

The tray app does the same thing without a console; this is the version for
a terminal, and the one that takes the Opus reply rather than raw PCM.

Hold the hotkey to speak, release to send. The reply is decoded and played as
it arrives rather than after it completes.

Which key that is comes from the server, so the panel and this client cannot
disagree about it. --key still overrides, for trying one out.

    uv run python client/ptt.py --url http://127.0.0.1:8000
"""

import argparse
import io
import queue
import sys
import threading
import time
from pathlib import Path

import av
import httpx
import numpy as np
from pynput import keyboard

# Runnable as `python client/ptt.py` as well as `python -m client.ptt`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from client.audio import (CAPTURE_RATE, PLAYBACK_RATE, open_output,  # noqa: E402
                          record, resolve_key, to_wav)


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


def fetch_key(client, url, override):
    if override:
        return override
    try:
        response = client.get(f"{url.rstrip('/')}/client", timeout=5.0)
        response.raise_for_status()
        return response.json()["push_to_talk_key"]
    except (httpx.HTTPError, KeyError, ValueError):
        # Not fatal: a client that cannot ask still has to be usable.
        print("could not read the key from the server, using ControlRight",
              file=sys.stderr)
        return "ControlRight"


def wake(client, url):
    """Nudge the server to load, without waiting for it."""
    try:
        client.post(f"{url.rstrip('/')}/ops/wake", timeout=5.0)
    except httpx.HTTPError:
        pass  # the request itself will load if this did not


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
    # Unset by default: the server holds the answer, so the two clients
    # cannot end up listening for different keys.
    ap.add_argument("--key", default=None,
                    help="override the server's key, e.g. ControlRight, F13")
    args = ap.parse_args()

    # One client for the session, and a keepalive long enough to survive a
    # pause in the conversation, so replies do not each pay for a new
    # connection.
    client = httpx.Client(
        timeout=300.0,
        limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=600.0),
    )
    try:
        client.get(f"{args.url.rstrip('/')}/health", timeout=5.0).raise_for_status()
    except httpx.HTTPError as e:
        sys.exit(f"cannot reach Naka at {args.url}: {e}")

    key_code = fetch_key(client, args.url, args.key)
    try:
        hotkey = resolve_key(key_code)
    except ValueError as e:
        sys.exit(str(e))

    # One listener for the whole session. Starting a second one to watch for
    # the release would race a quick tap: the release could land between the
    # two listeners and never be seen, recording forever.
    held = threading.Event()
    keyboard.Listener(
        on_press=lambda key: held.set() if key == hotkey else None,
        on_release=lambda key: held.clear() if key == hotkey else None,
    ).start()

    out = open_output()

    print(f"hold {key_code} to talk, ctrl-c to quit")
    try:
        while True:
            held.wait()
            # Wake the backend the instant the key goes down, so an idle
            # reload overlaps with speaking instead of following it.
            threading.Thread(target=wake, args=(client, args.url),
                             daemon=True).start()
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
