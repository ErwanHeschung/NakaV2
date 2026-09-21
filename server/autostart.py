"""Whether Naka starts when you sign in.

One registry value, HKCU\\...\\Run\\Naka, read and written from here by both the
panel and the tray menu, and set by the installer's checkbox. Deliberately not
a line in settings.toml: two places recording the same fact drift apart, and
Windows only ever reads the registry one.

What the value should contain is only known to whoever launched us, so the
tray passes it down as NAKA_LAUNCH. Run from a terminal, there is nothing
sensible to register, and the setting is simply not offered.
"""

import os
import sys

NAME = "Naka"
KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def default_command() -> str:
    """What sign-in should run: this app, tray only.

    Installed, that is Naka.exe. From a checkout it is naka.pyw under the
    venv's pythonw, so no console window appears at sign-in either.
    """
    from pathlib import Path

    from . import paths

    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --hidden'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pythonw}" "{paths.APP / "naka.pyw"}" --hidden'


def command() -> str | None:
    """The launch command, if we know it.

    The tray sets NAKA_LAUNCH for the server it starts; a server run by hand
    from a terminal has no business registering itself, so it gets None and
    the panel does not offer the setting.
    """
    return os.environ.get("NAKA_LAUNCH") if sys.platform == "win32" else None


def available() -> bool:
    return command() is not None


def enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as key:
            winreg.QueryValueEx(key, NAME)
        return True
    except FileNotFoundError:
        return False


def set_enabled(on: bool) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0,
                        winreg.KEY_SET_VALUE) as key:
        if on:
            launch = command()
            if not launch:
                raise RuntimeError("no launch command to register")
            winreg.SetValueEx(key, NAME, 0, winreg.REG_SZ, launch)
        else:
            try:
                winreg.DeleteValue(key, NAME)
            except FileNotFoundError:
                pass
