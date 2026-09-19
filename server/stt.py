"""Speech to text."""

import io
import wave

import numpy as np

from . import models, settings


WHISPER_RATE = 16000


def decode_wav(data: bytes) -> np.ndarray:
    """PCM WAV bytes to mono float32 at 16 kHz.

    Given an array rather than a path, faster-whisper assumes 16 kHz and does
    not resample. Passing audio at any other rate silently stretches it — a
    24 kHz clip is read as 1.5x its real length, which costs both accuracy and
    time rather than raising.
    """
    with wave.open(io.BytesIO(data), "rb") as f:
        frames = f.readframes(f.getnframes())
        channels = f.getnchannels()
        width = f.getsampwidth()
        rate = f.getframerate()

    if width != 2:
        raise ValueError(f"expected 16-bit PCM, got {width * 8}-bit")

    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)

    if rate != WHISPER_RATE:
        target = int(round(audio.size * WHISPER_RATE / rate))
        audio = np.interp(
            np.linspace(0, audio.size - 1, target, dtype=np.float64),
            np.arange(audio.size),
            audio,
        ).astype(np.float32)
    return audio


def transcribe_array(audio: np.ndarray) -> str:
    segments, _ = models.stt.transcribe(
        audio,
        language=settings.STT["language"],
        beam_size=settings.STT["beam_size"],
    )
    return " ".join(segment.text.strip() for segment in segments).strip()


def transcribe(wav_bytes: bytes) -> str:
    return transcribe_array(decode_wav(wav_bytes))
