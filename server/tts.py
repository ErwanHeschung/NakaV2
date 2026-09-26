"""Text to speech, and Opus encoding of the result.

Kokoro has no streaming API — generate() returns a whole waveform — so the
pipeline synthesises one sentence at a time and appends each to a single Ogg
Opus stream the client can play as it arrives.
"""

import io

import av
import numpy as np
import torch

from . import dsp, models, settings, speech


_voice_cache: dict[str, torch.Tensor] = {}


def resolve_voice(spec: str) -> str | torch.Tensor:
    """Resolve a voice spec, which may be a weighted blend.

    "af_heart" passes straight through; "af_heart:60,am_michael:40" is mixed
    by weight. Kokoro's own comma syntax exists but only averages equally.
    """
    if ":" not in spec:
        return spec
    if spec in _voice_cache:
        return _voice_cache[spec]

    packs, weights = [], []
    for part in spec.split(","):
        name, _, weight = part.strip().partition(":")
        packs.append(models.tts.load_single_voice(name.strip()))
        weights.append(float(weight) if weight else 1.0)

    total = sum(weights)
    blended = sum(pack * (weight / total)
                  for pack, weight in zip(packs, weights))
    _voice_cache[spec] = blended
    return blended


def speakable(text: str) -> str:
    """The segment as it should sound. See server/speech.py."""
    return speech.spoken(text)


def synthesize(text: str, apply_dsp: bool = True) -> np.ndarray:
    """Synthesise one sentence to mono float32 at the TTS sample rate."""
    text = speakable(text)
    if not text:
        return np.zeros(0, dtype=np.float32)
    voice = resolve_voice(settings.VOICE["voice"]["name"])
    chunks = [audio for _, _, audio in models.tts(text, voice=voice)]
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    wav = torch.cat(chunks) if len(chunks) > 1 else chunks[0]
    samples = wav.detach().float().cpu().numpy()
    if apply_dsp:
        samples = dsp.process(samples, settings.TTS["sample_rate"])
    return samples


class OpusStream:
    """Incremental Ogg Opus encoder.

    Audio is pushed a sentence at a time and the newly produced container bytes
    are returned, so the client can start playing before the reply is finished.
    """

    def __init__(self) -> None:
        self._buffer = io.BytesIO()
        self._read_pos = 0
        self._container = av.open(self._buffer, mode="w", format="ogg")
        self._stream = self._container.add_stream(
            "libopus", rate=settings.AUDIO["opus_sample_rate"]
        )
        self._stream.layout = "mono"
        self._stream.bit_rate = settings.AUDIO["bitrate"]
        self._resampler = av.AudioResampler(
            format="s16", layout="mono", rate=settings.AUDIO["opus_sample_rate"]
        )
        # libopus only accepts fixed-size frames, so samples are buffered here
        # and drawn off in exact multiples.
        self._fifo = av.AudioFifo()

    def _drain(self) -> bytes:
        self._buffer.seek(self._read_pos)
        data = self._buffer.read()
        self._read_pos += len(data)
        return data

    def push(self, samples: np.ndarray) -> bytes:
        if samples.size:
            frame = av.AudioFrame.from_ndarray(
                np.ascontiguousarray(samples.reshape(1, -1)),
                format="flt", layout="mono",
            )
            frame.sample_rate = settings.TTS["sample_rate"]
            for resampled in self._resampler.resample(frame):
                self._fifo.write(resampled)

        size = self._stream.frame_size or 960
        while (frame := self._fifo.read(size)) is not None:
            for packet in self._stream.encode(frame):
                self._container.mux(packet)
        return self._drain()

    def close(self) -> bytes:
        remainder = self._fifo.read()
        if remainder is not None:
            for packet in self._stream.encode(remainder):
                self._container.mux(packet)
        for packet in self._stream.encode(None):
            self._container.mux(packet)
        self._container.close()
        return self._drain()


def encode_opus(samples: np.ndarray) -> bytes:
    stream = OpusStream()
    return stream.push(samples) + stream.close()


def to_pcm16(samples: np.ndarray) -> bytes:
    """Mono float32 to little-endian 16-bit PCM, for clients that decode none.

    Clipped rather than scaled to fit: the DSP chain ends in a limiter, so
    anything past full scale here is a bug upstream, and quietly rescaling the
    whole reply would hide it.
    """
    if not samples.size:
        return b""
    clipped = np.clip(samples, -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()
