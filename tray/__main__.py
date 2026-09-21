"""Naka in the tray: the one thing a person starts.

It starts the server, holds the push-to-talk key for the whole machine, keeps
an icon in the notification area, and shows the panel in its own window when
asked. Closing the window leaves Naka running; Quit in the tray menu stops it.

    python -m tray            # from a checkout, window shown
    python -m tray --hidden   # what sign-in runs: tray icon only

Threading, which Windows is particular about: webview must own the main thread,
because it pumps the Win32 message loop, and so would pystray's icon. The
icon is therefore started with run_detached() from webview's start callback,
which is what pystray provides it for.
"""

import argparse
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

import httpx
import pystray
import webview
from PIL import Image, ImageDraw

from server import autostart, logsetup, paths, settings

from . import theme
from .supervisor import Server
from .voice import Voice

log = logging.getLogger("naka.tray")

# Colour is state, as in the panel: grey is resting, anything coloured is live.
_COLOURS = {
    "starting": (74, 81, 98), "restarting": (74, 81, 98),
    "ready": (124, 92, 255), "attached": (124, 92, 255),
    "listening": (255, 107, 107), "thinking": (240, 136, 62),
    "speaking": (77, 159, 255), "failed": (60, 60, 60), "stopped": (60, 60, 60),
}


def _icon_image(state: str) -> Image.Image:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((6, 6, 58, 58), fill=_COLOURS.get(state, (74, 81, 98)))
    if state == "failed":
        draw.ellipse((40, 40, 60, 60), fill=(255, 80, 80))
    return image


def _single_instance() -> bool:
    """True if we are the only Naka. A second one asks the first to show itself."""
    if sys.platform != "win32":
        return True
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # Held for the life of the process; Windows releases it when we exit.
    _single_instance.handle = kernel32.CreateMutexW(None, False, "Local\\NakaTray")
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hidden", action="store_true",
                        help="start in the tray without showing the window")
    args = parser.parse_args()

    logsetup.configure("tray.log")
    # A line per request otherwise, and the tray reports every state change.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    port = int(settings.SERVER.get("port", 8000))
    base = f"http://127.0.0.1:{port}"

    if not _single_instance():
        try:
            httpx.post(f"{base}/client/show", timeout=3.0)
        except httpx.HTTPError:
            pass
        return

    # Inherited by the server, so the panel can offer the sign-in toggle with
    # the right command in it, and used by the tray menu's own toggle.
    os.environ["NAKA_LAUNCH"] = autostart.default_command()

    # Before any menu exists: Windows decides a menu's theme when it is built.
    theme.dark_menus()

    quitting = threading.Event()
    icon: pystray.Icon | None = None
    window = webview.create_window(
        settings.IDENTITY.get("assistant", "Naka"),
        html="<body style='background:#07080b;color:#9aa1b2;font:14px sans-serif;"
             "display:grid;place-items:center;height:100vh;margin:0'>Starting…</body>",
        width=1280, height=820, min_size=(900, 600), hidden=args.hidden,
        # Painted before WebView2 has drawn anything, so opening the window
        # does not flash white first.
        background_color=theme.BACKGROUND,
    )

    def repaint(state: str) -> None:
        if icon is not None:
            icon.icon = _icon_image(state)
            icon.title = f"{settings.IDENTITY.get('assistant', 'Naka')} — {state}"

    server = Server(port, on_change=repaint)
    voice = Voice(base, on_state=repaint,
                  on_error=lambda message: icon and icon.notify(message, "Naka"))

    # --- window ---------------------------------------------------------

    def show(*_):
        window.show()
        window.restore()
        on_shown()

    def on_closing():
        # Closing the window is not quitting. The tray owns Naka's lifetime.
        if quitting.is_set():
            return True
        window.hide()
        return False

    window.events.closing += on_closing

    def on_shown():
        # The frame exists only once the window is shown. Recoloured so the
        # caption bar is the panel's own background rather than a white strip.
        hwnd = 0
        try:
            hwnd = int(window.native.Handle.ToInt64())
        except Exception:  # pywebview's native object differs by backend
            hwnd = theme.find_window(window.title)
        theme.style_window(hwnd)

    window.events.shown += on_shown

    # --- tray menu ------------------------------------------------------

    def post(path: str):
        def go(*_):
            threading.Thread(target=lambda: _quiet_post(f"{base}{path}"),
                             daemon=True).start()
        return go

    def toggle_autostart(*_):
        autostart.set_enabled(not autostart.enabled())

    def open_logs(*_):
        os.startfile(paths.LOGS)  # noqa: S606 — opens Explorer on our own folder

    def quit_app(*_):
        quitting.set()
        window.destroy()

    icon = pystray.Icon(
        "naka", _icon_image("starting"), "Naka — starting",
        menu=pystray.Menu(
            pystray.MenuItem("Open Naka", show, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda _: f"Talk: hold {voice.key}", None, enabled=False),
            pystray.MenuItem("Release the GPU", post("/ops/unload")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Start with Windows", toggle_autostart,
                             checked=lambda _: autostart.enabled()),
            pystray.MenuItem("Open logs folder", open_logs),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", quit_app),
        ),
    )

    # --- startup, off the main thread ------------------------------------

    def boot():
        icon.run_detached()
        server.start()
        if not server.wait_ready():
            icon.notify("Naka's server did not start. The log has the reason.",
                        "Naka")
            return
        window.load_url(f"{base}/ui/?embedded=1")
        voice.start()
        threading.Thread(target=listen, daemon=True, name="events").start()

    def listen():
        """Follow the server's event stream for what concerns the tray."""
        while not quitting.is_set():
            try:
                with httpx.stream("GET", f"{base}/events", timeout=None) as response:
                    for line in response.iter_lines():
                        if quitting.is_set():
                            return
                        if not line.startswith("data: "):
                            continue
                        event = json.loads(line[6:])
                        if event.get("topic") == "settings":
                            voice.refresh_config()
                        elif event.get("topic") == "client" and event.get("show"):
                            show()
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(2.0)

    webview.start(lambda: threading.Thread(target=boot, daemon=True).start(),
                  gui="edgechromium")

    # webview.start returns once the window is destroyed, which only Quit does.
    quitting.set()
    voice.stop()
    server.stop()
    icon.stop()


def _quiet_post(url: str) -> None:
    try:
        httpx.post(url, timeout=30.0)
    except httpx.HTTPError:
        pass


if __name__ == "__main__":
    main()
