"""Text to speech, and Opus encoding of the result.

Kokoro has no streaming API — generate() returns a whole waveform — so the
pipeline synthesises one sentence at a time and appends each to a single Ogg
Opus stream the client can play as it arrives.
"""

import io

import av
import numpy as np
import torch

from . import models, settings


def synthesize(text: str) -> np.ndarray:
    """Synthesise one sentence to mono float32 at the TTS sample rate."""
    chunks = [audio for _, _, audio in
              models.tts(text, voice=settings.TTS["voice"])]
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    wav = torch.cat(chunks) if len(chunks) > 1 else chunks[0]
    return wav.detach().float().cpu().numpy()


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
