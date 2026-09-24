"""Push to talk, from anywhere on the machine.

client/ptt.py's loop without the console: hold the key, speak, let go, hear
the reply as it is generated. The key is a global hook, so it works whatever
window is in front — a game, an editor, nothing at all.

The reply is asked for as raw PCM rather than Opus. The server already offers
it for the panel, and taking it here means the tray needs no audio decoder at
all: no PyAV, no ffmpeg in the frozen app.
"""

import logging
import threading
import time

import httpx
from pynput import keyboard

from client.audio import CAPTURE_RATE, PcmPlayer, open_output, record, resolve_key, to_wav

log = logging.getLogger("naka.tray.voice")

# Below this an utterance is a key bounce — or Ctrl+C with the talk key being
# right Ctrl — not speech.
MIN_SECONDS = 0.3


class Voice:
    def __init__(self, base_url: str, on_state=None, on_error=None):
        self.url = base_url
        self._on_state = on_state or (lambda state: None)
        self._on_error = on_error or (lambda message: None)
        self._held = threading.Event()
        self._hotkey = None
        self._key_code = "ControlRight"
        self._agentic = True
        self._stopped = False
        # One client for the session: keepalive long enough to outlast a pause
        # in the conversation, so replies do not each open a connection.
        self._http = httpx.Client(timeout=300.0, limits=httpx.Limits(
            max_keepalive_connections=4, keepalive_expiry=600.0))
        self._out = None
        self._listener = None
        # Barge-in: pressing the key while she is answering stops her. Set
        # while a reply is being fetched or played, and the means to end it.
        self._answering = threading.Event()
        self._cancel = threading.Event()
        self._response = None
        self._response_lock = threading.Lock()

    @property
    def key(self) -> str:
        return self._key_code

    def refresh_config(self) -> None:
        """Re-read the key and the tools setting. Called when settings change."""
        try:
            config = self._http.get(f"{self.url}/client", timeout=5.0).json()
        except (httpx.HTTPError, ValueError):
            return
        code = config.get("push_to_talk_key", "ControlRight")
        try:
            self._hotkey = resolve_key(code)
            self._key_code = code
        except ValueError as e:
            log.error("cannot bind %r: %s", code, e)
        self._agentic = bool(config.get("agentic", True))

    def start(self) -> None:
        self.refresh_config()
        if self._hotkey is None:
            self._hotkey = resolve_key(self._key_code)
        try:
            self._out = open_output()
        except Exception as e:  # PortAudio raises its own error types
            log.error("no audio output: %s", e)
            self._on_error("No speaker found — Naka can hear you but cannot answer aloud.")
        # One listener for the whole session. A second one started to watch for
        # the release would race a quick tap: the release could land between
        # the two and never be seen, recording forever.
        self._listener = keyboard.Listener(on_press=self._press,
                                           on_release=self._release)
        self._listener.start()
        threading.Thread(target=self._loop, daemon=True, name="voice").start()
        self._state("ready")

    def stop(self) -> None:
        self._stopped = True
        self._held.clear()
        if self._listener is not None:
            self._listener.stop()

    # ------------------------------------------------------------------

    def _press(self, key):
        # Compared against whatever the key is now, so a change in Settings
        # takes effect without restarting the listener.
        if key == self._hotkey:
            if not self._held.is_set() and self._answering.is_set():
                self._interrupt()
            self._held.set()

    def silence(self) -> None:
        """Stop the answer because the server said to, without telling it."""
        if self._answering.is_set():
            self._interrupt(tell_server=False)

    def _interrupt(self, tell_server: bool = True) -> None:
        """Stop the answer in progress: its sound, its request, its task.

        Called from the keyboard hook's thread while the voice thread is
        blocked reading the reply or writing audio. The flag stops the audio
        within a tenth of a second (the player writes in slices and checks
        it); closing the response unblocks a read that is waiting on her to
        think; and the server is told to stop the agent, which ends a command
        or a search mid-way.
        """
        log.info("interrupted by the talk key")
        self._cancel.set()
        with self._response_lock:
            response = self._response
        if response is not None:
            try:
                response.close()
            except Exception:  # noqa: BLE001  whatever state it was in
                pass
        if tell_server:
            threading.Thread(target=self._stop_agent, daemon=True).start()

    def _stop_agent(self) -> None:
        try:
            self._http.post(f"{self.url}/agent/stop", timeout=5.0)
        except httpx.HTTPError:
            pass

    def _release(self, key):
        if key == self._hotkey:
            self._held.clear()

    def _state(self, state: str) -> None:
        self._on_state(state)
        # Relayed so an open panel's orb follows the physical key. Fire and
        # forget: the panel being closed is not an error.
        threading.Thread(target=self._post_state, args=(state,), daemon=True).start()

    def _post_state(self, state: str) -> None:
        try:
            self._http.post(f"{self.url}/client/state", json={"state": state},
                            timeout=2.0)
        except httpx.HTTPError:
            pass

    def _loop(self) -> None:
        while not self._stopped:
            self._held.wait()
            if self._stopped:
                return
            # Woken the instant the key goes down, so a reload of the models
            # overlaps with the speaking instead of following it.
            threading.Thread(target=self._wake, daemon=True).start()
            self._state("listening")
            try:
                samples = record(self._held)
            except Exception as e:
                log.error("microphone failed: %s", e)
                self._on_error("Naka cannot open the microphone.")
                self._wait_release()
                self._state("ready")
                continue
            if len(samples) / CAPTURE_RATE < MIN_SECONDS:
                self._state("ready")
                continue
            self._state("thinking")
            self._cancel.clear()
            self._answering.set()
            try:
                self._converse(to_wav(samples))
            except Exception as e:
                if not self._cancel.is_set():
                    log.error("turn failed: %s", e)
            finally:
                self._answering.clear()
                with self._response_lock:
                    self._response = None
            self._state("ready")

    def _wait_release(self) -> None:
        while self._held.is_set() and not self._stopped:
            time.sleep(0.05)

    def _wake(self) -> None:
        try:
            self._http.post(f"{self.url}/ops/wake", timeout=5.0)
        except httpx.HTTPError:
            pass  # the request itself will load what is missing

    def _converse(self, wav: bytes) -> None:
        start = time.perf_counter()
        with self._http.stream(
            "POST", f"{self.url}/converse",
            files={"file": ("speech.wav", wav, "audio/wav")},
            data={"agentic": str(self._agentic).lower(), "format": "pcm"},
        ) as response:
            if response.status_code != 200:
                response.read()
                # 400 "no speech detected" is a normal outcome of a mumble.
                log.info("server said %s: %s", response.status_code,
                         response.text[:200])
                return
            with self._response_lock:
                self._response = response
            if self._cancel.is_set():
                return
            rate = int(response.headers.get("X-Sample-Rate", "24000"))
            player = (PcmPlayer(self._out, rate, self._cancel)
                      if self._out is not None else None)
            spoke = False
            for chunk in response.iter_bytes():
                if self._cancel.is_set():
                    return
                if player is None:
                    continue
                if player.feed(chunk) and not spoke:
                    spoke = True
                    self._state("speaking")
                    log.info("first audio %.0fms after release",
                             (time.perf_counter() - start) * 1000)
