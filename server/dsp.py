"""Post-processing chain that gives Naka her voice.

The character comes from here rather than from the TTS model: this chain is
deterministic, so every sentence sounds the same, which an expressive model
cannot promise. It runs through PyAV's filter graph because there is no ffmpeg
binary available — same filters, no subprocess.

The chain is pitch/formant shift, radio-band EQ, short chorus, heavy
compression, optional bitcrush, limiter. Parameters live in config/voice.toml.
"""

import logging
from fractions import Fraction

import av
import av.filter
import numpy as np

from .settings import VOICE

log = logging.getLogger("naka.dsp")

LAYOUT = "mono"
FORMAT = "flt"


def _chain(sample_rate: int) -> str:
    dsp = VOICE["dsp"]
    steps = []

    semitones = dsp["pitch"]["semitones"]
    if semitones:
        # Resampling shifts pitch and formants together; atempo then puts the
        # duration back. Doing it this way rather than with a phase vocoder is
        # what keeps the formant shift, and the formant shift is the point.
        ratio = 2 ** (semitones / 12)
        steps.append(f"asetrate={int(sample_rate * ratio)}")
        steps.append(f"aresample={sample_rate}")
        steps.append(f"atempo={1 / ratio:.6f}")

    eq = dsp["eq"]
    steps.append(f"highpass=f={eq['highpass_hz']}")
    steps.append(f"lowpass=f={eq['lowpass_hz']}")
    if eq["presence_gain_db"]:
        steps.append(f"equalizer=f={eq['presence_hz']}:t=q:w=1.2:"
                     f"g={eq['presence_gain_db']}")

    chorus = dsp["chorus"]
    if chorus["enabled"]:
        steps.append(
            f"chorus={chorus['in_gain']}:{chorus['out_gain']}:"
            f"{chorus['delays_ms']}:{chorus['decays']}:"
            f"{chorus['speeds_hz']}:{chorus['depths_ms']}"
        )

    comp = dsp["compressor"]
    steps.append(
        f"acompressor=threshold={comp['threshold_db']}dB:ratio={comp['ratio']}:"
        f"attack={comp['attack_ms']}:release={comp['release_ms']}:"
        f"makeup={comp['makeup']}"
    )

    crusher = dsp["crusher"]
    if crusher["enabled"]:
        steps.append(f"acrusher=bits={crusher['bits']}:mix={crusher['mix']}:"
                     f"mode=log")

    steps.append(f"alimiter=limit={dsp['limiter']['limit']}")
    steps.append(f"aformat=sample_fmts={FORMAT}:sample_rates={sample_rate}:"
                 f"channel_layouts={LAYOUT}")
    return ",".join(steps)


def _build(sample_rate: int) -> tuple[av.filter.Graph, object, object]:
    graph = av.filter.Graph()
    source = graph.add_abuffer(
        format=FORMAT, sample_rate=sample_rate, layout=LAYOUT,
        time_base=f"1/{sample_rate}",
    )
    previous = source
    for step in _chain(sample_rate).split(","):
        name, _, args = step.partition("=")
        node = graph.add(name, args or None)
        previous.link_to(node)
        previous = node
    sink = graph.add("abuffersink")
    previous.link_to(sink)
    graph.configure()
    return graph, source, sink


def process(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """Apply the chain to one sentence of mono float32 audio.

    A fresh graph per sentence keeps filter state (compressor envelope, chorus
    phase) from bleeding across sentence boundaries, where the audio is not
    actually continuous.
    """
    if not VOICE["dsp"]["enabled"] or samples.size == 0:
        return samples

    graph, source, sink = _build(sample_rate)

    frame = av.AudioFrame.from_ndarray(
        np.ascontiguousarray(samples.reshape(1, -1)), format=FORMAT, layout=LAYOUT
    )
    frame.sample_rate = sample_rate
    frame.time_base = Fraction(1, sample_rate)
    frame.pts = 0

    source.push(frame)
    source.push(None)

    out = []
    while True:
        try:
            out.append(sink.pull().to_ndarray().reshape(-1))
        except (av.BlockingIOError, av.EOFError):
            break

    if not out:
        return samples
    return np.concatenate(out).astype(np.float32)
