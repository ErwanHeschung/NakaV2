r"""Run setup from a terminal. The wizard window does the same thing with a UI.

    python -m setup --model gemma-4-12b --user Erwan --pronoun he --possessive his
    python -m setup --model https://huggingface.co/.../resolve/main/x.gguf
    python -m setup --model "D:\Models\my-model.gguf"       # one you already have
    python -m setup --redo-from model --model qwen3-14b    # switch models
    python -m setup --check                                # the GPU gate only

Choices are remembered in state.json, so a rerun after an interruption needs
no arguments at all and carries on from the step that did not finish.
"""

import argparse
import sys
import time

from . import gpu
from . import state as st
from .steps import STEPS, Context, load_models, run

_last_line = [0.0]


def report(step: str, status: str | None = None, done=None, total=None,
           message: str = "") -> None:
    if status:
        mark = {"running": "..", "ok": "ok", "failed": "!!", "skipped": "--"}[status]
        print(f"\n[{mark}] {step:10} {message}" if status == "running"
              else f"[{mark}] {step:10} {message}", flush=True)
        return
    # Progress, at most once a second so a terminal is not flooded.
    now = time.monotonic()
    if now - _last_line[0] < 1.0:
        return
    _last_line[0] = now
    if total:
        print(f"     {done / 1e6:9.1f} / {total / 1e6:.1f} MB  "
              f"{100 * done / total:5.1f}%  {message}", flush=True)
    elif message:
        print(f"     {message}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m setup")
    parser.add_argument("--model", help="a catalogue id, an https link to a GGUF, "
                        "or the full path of a GGUF already on this machine")
    parser.add_argument("--assistant")
    parser.add_argument("--user")
    parser.add_argument("--pronoun")
    parser.add_argument("--possessive")
    parser.add_argument("--autostart", action=argparse.BooleanOptionalAction)
    parser.add_argument("--redo-from", choices=[s.id for s in STEPS])
    parser.add_argument("--check", action="store_true", help="the GPU gate only")
    args = parser.parse_args()

    models = load_models()
    if args.check:
        verdict = gpu.gate(models)
        for key, value in verdict.items():
            print(f"{key}: {value}")
        return 0 if verdict["ok"] else 1

    state = st.load()
    choices = state.setdefault("choices", {})
    if args.model:
        choices["model"] = args.model
    identity = choices.setdefault("identity", {})
    for key, value in (("assistant", args.assistant), ("user", args.user),
                       ("user_pronoun", args.pronoun),
                       ("user_possessive", args.possessive)):
        if value:
            identity[key] = value
    if args.autostart is not None:
        choices["autostart"] = args.autostart
    if not choices.get("model"):
        print("choose a model with --model: "
              + ", ".join(m["id"] for m in models) + ", or an https link")
        return 2
    st.save(state)

    started = time.monotonic()
    ok = run(Context(state=state, report=report, models=models),
             redo_from=args.redo_from)
    minutes = (time.monotonic() - started) / 60
    print(f"\n{'Setup complete' if ok else 'Setup stopped'} after {minutes:.1f} min.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
