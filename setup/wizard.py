"""The first-run wizard: setup's steps, in a window that looks like Naka.

The page is ui/public/setup.html, built from TypeScript with the rest of the
panel so it shares its orb, icons and controls. It talks to this module through
pywebview's JS bridge — window.pywebview.api — rather than over HTTP: no second
server, no port that could already be taken on someone's machine, and setup
itself has to work before anything else is installed.

Progress goes the other way by calling window.nakaSetup(event) in the page.

    python -m setup --ui
"""

import json
import logging
import threading
import time
from pathlib import Path

from server import paths, settings

from . import fetch, gpu
from . import state as st
from .steps import STEPS, Context, load_models, run

log = logging.getLogger("naka.setup")


class WizardApi:
    """Every public method here is callable from the page as a promise.

    Everything else must start with an underscore. pywebview walks an API
    object's public attributes to expose them, recursively — and a public
    reference to the window sent it crawling through WebView2's internals
    from the wrong thread, failing on each, so the bridge never arrived.
    """

    def __init__(self):
        self._window = None
        self._state = st.load()
        self._thread: threading.Thread | None = None
        self._finished = threading.Event()
        self._last_push = 0.0

    # --------------------------------------------------------- read

    def plan(self) -> dict:
        """What the first screen needs: the card, the models, the defaults."""
        models = load_models()
        verdict = gpu.gate(models)
        fits = set(verdict.get("fits", []))
        identity = {**settings.IDENTITY, **self._state.get("choices", {}).get("identity", {})}
        # The shipped config names the user "User" so the prompts read
        # sensibly before setup; the wizard should ask, not suggest that.
        shipped = settings._read(settings._SHIPPED_SETTINGS).get("identity", {})
        if identity.get("user") == shipped.get("user"):
            identity["user"] = ""
        return {
            "gpu": verdict,
            "models": [{**m, "fits": m["id"] in fits} for m in models],
            "identity": {k: identity.get(k, "") for k in
                         ("assistant", "user", "user_pronoun", "user_possessive")},
            "push_to_talk_key": self._state.get("choices", {}).get(
                "push_to_talk_key", settings.CLIENT.get("push_to_talk_key", "ControlRight")),
            "steps": [{"id": s.id, "title": s.title,
                       "status": self._state.get("steps", {}).get(s.id, {}).get("status", "pending"),
                       "detail": self._state.get("steps", {}).get(s.id, {}).get("detail", "")}
                      for s in STEPS],
            "choices": self._state.get("choices", {}),
            "complete": st.complete(self._state),
        }

    def browse(self) -> dict | None:
        """The native Open dialog, for a model already on this machine."""
        import webview

        picked = self._window.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=False,
            file_types=("GGUF models (*.gguf)", "All files (*.*)"))
        if not picked:
            return None
        path = Path(picked[0])
        try:
            with path.open("rb") as f:
                fetch.check_gguf_header(f.read(8))
        except fetch.FetchError as e:
            return {"path": str(path), "name": path.name, "ok": False, "reason": str(e)}
        except OSError as e:
            return {"path": str(path), "name": path.name, "ok": False,
                    "reason": f"it could not be read: {e}"}
        return {"path": str(path), "name": path.name, "ok": True,
                "bytes": path.stat().st_size}

    def check_link(self, url: str) -> dict:
        """A quick look at a pasted link before committing to it."""
        import httpx

        url = url.strip()
        if not url.startswith("https://"):
            return {"ok": False, "reason": "the link has to start with https://"}
        if "/blob/" in url:
            return {"ok": False, "reason": "that is the page for the file, not the "
                    "file itself — on Hugging Face, use the link with /resolve/ "
                    "instead of /blob/"}
        try:
            response = httpx.head(url, follow_redirects=True, timeout=15.0)
        except httpx.HTTPError as e:
            return {"ok": False, "reason": f"it could not be reached: {e}"}
        if response.status_code != 200:
            return {"ok": False, "reason": f"it answered HTTP {response.status_code}"}
        size = int(response.headers.get("content-length", 0) or 0)
        return {"ok": True, "bytes": size or None,
                "resumable": response.headers.get("accept-ranges") == "bytes"}

    # --------------------------------------------------------- act

    def start(self, choices: dict) -> None:
        """Remember the choices and run every step not yet done."""
        if self._thread and self._thread.is_alive():
            return
        self._state.setdefault("choices", {}).update(choices)
        st.save(self._state)
        self._thread = threading.Thread(target=self._run, daemon=True, name="setup")
        self._thread.start()

    def retry(self) -> None:
        """Run again from the step that failed; finished steps are skipped."""
        self.start({})

    def open_logs(self) -> None:
        import os
        paths.LOGS.mkdir(parents=True, exist_ok=True)
        os.startfile(paths.LOGS)  # noqa: S606 — Explorer on our own folder

    def diagnostics(self) -> str:
        """Everything worth pasting into a bug report, as text."""
        return json.dumps({"gpu": self._state.get("gpu"), "steps": self._state.get("steps"),
                           "data": str(paths.DATA), "app": str(paths.APP)}, indent=2)

    def finish(self) -> None:
        self._finished.set()
        if self._window is not None:
            self._window.destroy()

    # --------------------------------------------------------- internals

    def _run(self) -> None:
        ok = run(Context(state=self._state, report=self._report))
        self._push({"kind": "done", "ok": ok, "complete": st.complete(self._state)})

    def _report(self, step: str, status: str | None = None, done=None, total=None,
                message: str = "") -> None:
        if status is None:
            # Progress: at most four a second across the bridge. Each is an
            # evaluate_js round trip, and a flood of them makes the page lag.
            now = time.monotonic()
            if now - self._last_push < 0.25:
                return
            self._last_push = now
        self._push({"kind": "step" if status else "progress", "step": step,
                    "status": status, "done": done, "total": total, "message": message})

    def _push(self, event: dict) -> None:
        if self._window is None:
            return
        try:
            self._window.evaluate_js(f"window.nakaSetup && window.nakaSetup({json.dumps(event)})")
        except Exception as e:  # the window may be closing
            log.debug("could not reach the page: %s", e)


def run_window() -> bool:
    """Show the wizard until it is finished or closed. Returns whether setup is complete."""
    import webview

    from tray import theme

    theme.dark_menus()
    api = WizardApi()
    window = webview.create_window(
        f"{settings.IDENTITY.get('assistant', 'Naka')} — setup",
        url=str(paths.UI / "setup.html"), js_api=api,
        width=980, height=720, min_size=(820, 620), background_color=theme.BACKGROUND,
    )
    api._window = window

    def on_shown():
        hwnd = 0
        try:
            hwnd = int(window.native.Handle.ToInt64())
        except Exception:
            hwnd = theme.find_window(window.title)
        theme.style_window(hwnd)

    window.events.shown += on_shown
    # http_server: pywebview serves the page's folder over loopback, so the
    # ES modules and the import map resolve exactly as they do in the panel.
    webview.start(http_server=True, gui="edgechromium")
    return st.complete(st.load())
