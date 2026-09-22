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
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
VENV = BUILD / "venv"
DIST = BUILD / "dist" / "Naka"
WIZARD = BUILD / "wizard"
VENDOR = BUILD / "vendor"

# Microsoft's evergreen bootstrapper: a couple of megabytes that install the
# WebView2 runtime if the machine has none, and do nothing if it has. There
# is no version to pin — the link always serves the current one, which is the
# point of it.
WEBVIEW2 = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"

# The installer people download. Anything approaching this means the runtime
# or a model got packed in, which is exactly what first-run setup is for.
MAX_INSTALLER_MB = 80

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


def check_not_running() -> None:
    """A running Naka holds its own DLLs open, and the freeze cannot replace them."""
    listed = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Naka.exe", "/NH"],
                            capture_output=True, text=True).stdout
    if "Naka.exe" in listed:
        sys.exit("Naka is running — quit it from the tray icon, then build again")


def build_venv() -> Path:
    # only-managed: a Python uv installed itself, never whichever one the
    # registry or PATH offers first on the build machine.
    env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(VENV),
               UV_PYTHON_PREFERENCE="only-managed")
    run([tool("uv"), "sync", "--frozen", "--no-install-project",
         "--only-group", "tray", "--only-group", "build"], cwd=ROOT, env=env)
    return VENV / "Scripts" / "python.exe"


def draw_assets(python: Path) -> None:
    # Before the freeze: PyInstaller stamps naka.ico into Naka.exe.
    run([str(python), str(ROOT / "packaging" / "assets.py"), str(WIZARD)])


def freeze() -> None:
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


def version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    found = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not found:
        sys.exit("no version in pyproject.toml")
    return found.group(1)


def iscc() -> str | None:
    """Inno Setup's compiler, wherever it was installed."""
    found = shutil.which("iscc")
    if found:
        return found
    bases = [os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", ""),
             os.environ.get("LOCALAPPDATA", "") + r"\Programs"]
    # 7 only: the installer asks for its dark appearance, which 6 cannot build.
    for release in ("Inno Setup 7",):
        for base in bases:
            candidate = Path(base) / release / "ISCC.exe"
            if base and candidate.exists():
                return str(candidate)
    return None


def installer() -> None:
    compiler = iscc()
    if not compiler:
        print("\nInno Setup 7 is not installed; skipping the installer.\n"
              "Get it from https://jrsoftware.org/isdl.php and run this again.")
        return
    VENDOR.mkdir(parents=True, exist_ok=True)
    bootstrapper = VENDOR / "MicrosoftEdgeWebview2Setup.exe"
    if not bootstrapper.exists():
        print(f"> downloading the WebView2 bootstrapper")
        urllib.request.urlretrieve(WEBVIEW2, bootstrapper)
    run([compiler, f"/DVersion={version()}", f"/DSource={DIST}",
         str(ROOT / "packaging" / "naka.iss")])
    built = BUILD / f"Naka-setup-{version()}.exe"
    size = built.stat().st_size
    print(f"\n{built}: {size / 1e6:.1f} MB", flush=True)
    if size > MAX_INSTALLER_MB * 1e6:
        sys.exit(f"over {MAX_INSTALLER_MB} MB: something heavy was packed in")


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
    check_not_running()
    build_ui()
    shutil.rmtree(DIST, ignore_errors=True)
    draw_assets(build_venv())
    freeze()
    lay_out()
    check_size()
    installer()


if __name__ == "__main__":
    main()
