"""The server, as a child of the tray.

The tray starts it, notices when it dies, restarts it a few times, and stops
it on the way out. Until now something outside the app had to be running
before anything worked — client/ptt.py simply exits if the server is not up.
Here the tray is the one thing a person starts, and it brings the rest.

The server runs in the tray's job object (server/winjob.py), so if the tray is
killed the server goes with it — and llama-server, in the server's own job,
goes with that. Nothing is left holding the GPU.
"""

import logging
import os
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

import httpx

from server import paths, winjob

log = logging.getLogger("naka.tray.server")

_FLAGS = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
          if sys.platform == "win32" else 0)

# Restarts allowed inside the window, and the wait before each. A server that
# dies three times in ten minutes is not going to be fixed by a fourth try,
# and a tight restart loop would hide the problem while burning the GPU.
_BACKOFF = (5, 15, 45)
_WINDOW = 600


def server_python() -> Path:
    """The Python that has torch in it.

    Installed, that is the runtime venv setup built. From a checkout it is the
    repo's own .venv, which is also what the tray is running from.
    """
    override = os.environ.get("NAKA_PYTHON")
    candidates = [Path(override)] if override else []
    candidates += [
        paths.RUNTIME / "venv" / "Scripts" / "python.exe",
        paths.APP / ".venv" / "Scripts" / "python.exe",
    ]
    if not getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).with_name("python.exe"))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("no Python runtime found; setup has not been run")


class Server:
    def __init__(self, port: int, on_change=None):
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        self.state = "stopped"  # starting | ready | failed | stopped | attached
        self.tail: deque[str] = deque(maxlen=50)
        self._process: subprocess.Popen | None = None
        self._stopping = False
        self._restarts: deque[float] = deque()
        self._on_change = on_change or (lambda state: None)

    # ------------------------------------------------------------------

    def healthy(self) -> bool:
        try:
            return httpx.get(f"{self.url}/health", timeout=2.0).status_code == 200
        except httpx.HTTPError:
            return False

    def start(self) -> None:
        # Someone already running a server on this port — a developer's
        # terminal, most likely. Use it rather than failing to bind and
        # crash-looping on "address already in use".
        if self.healthy():
            log.info("a server is already answering on %s; using it", self.url)
            self._set("attached")
            return
        self._spawn()
        threading.Thread(target=self._watch, daemon=True, name="server-watch").start()

    def _spawn(self) -> None:
        python = server_python()
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        self._stopping = False
        self._process = subprocess.Popen(
            [str(python), "-m", "uvicorn", "server.main:app",
             "--host", "127.0.0.1", "--port", str(self.port),
             # The panel polls every two seconds; an access line per poll is
             # noise in a log nobody is watching live.
             "--no-access-log"],
            cwd=str(paths.APP), env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, creationflags=_FLAGS,
        )
        winjob.contain(self._process.pid)
        threading.Thread(target=self._drain, args=(self._process,),
                         daemon=True, name="server-drain").start()
        log.info("server started (pid %d) with %s", self._process.pid, python)
        self._set("starting")

    def _drain(self, process: subprocess.Popen) -> None:
        # Read whether or not anyone wants it: a full pipe blocks the child.
        assert process.stdout is not None
        for raw in process.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                self.tail.append(line)

    def _watch(self) -> None:
        while True:
            process = self._process
            if process is None:
                return
            # Up yet?
            if self.state == "starting" and self.healthy():
                self._set("ready")
            code = process.poll()
            if code is None:
                time.sleep(1.0)
                continue
            if self._stopping:
                return
            log.error("server exited (code %s). Last output:\n  %s", code,
                      "\n  ".join(list(self.tail)[-12:]))
            now = time.monotonic()
            while self._restarts and now - self._restarts[0] > _WINDOW:
                self._restarts.popleft()
            if len(self._restarts) >= len(_BACKOFF):
                self._set("failed")
                return
            wait = _BACKOFF[len(self._restarts)]
            self._restarts.append(now)
            self._set("restarting")
            time.sleep(wait)
            if self._stopping:
                return
            self._spawn()

    def wait_ready(self, timeout: float = 180.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.state in ("ready", "attached"):
                return True
            if self.state == "failed":
                return False
            time.sleep(0.5)
        return False

    def stop(self) -> None:
        """Release the GPU first, then end the process.

        /ops/unload stops llama-server and frees the speech models while the
        server can still do it tidily; terminating straight away would leave
        that to the job objects, which works but is abrupt.
        """
        self._stopping = True
        process = self._process
        if process is None or process.poll() is not None:
            return
        try:
            httpx.post(f"{self.url}/ops/unload", timeout=15.0)
        except httpx.HTTPError:
            pass
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        log.info("server stopped")
        self._set("stopped")

    def _set(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self._on_change(state)
