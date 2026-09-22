r"""Mark setup as done against a runtime that already exists — for development.

The installed app runs setup on first launch and downloads ~15 GB. On a
machine that already has all of it — a checkout's .venv, the models and the
HuggingFace cache in the data folder — that is a long wait for bytes that are
already on the disk, and a bad connection makes it longer.

This writes the state file setup would have written, and links the runtime
venv to one that exists, so Naka.exe starts straight into the tray.

    python scripts\dev_setup_state.py --venv .venv

Nothing here ships: a real install runs the real steps. It checks that each
thing it claims is done is actually there, and says what is missing instead
of writing a state file that lies.
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import paths  # noqa: E402
from setup import steps  # noqa: E402
from setup import state as st  # noqa: E402


def link(target: Path, source: Path) -> None:
    """A junction, so ~5 GB is not copied. Windows follows it like a folder."""
    if target.exists() and not target.is_symlink():
        if any(target.iterdir()):
            sys.exit(f"{target} already holds something; move it away first")
        target.rmdir()
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cmd", "/c", "mklink", "/J", str(target), str(source)],
                   check=True, capture_output=True)


def main() -> int:
    parser = argparse.ArgumentParser(prog="dev_setup_state.py")
    parser.add_argument("--venv", default=".venv",
                        help="a Python environment with the server's packages")
    args = parser.parse_args()

    # A runtime venv already in the data folder — one a real setup built, or
    # one restored there — is used as it is; nothing to link.
    if (steps.VENV / "Scripts" / "python.exe").exists():
        venv = steps.VENV
    else:
        venv = Path(args.venv).resolve()
        if not (venv / "Scripts" / "python.exe").exists():
            sys.exit(f"{venv} is not a Windows virtual environment")

    missing = [str(p) for p in (
        steps.LLAMA / "llama-server.exe",
        paths.MODELS,
        paths.CACHE / "hf" / "hub",
    ) if not p.exists()]
    if missing:
        sys.exit("not in the data folder yet: " + ", ".join(missing))

    if steps.VENV != venv:
        link(steps.VENV, venv)

    state = st.load()
    now = datetime.now().isoformat(timespec="seconds")
    state["steps"] = {step.id: {"status": "ok", "detail": "assumed by "
                                "scripts/dev_setup_state.py", "at": now}
                      for step in steps.STEPS}
    state["uv_lock_sha"] = steps._uv_lock_sha()
    state.setdefault("choices", {}).setdefault("model", "gemma-4-12b")
    st.save(state)
    print(f"{paths.STATE} says setup is complete")
    print(f"{steps.VENV} -> {venv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
