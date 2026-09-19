"""Model residency and GPU serialisation.

Models stay loaded for the process lifetime; reloading per request would cost
seconds per utterance. Both are warmed at boot because the first inference
compiles kernels — measured at 1238 ms cold against ~150 ms warm.
"""

import asyncio
import logging
import time

import torch  # noqa: F401  must precede faster_whisper: it preloads the CUDA
# libraries that ctranslate2 dlopens but does not bundle.
from faster_whisper import WhisperModel
from kokoro import KPipeline

from . import settings

log = logging.getLogger("naka.models")

# Serialises in-process GPU work. It deliberately does NOT cover the LLM: that
# runs in the llama.cpp container with --parallel 1, so it already serialises
# itself, and holding a lock across it would forbid the TTS/LLM overlap that
# sentence streaming depends on.
gpu_lock = asyncio.Lock()

stt: WhisperModel | None = None
tts: KPipeline | None = None


def load() -> None:
    global stt, tts

    start = time.perf_counter()
    stt = WhisperModel(settings.STT["model"], device="cuda",
                       compute_type=settings.STT["compute_type"])
    log.info("stt loaded in %.1fs", time.perf_counter() - start)

    start = time.perf_counter()
    tts = KPipeline(lang_code=settings.TTS["lang_code"], device="cuda")
    log.info("tts loaded in %.1fs", time.perf_counter() - start)


def warmup() -> None:
    from .stt import transcribe_array
    from .tts import synthesize

    start = time.perf_counter()
    synthesize("Warming up.")
    log.info("tts warm in %.0fms", (time.perf_counter() - start) * 1000)

    import numpy as np
    start = time.perf_counter()
    transcribe_array(np.zeros(16000, dtype="float32"))
    log.info("stt warm in %.0fms", (time.perf_counter() - start) * 1000)


def unload() -> None:
    """Free the card. Phase 6 exposes this; the PC is also used for games."""
    global stt, tts
    stt = None
    tts = None
    torch.cuda.empty_cache()
    log.info("models unloaded")
