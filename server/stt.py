"""Speech to text."""

import io
import wave

import av
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


def decode_compressed(data: bytes) -> np.ndarray:
    """Anything PyAV can open, to mono float32 at 16 kHz.

    The browser records with MediaRecorder, which produces WebM/Opus and not
    WAV. Rather than reimplementing capture in the panel with an AudioWorklet
    purely to control the container, the server decodes what the browser
    natively produces — PyAV is already here for the Opus replies.
    """
    with av.open(io.BytesIO(data), mode="r") as container:
        resampler = av.AudioResampler(format="flt", layout="mono",
                                      rate=WHISPER_RATE)
        chunks = [
            resampled.to_ndarray().reshape(-1)
            for frame in container.decode(audio=0)
            for resampled in resampler.resample(frame)
        ]
        chunks += [
            resampled.to_ndarray().reshape(-1)
            for resampled in resampler.resample(None)
        ]
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32)


def transcribe_array(audio: np.ndarray) -> str:
    segments, _ = models.stt.transcribe(
        audio,
        language=settings.STT["language"],
        beam_size=settings.STT["beam_size"],
    )
    return " ".join(segment.text.strip() for segment in segments).strip()


def transcribe(data: bytes) -> str:
    """WAV is read directly; everything else goes through PyAV.

    Sniffed rather than taken from the upload's content type, because the two
    clients disagree about what they call it and the first four bytes do not.
    """
    if data[:4] == b"RIFF":
        return transcribe_array(decode_wav(data))
    return transcribe_array(decode_compressed(data))
