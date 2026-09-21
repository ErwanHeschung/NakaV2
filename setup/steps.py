"""The steps from an empty folder to a Naka that answers.

Each step is safe to run twice: it checks what is already there before
fetching anything, and the runner records its outcome in state.json, so a
setup that stopped halfway — closed window, dropped network, reboot — picks up
at the step that did not finish.

The order is deliberate. The cheap checks come first, so a machine that cannot
run Naka finds out before downloading anything. The check that torch really
works on this card (gpu-proof) comes straight after the runtime is installed
and before the multi-gigabyte model: that is where a card the torch build has
no kernels for would show up, and it should show up before 7 GB of weights.

Nothing here imports torch. Setup runs inside the tray app, which does not
carry it; everything that needs torch runs as a subprocess of the runtime
Python that setup itself installs.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx

from server import autostart, paths, winjob

from . import fetch, gpu
from . import state as st

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

UV_DIR = paths.RUNTIME / "uv"
UV = UV_DIR / "uv.exe"
PYTHON_DIR = paths.RUNTIME / "python"
VENV = paths.RUNTIME / "venv"
VENV_PYTHON = VENV / "Scripts" / "python.exe"
LLAMA = paths.RUNTIME / "llama"
UV_CACHE = paths.CACHE / "uv"
HF_HOME = paths.CACHE / "hf"
DOWNLOADS = paths.CACHE / "downloads"

# The size of the finished runtime venv on Windows, for the progress bar. It is
# an estimate — uv does not report install progress in a machine-readable way —
# but the bar only has to move honestly, not to the byte.
VENV_BYTES = 4_800_000_000
# CPython 3.12 as uv unpacks it, measured.
PYTHON_BYTES = 68_000_000


class StepError(Exception):
    """A failure with a sentence a person can act on."""


def manifest_dir() -> Path:
    # Installed, the manifests ship at the top of the app; in a checkout they
    # live under packaging/.
    for candidate in (paths.APP / "manifest", paths.APP / "packaging" / "manifest"):
        if candidate.exists():
            return candidate
    raise StepError("setup's manifest files are missing from the app folder")


def load_runtime() -> dict:
    return json.loads((manifest_dir() / "runtime.json").read_text(encoding="utf-8"))


def load_models() -> list[dict]:
    """The shipped catalogue, plus any the person added to their own copy."""
    models = json.loads(
        (manifest_dir() / "models.json").read_text(encoding="utf-8"))["models"]
    extra = paths.CONFIG / "models.json"
    if extra.exists():
        known = {m["id"] for m in models}
        models += [m for m in json.loads(extra.read_text(encoding="utf-8")
                                         ).get("models", [])
                   if m.get("id") not in known]
    return models


@dataclass
class Context:
    state: dict
    report: Callable[..., None]
    runtime: dict = field(default_factory=load_runtime)
    models: list[dict] = field(default_factory=load_models)
    step: str = ""

    @property
    def choices(self) -> dict:
        return self.state.setdefault("choices", {})

    def progress(self, done: int | None = None, total: int | None = None,
                 message: str = "") -> None:
        self.report(step=self.step, done=done, total=total, message=message)


# ------------------------------------------------------------------ helpers


def _dir_bytes(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


class _Watch:
    """Reports a directory's growth as progress while a subprocess fills it."""

    def __init__(self, ctx: Context, path: Path, total: int, message: str):
        self.ctx, self.path, self.total, self.message = ctx, path, total, message
        self._stop = threading.Event()
        self._base = _dir_bytes(path) if path.exists() else 0

    def grown(self) -> int:
        return _dir_bytes(self.path) - self._base if self.path.exists() else 0

    def measure(self) -> tuple[int, int, str]:
        return min(self.grown(), self.total), self.total, self.message

    def __enter__(self):
        def loop():
            while not self._stop.wait(1.0):
                self.ctx.progress(*self.measure())
        threading.Thread(target=loop, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self._stop.set()


class _SyncWatch(_Watch):
    """uv sync in two halves: every wheel into uv's cache, then into the venv.

    Watching the venv alone left the bar at zero through the download, which
    is most of the step. Each half is counted as the venv's size: the cache
    holds the wheels unpacked, so it grows to about that too. Each half has a
    bar of its own, so the megabytes shown are real ones. A cache that was
    already warm goes straight to the install half once the venv grows.
    """

    def __init__(self, ctx: Context):
        super().__init__(ctx, VENV, VENV_BYTES, "Installing the runtime")
        self._cache = _Watch(ctx, Path(_uv_env()["UV_CACHE_DIR"]), VENV_BYTES,
                             "Downloading the runtime")

    def measure(self) -> tuple[int, int, str]:
        if self.grown() > 20_000_000:
            return super().measure()
        return self._cache.measure()


def _run(argv: list[str], *, env: dict | None = None, cwd: Path | None = None,
         on_line: Callable[[str], None] | None = None) -> tuple[int, list[str]]:
    """Run a subprocess with no console window. Returns (code, last lines)."""
    tail: deque[str] = deque(maxlen=30)
    process = subprocess.Popen(
        argv, cwd=str(cwd) if cwd else None, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, creationflags=_NO_WINDOW)
    winjob.contain(process.pid)
    assert process.stdout is not None
    for raw in process.stdout:
        line = raw.decode("utf-8", errors="replace").rstrip()
        if line:
            tail.append(line)
            if on_line:
                on_line(line)
    return process.wait(), list(tail)


def _uv_env() -> dict:
    env = dict(os.environ)
    # A cache already configured on this machine is used rather than a fresh
    # one: the wheels in it are the same bytes, checked against the same lock.
    env.setdefault("UV_CACHE_DIR", str(UV_CACHE))
    env.update({
        "UV_PYTHON_INSTALL_DIR": str(PYTHON_DIR),
        # Only the Python uv installs for us. Otherwise it happily picks up
        # whatever Python the machine already has, which is a different
        # runtime on every machine setup runs on.
        "UV_PYTHON_PREFERENCE": "only-managed",
        "UV_PROJECT_ENVIRONMENT": str(VENV),
        "UV_NO_PROGRESS": "1",
    })
    env.pop("VIRTUAL_ENV", None)
    return env


def _uv_lock_sha() -> str:
    return fetch.sha256_of(paths.APP / "uv.lock")


# -------------------------------------------------------------------- steps


def step_gpu(ctx: Context) -> str:
    verdict = gpu.gate(ctx.models)
    ctx.state["gpu"] = verdict
    if not verdict["ok"]:
        raise StepError(verdict["reason"])
    return f"{verdict['name']}, {round(verdict['vram_mb'] / 1024)} GB, driver {verdict['driver']}"


def step_disk(ctx: Context) -> str:
    model = _chosen_model(ctx)
    remaining = (0 if st.done(ctx.state, "venv") else VENV_BYTES + 3_000_000_000) \
        + (0 if st.done(ctx.state, "llama") else 2_000_000_000) \
        + (0 if st.done(ctx.state, "speech") else 2_000_000_000) \
        + (0 if st.done(ctx.state, "model") or model["id"] == "local"
           else model.get("bytes") or 10_000_000_000)
    need = remaining + 2_000_000_000  # room to breathe afterwards
    paths.DATA.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(paths.DATA).free
    if free < need:
        raise StepError(f"Naka needs about {need / 1e9:.0f} GB free on this "
                        f"drive and there are {free / 1e9:.0f} GB.")
    return f"{free / 1e9:.0f} GB free, about {need / 1e9:.0f} GB needed"


def step_uv(ctx: Context) -> str:
    spec = ctx.runtime["uv"]
    if UV.exists():
        return "already installed"
    archive = DOWNLOADS / "uv.zip"
    fetch.download(spec["url"], archive, sha256=spec["sha256"], size=spec["bytes"],
                   on_progress=lambda d, t: ctx.progress(d, t, "Downloading uv"))
    UV_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        for name in z.namelist():
            if name.endswith(".exe"):
                (UV_DIR / Path(name).name).write_bytes(z.read(name))
    archive.unlink(missing_ok=True)
    return f"uv {spec['version']}"


def step_python(ctx: Context) -> str:
    version = ctx.runtime["python"]
    ctx.progress(message=f"Installing Python {version}")
    watch = _Watch(ctx, PYTHON_DIR, PYTHON_BYTES, f"Installing Python {version}")
    # --no-registry: by default uv also lists the Python it installs in the
    # Windows registry (PEP 514), where every other tool on the machine would
    # find it and pick it up. Naka's Python is Naka's alone.
    with watch:
        code, tail = _run([str(UV), "python", "install", "--no-registry", version],
                          env=_uv_env())
    if code != 0:
        raise StepError("Python could not be installed:\n  " + "\n  ".join(tail[-6:]))
    return f"Python {version}"


def step_venv(ctx: Context) -> str:
    """Torch, Whisper, Kokoro and the rest — the bulk of the download.

    --frozen installs exactly what uv.lock says and nothing else resolves.
    --no-install-project because the server runs from the app folder as it
    is; building it into a wheel would only add a step that can fail.
    """
    with _SyncWatch(ctx):
        code, tail = _run(
            [str(UV), "sync", "--frozen", "--no-dev", "--no-install-project",
             "--python", ctx.runtime["python"]],
            env=_uv_env(), cwd=paths.APP,
            on_line=lambda line: ctx.progress(message=line[:120]))
    if code != 0:
        raise StepError("The runtime could not be installed:\n  " + "\n  ".join(tail[-8:]))
    # Recorded so a later version of the app can tell whether its lockfile
    # changed, and re-run only this step when it has.
    ctx.state["uv_lock_sha"] = _uv_lock_sha()
    return f"{_dir_bytes(VENV) / 1e9:.1f} GB installed"


def step_llama(ctx: Context) -> str:
    spec = ctx.runtime["llama"]
    if (LLAMA / "llama-server.exe").exists() and ctx.state.get("llama_build") == spec["build"]:
        return f"build {spec['build']} already installed"
    LLAMA.mkdir(parents=True, exist_ok=True)
    total = sum(f["bytes"] for f in spec["files"])
    before = 0
    for item in spec["files"]:
        archive = DOWNLOADS / Path(item["url"]).name
        fetch.download(
            item["url"], archive, sha256=item["sha256"], size=item["bytes"],
            on_progress=lambda d, t, b=before: ctx.progress(b + d, total,
                                                            "Downloading llama.cpp"))
        with zipfile.ZipFile(archive) as z:
            z.extractall(LLAMA)
        archive.unlink(missing_ok=True)
        before += item["bytes"]
    ctx.state["llama_build"] = spec["build"]
    return f"llama.cpp {spec['build']}"


def step_gpu_proof(ctx: Context) -> str:
    ctx.progress(message="Checking the runtime can use your graphics card")
    code, tail = _run([str(VENV_PYTHON), str(paths.APP / "eval" / "check_gpu.py"),
                       "--json"], cwd=paths.APP)
    try:
        verdict = json.loads(tail[-1])
    except (IndexError, ValueError):
        raise StepError("The graphics card check did not run:\n  " + "\n  ".join(tail[-6:]))
    if not verdict.get("ok"):
        raise StepError(verdict.get("reason", "the graphics card check failed"))
    return f"{verdict['device']}, compute {verdict['capability'][0]}.{verdict['capability'][1]}"


def _hf_snapshot(repo: str, revision: str) -> Path:
    """Where the runtime looks for a model's files: the Hugging Face cache.

    On Windows without Developer Mode the cache cannot symlink, so it keeps
    real files under snapshots/<revision>/ — which is exactly where these go.
    """
    return HF_HOME / "hub" / ("models--" + repo.replace("/", "--")) \
        / "snapshots" / revision


def step_speech(ctx: Context) -> str:
    """Whisper and Kokoro, so nothing is downloaded at runtime.

    The two large files — Whisper's weights and Kokoro's — come through our
    own downloader rather than Hugging Face's: it resumes a partial file
    across runs and reports real progress. The hub library names each attempt
    with a random suffix, so an interrupted 1.6 GB download started over from
    nothing, and on a slow line that was an hour lost per interruption. The
    handful of small files then come through the hub library, which also
    records the revision the runtime will ask for.
    """
    big = [(entry, name, spec) for entry in ctx.runtime["speech"]
           for name, spec in entry.get("verify", {}).items()]
    total = sum(spec["bytes"] for _, _, spec in big)
    before = 0
    for entry, name, spec in big:
        repo, revision = entry["repo"], entry["revision"]
        label = repo.split("/")[-1]
        fetch.download(
            f"https://huggingface.co/{repo}/resolve/{revision}/{name}",
            _hf_snapshot(repo, revision) / name,
            sha256=spec["sha256"], size=spec["bytes"],
            on_progress=lambda d, t, b=before, n=label: ctx.progress(
                b + d, total, f"Downloading {n}"))
        before += spec["bytes"]

    ctx.progress(total, total, "Fetching the remaining small files")
    manifest = manifest_dir() / "runtime.json"
    env = dict(os.environ)
    env.pop("HF_HUB_OFFLINE", None)
    code, tail = _run(
        [str(VENV_PYTHON), "-m", "setup.prefetch", "--manifest", str(manifest),
         "--hf-home", str(HF_HOME)],
        env=env, cwd=paths.APP)
    errors = [json.loads(line) for line in tail
              if line.startswith("{") and '"error"' in line]
    if code != 0:
        reason = errors[-1]["reason"] if errors else "\n  ".join(tail[-6:])
        raise StepError(f"The speech models could not be downloaded: {reason}")
    return "Whisper and Kokoro"


def _chosen_model(ctx: Context) -> dict:
    choice = ctx.choices.get("model")
    if not choice:
        raise StepError("no language model has been chosen")
    for model in ctx.models:
        if model["id"] == choice:
            return model
    local = Path(choice)
    if local.is_absolute():
        # A file already on this machine, chosen with Browse. Used where it
        # is rather than copied: a second 7 GB for the same bytes would be a
        # strange thing to do to someone's disk. Its "file" is the full path,
        # which llamacpp.model_path() takes as it is.
        return {"id": "local", "name": local.name, "path": str(local),
                "file": str(local), "bytes": local.stat().st_size
                if local.exists() else None}
    if choice.startswith("https://"):
        # A link someone pasted. No checksum can be known in advance; the
        # file is proved by loading it instead (the prove step).
        name = Path(choice.split("?")[0]).name
        if not name.lower().endswith(".gguf"):
            name += ".gguf"
        return {"id": "custom", "name": name, "url": choice, "file": name}
    raise StepError(f"unknown model {choice!r}")


def step_model(ctx: Context) -> str:
    model = _chosen_model(ctx)
    if model["id"] == "local":
        path = Path(model["path"])
        if not path.is_file():
            raise StepError(f"{path} is not there any more")
        # The header now, so a wrong file is caught in a second; whether this
        # llama.cpp can actually run it is the prove step's job.
        with path.open("rb") as f:
            fetch.check_gguf_header(f.read(8))
        return f"{path.name}, used where it is ({path.stat().st_size / 1e9:.1f} GB)"
    digest = fetch.download(
        model["url"], paths.MODELS / model["file"], sha256=model.get("sha256"),
        size=model.get("bytes"), gguf=True,
        on_progress=lambda d, t: ctx.progress(d, t, f"Downloading {model['name']}"))
    if model["id"] == "custom":
        _remember_custom(model, digest)
    return model["name"]


def _remember_custom(model: dict, digest: str) -> None:
    """Record a pasted model with its hash, so a later repair can verify it."""
    path = paths.CONFIG / "models.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"models": []}
    entry = {"id": Path(model["file"]).stem, "name": model["file"], "url": model["url"],
             "file": model["file"], "sha256": digest,
             "bytes": (paths.MODELS / model["file"]).stat().st_size}
    data["models"] = [m for m in data["models"] if m.get("file") != model["file"]] + [entry]
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def step_configure(ctx: Context) -> str:
    """Write what the person told setup into their settings.toml."""
    import tomlkit

    model = _chosen_model(ctx)
    target = paths.CONFIG / "settings.toml"
    doc = tomlkit.parse(target.read_text(encoding="utf-8"))
    identity = ctx.choices.get("identity", {})
    for key in ("assistant", "user", "user_pronoun", "user_possessive"):
        if identity.get(key):
            doc.setdefault("identity", tomlkit.table())[key] = identity[key]
    doc.setdefault("llm", tomlkit.table())["model_file"] = model["file"]
    if ctx.choices.get("push_to_talk_key"):
        doc.setdefault("client", tomlkit.table())["push_to_talk_key"] = \
            ctx.choices["push_to_talk_key"]
    tmp = target.with_suffix(".toml.tmp")
    tmp.write_text(tomlkit.dumps(doc), encoding="utf-8")
    tmp.replace(target)
    return f"{identity.get('user', 'you')}, {model['file']}"


def step_prove(ctx: Context) -> str:
    """Start llama-server on the model and have it answer once.

    The only real test of a model file, and for a pasted link the only test
    there is: a checksum says the bytes arrived, not that this llama.cpp can
    run them. Uses its own port so a Naka already running is not disturbed.
    """
    model = _chosen_model(ctx)
    port = 8091
    argv = [str(LLAMA / "llama-server.exe"), "--model", str(paths.MODELS / model["file"]),
            "--host", "127.0.0.1", "--port", str(port), "--n-gpu-layers", "999",
            "--ctx-size", "2048", "--jinja"]
    ctx.progress(message=f"Loading {model['name']} to check it runs")
    process = subprocess.Popen(argv, cwd=str(LLAMA), stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               creationflags=_NO_WINDOW)
    winjob.contain(process.pid)
    tail: deque[str] = deque(maxlen=20)

    def drain():
        assert process.stdout is not None
        for raw in process.stdout:
            tail.append(raw.decode("utf-8", errors="replace").rstrip())
    threading.Thread(target=drain, daemon=True).start()

    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise StepError(f"{model['name']} would not load:\n  "
                                + "\n  ".join(list(tail)[-8:]))
            try:
                if httpx.get(f"{base}/health", timeout=2.0).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1.0)
        else:
            raise StepError(f"{model['name']} did not finish loading within five minutes")

        started = time.monotonic()
        reply = httpx.post(f"{base}/v1/chat/completions", timeout=120.0, json={
            "messages": [{"role": "user", "content": "Say hello in three words."}],
            "max_tokens": 16, "temperature": 0,
            "chat_template_kwargs": {"enable_thinking": False},
        }).json()["choices"][0]["message"].get("content", "").strip()
        return f"answered in {(time.monotonic() - started) * 1000:.0f}ms: {reply[:40]!r}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def step_autostart(ctx: Context) -> str:
    on = bool(ctx.choices.get("autostart", True))
    # Written straight to the registry, which is the only place Windows looks.
    os.environ["NAKA_LAUNCH"] = autostart.default_command()
    autostart.set_enabled(on)
    return "on" if on else "off"


