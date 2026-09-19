"""Speech to text."""

import io
import wave

import numpy as np

from . import models, settings


def decode_wav(data: bytes) -> np.ndarray:
    """PCM WAV bytes to mono float32 at whatever rate the file carries.

    faster-whisper resamples internally, so only channel layout and sample
    width need normalising here.
    """
    with wave.open(io.BytesIO(data), "rb") as f:
        frames = f.readframes(f.getnframes())
        channels = f.getnchannels()
        width = f.getsampwidth()

    if width != 2:
        raise ValueError(f"expected 16-bit PCM, got {width * 8}-bit")

    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
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
