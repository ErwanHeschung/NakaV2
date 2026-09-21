"""How to run llama-server.exe: where it is, what to load, and with what.

The arguments are the ones compose.yml passed to the container, with one
deliberate change: --host is 127.0.0.1 rather than 0.0.0.0. The container had
to bind every interface because it listened inside its own network namespace,
and compose then published it to loopback only. A native process binds where
it is told, so it is told loopback directly.

No PATH is set up for it. Windows looks in an executable's own directory before
anywhere else when resolving its DLLs, and the CUDA 13 runtime is unpacked right
beside llama-server.exe — so it finds its own, and nothing it needs has to be
put where torch, on CUDA 12.8 in the Python process, might pick it up instead.
"""

import sys
from pathlib import Path
from urllib.parse import urlsplit

from . import paths, settings

BIN = paths.RUNTIME / "llama" / ("llama-server.exe" if sys.platform == "win32"
                                 else "llama-server")


def model_path() -> Path:
    return paths.MODELS / settings.LLM["model_file"]


def port() -> int:
    """Taken from the URL everything else talks to, so the two cannot drift."""
    return urlsplit(settings.LLM["url"]).port or 8080


def argv() -> list[str]:
    return [
        str(BIN),
        "--model", str(model_path()),
        "--host", "127.0.0.1",
        "--port", str(port()),
        # Every layer on the GPU. There is no CPU fallback by design: at this
        # model size it would turn a second's reply into a minute's.
        "--n-gpu-layers", "999",
        # One inference at a time: concurrent requests on a single card lose
        # more to thrashing than parallelism wins back.
        "--parallel", "1",
        "--ctx-size", str(settings.LLM["ctx_size"]),
        # The model's own chat template, which tool calling depends on.
        "--jinja",
    ]


def missing() -> str | None:
    """Why it cannot start, in a sentence, or None if it can."""
    if not BIN.exists():
        return f"llama-server is not installed ({BIN} is missing)"
    if not model_path().exists():
        return f"the model file {model_path().name} is not in {paths.MODELS}"
    return None