@dataclass(frozen=True)
class Step:
    id: str
    title: str
    run: Callable[[Context], str]


STEPS = [
    Step("gpu", "Checking your graphics card", step_gpu),
    Step("disk", "Checking disk space", step_disk),
    Step("uv", "Getting the installer tool", step_uv),
    Step("python", "Installing Python", step_python),
    Step("venv", "Installing the runtime", step_venv),
    Step("llama", "Installing llama.cpp", step_llama),
    Step("gpu-proof", "Checking the runtime can use your card", step_gpu_proof),
    Step("speech", "Downloading the speech models", step_speech),
    Step("model", "Downloading the language model", step_model),
    Step("configure", "Saving your settings", step_configure),
    Step("prove", "Checking the model runs", step_prove),
    Step("autostart", "Starting with Windows", step_autostart),
]


def run(ctx: Context, redo_from: str | None = None) -> bool:
    """Run every step not already done. Returns whether all of them are.

    `redo_from` forces that step and every one after it to run again — the
    "run setup again from the model" path, for choosing a different model.
    """
    forcing = False
    for step in STEPS:
        forcing = forcing or step.id == redo_from
        ctx.step = step.id
        if st.done(ctx.state, step.id) and not forcing:
            ctx.report(step=step.id, status="skipped", message=step.title)
            continue
        ctx.report(step=step.id, status="running", message=step.title)
        st.mark(ctx.state, step.id, "running")
        try:
            detail = step.run(ctx)
        except (StepError, fetch.FetchError) as e:
            st.mark(ctx.state, step.id, "failed", str(e))
            ctx.report(step=step.id, status="failed", message=str(e))
            return False
        except Exception as e:  # anything unexpected is still a failed step, with its reason
            st.mark(ctx.state, step.id, "failed", f"{type(e).__name__}: {e}")
            ctx.report(step=step.id, status="failed", message=f"{type(e).__name__}: {e}")
            return False
        st.mark(ctx.state, step.id, "ok", detail)
        ctx.report(step=step.id, status="ok", message=detail)
    return True
