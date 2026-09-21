r"""Build Naka.exe, with everything it runs laid out beside it.

    uv run --no-sync python packaging\build.py

Standard library only, so any Python runs it. Needs Node (the version in
ui/.nvmrc) and uv on PATH. The result is build\dist\Naka, which the installer
packs as it is:

    Naka.exe, _internal\     the frozen tray (and the setup wizard)
    server\ setup\ client\   source the runtime venv's Python runs
    ui\public\               the built panel and wizard
    config\                  shipped defaults, copied to the data folder once
    manifest\                pinned downloads: runtime.json, models.json
    eval\check_gpu.py        setup's GPU proof
    pyproject.toml uv.lock   what setup's `uv sync` installs

The freeze uses a venv of its own, holding only the tray's packages and
PyInstaller. Freezing from the runtime venv would sweep torch in; the size
check at the end is there in case something finds another way to.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
VENV = BUILD / "venv"
DIST = BUILD / "dist" / "Naka"

# Well above the ~60 MB this should be, and far below the gigabytes that
# torch or CUDA libraries would add.
MAX_MB = 150

# Source trees the server and setup run from. Tests, evals and the CLI client
# stay behind; client is here because the tray imports client.audio, and the
# server does not, but it costs nothing and keeps `python -m client.ptt` working.
TREES = ("server", "setup", "client", "config")
FILES = ("pyproject.toml", "uv.lock", "eval/check_gpu.py")


def run(argv: list[str], **kwargs) -> None:
    print(f"> {' '.join(argv)}", flush=True)
    subprocess.run(argv, check=True, **kwargs)


def tool(name: str) -> str:
    # npm is usually a .cmd shim on Windows, which CreateProcess will not find
    # by bare name; some installs (e.g. nvm-windows) ship npm.exe instead.
    names = (name, name + ".cmd") if sys.platform == "win32" and name == "npm" else (name,)
    for candidate in names:
        found = shutil.which(candidate)
        if found:
            return found
    sys.exit(f"{name} is not on PATH")


def build_ui() -> None:
    wanted = (ROOT / "ui" / ".nvmrc").read_text(encoding="utf-8").strip().lstrip("v")
    node = subprocess.run([tool("node"), "--version"], capture_output=True, text=True,
                          check=True).stdout.strip().lstrip("v")
    if node != wanted:
        sys.exit(f"Node {node} found, ui/.nvmrc wants {wanted}")
    npm = tool("npm")
    run([npm, "ci"], cwd=ROOT / "ui")
    run([npm, "run", "build"], cwd=ROOT / "ui")
    for page in ("main.js", "setup.js"):
        # The server mounts the panel only when it is built, and an unbuilt
        # one would otherwise ship as a blank window.
        if not (ROOT / "ui" / "public" / "js" / page).exists():
            sys.exit(f"the UI build did not produce js/{page}")


def freeze() -> None:
    # only-managed: a Python uv installed itself, never whichever one the
    # registry or PATH offers first on the build machine.
    env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(VENV),
               UV_PYTHON_PREFERENCE="only-managed")
    run([tool("uv"), "sync", "--frozen", "--no-install-project",
         "--only-group", "tray", "--only-group", "build"], cwd=ROOT, env=env)
    run([str(VENV / "Scripts" / "python.exe"), "-m", "PyInstaller",
         str(ROOT / "packaging" / "naka.spec"), "--noconfirm", "--clean",
         "--distpath", str(DIST.parent), "--workpath", str(BUILD / "work")], cwd=ROOT)


def lay_out() -> None:
    skip = shutil.ignore_patterns("__pycache__", "*.pyc")
    for tree in TREES:
        shutil.copytree(ROOT / tree, DIST / tree, ignore=skip)
    shutil.copytree(ROOT / "ui" / "public", DIST / "ui" / "public", ignore=skip)
    shutil.copytree(ROOT / "packaging" / "manifest", DIST / "manifest")
    for name in FILES:
        (DIST / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, DIST / name)


def check_size() -> None:
    total = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file())
    print(f"\n{DIST}: {total / 1e6:.1f} MB", flush=True)
    if total > MAX_MB * 1e6:
        biggest = sorted(((f.stat().st_size, f) for f in DIST.rglob("*") if f.is_file()),
                         reverse=True)[:10]
        for size, f in biggest:
            print(f"  {size / 1e6:8.1f} MB  {f.relative_to(DIST)}")
        sys.exit(f"over {MAX_MB} MB: something heavy was bundled")


def main() -> None:
    if sys.platform != "win32":
        sys.exit("Naka.exe is built on Windows")
    build_ui()
    shutil.rmtree(DIST, ignore_errors=True)
    freeze()
    lay_out()
    check_size()


if __name__ == "__main__":
    main()
