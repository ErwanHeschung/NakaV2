"""Microphone in, speaker out, and which key means "talk".

Shared by the tray app and client/ptt.py, which is the same loop without a
window. Nothing here knows about HTTP or the server.
"""

import io
import re
import wave

import numpy as np
import sounddevice as sd
from pynput import keyboard

CAPTURE_RATE = 16000
PLAYBACK_RATE = 48000
BLOCK = 1024


def resolve_key(code):
    """A browser KeyboardEvent.code to something pynput will compare equal to.

    The config is stored the browser's way because that spelling is specified
    and pynput's is not, so the mapping lives on this side. Anything not named
    here is assumed to be a character key, which covers letters and digits.
    """
    named = {
        "ControlLeft": "ctrl_l", "ControlRight": "ctrl_r",
        "AltLeft": "alt_l", "AltRight": "alt_gr",
        "ShiftLeft": "shift_l", "ShiftRight": "shift_r",
        "MetaLeft": "cmd_l", "MetaRight": "cmd_r",
        "Space": "space", "Enter": "enter", "Tab": "tab",
        "CapsLock": "caps_lock", "Escape": "esc", "Backspace": "backspace",
        "Insert": "insert", "Home": "home", "End": "end",
        "PageUp": "page_up", "PageDown": "page_down",
        "ArrowUp": "up", "ArrowDown": "down",
        "ArrowLeft": "left", "ArrowRight": "right",
    }
    if code in named:
        return getattr(keyboard.Key, named[code])
    if re.fullmatch(r"F\d{1,2}", code):
        return getattr(keyboard.Key, code.lower())
    if code.startswith("Key") and len(code) == 4:
        return keyboard.KeyCode.from_char(code[3].lower())
    if code.startswith("Digit") and len(code) == 6:
        return keyboard.KeyCode.from_char(code[5])
    # A pynput name given directly, for anything this does not cover.
    if hasattr(keyboard.Key, code):
        return getattr(keyboard.Key, code)
    raise ValueError(f"do not know how to listen for {code!r}")


def record(held, on_level=None):
    """Capture while `held` is set. Returns mono int16 at 16 kHz.

    `on_level`, if given, is called with each block's 0..1 loudness.
    """
    frames = []
    with sd.InputStream(samplerate=CAPTURE_RATE, channels=1, dtype="int16",
                        blocksize=BLOCK) as stream:
        while held.is_set():
            block, _ = stream.read(BLOCK)
            frames.append(block.copy())
            if on_level is not None:
                rms = float(np.sqrt(np.mean((block.astype(np.float32) / 32768) ** 2)))
                on_level(min(1.0, rms * 4))

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


def open_output():
    """The speaker stream, opened once and held for the session.

    Opening a WASAPI device costs hundreds of milliseconds, which would land
    squarely in the time-to-first-sound path if it happened per reply.
    """
    out = sd.OutputStream(samplerate=PLAYBACK_RATE, channels=1,
                          dtype="float32", latency="low")
    out.start()
    return out


class PcmPlayer:
    """Plays raw little-endian 16-bit mono PCM as it arrives.

    The server's format=pcm reply, for clients that decode nothing: no Opus,
    so no PyAV and no ffmpeg in anything that uses this. The chunks come at
    the TTS rate and are stretched to the held-open output stream's rate.
    """

    # Written a tenth of a second at a time, so a cancel lands within that.
    # Aborting the stream from another thread instead left the blocked write
    # hanging on Windows' MME driver, and the stream would not start again.
    SLICE = PLAYBACK_RATE // 10

    def __init__(self, out, rate: int, cancel=None):
        self.out = out
        self.rate = rate
        self.cancel = cancel
        self._carry = b""

    def feed(self, data: bytes) -> bool:
        """Play a chunk. Returns whether any audio came out of it."""
        data = self._carry + data
        usable = len(data) - (len(data) % 2)
        # A chunk can split a sample down the middle; the odd byte waits.
        self._carry = data[usable:]
        if not usable:
            return False
        samples = np.frombuffer(data[:usable], dtype="<i2").astype(np.float32) / 32768
        if self.rate != PLAYBACK_RATE:
            n = int(round(samples.size * PLAYBACK_RATE / self.rate))
            samples = np.interp(np.linspace(0, samples.size - 1, n),
                                np.arange(samples.size), samples).astype(np.float32)
        for start in range(0, samples.size, self.SLICE):
            if self.cancel is not None and self.cancel.is_set():
                return True
            self.out.write(samples[start:start + self.SLICE])
        return True
