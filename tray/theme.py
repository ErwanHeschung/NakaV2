"""Make the Windows parts of Naka look like the rest of Naka.

Two pieces of chrome are drawn by Windows rather than by the panel: the
window's caption bar and the tray icon's right-click menu. Left alone, both
come out in the system's default light theme — a white strip above a near
black app, and a white menu popping out of it.

The window keeps its native frame and is recoloured, rather than made
frameless with a title bar drawn in HTML. A frameless window on Windows loses
edge resizing, Snap layouts and its drop shadow unless all three are
reimplemented by hand; recolouring keeps every one of them and still makes the
frame disappear into the content.

Colours are the panel's own tokens from ui/public/style.css.
"""

import ctypes
import logging
import sys

log = logging.getLogger("naka.tray.theme")

# From style.css :root — --void, --ink-dim and --line.
BACKGROUND = "#07080b"
TEXT = "#9aa1b2"
BORDER = "#222736"


def _colorref(hex_colour: str) -> ctypes.c_uint:
    """#rrggbb to a Win32 COLORREF, which is 0x00BBGGRR."""
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return ctypes.c_uint(r | (g << 8) | (b << 16))


def dark_menus() -> None:
    """Render this process's Win32 menus — the tray menu — in dark mode.

    Uses uxtheme's SetPreferredAppMode, exported only by ordinal. It is not in
    the public headers, but it is how Explorer, Notepad and every other
    first-party app get dark context menus, and it has been stable since
    Windows 10 1903. Must run before the first menu is created. Failing it is
    cosmetic, so failure is logged and ignored.
    """
    if sys.platform != "win32":
        return
    try:
        uxtheme = ctypes.WinDLL("uxtheme")
        set_preferred_app_mode = uxtheme[135]
        set_preferred_app_mode.argtypes = (ctypes.c_int,)
        flush_menu_themes = uxtheme[136]
        set_preferred_app_mode(2)  # ForceDark
        flush_menu_themes()
    except (OSError, AttributeError) as e:
        log.info("dark menus unavailable: %s", e)


def style_window(hwnd: int) -> None:
    """Dark frame, with the caption painted the panel's own background.

    Attribute 20 turns on the dark frame and dark caption buttons; 34, 35 and
    36 set the border, caption and title text colours. The last three are
    Windows 11 only; on 10 they fail quietly and the frame is merely dark.
    """
    if sys.platform != "win32" or not hwnd:
        return
    dwm = ctypes.WinDLL("dwmapi")
    handle = ctypes.c_void_p(hwnd)

    def set_attribute(attribute: int, value) -> None:
        dwm.DwmSetWindowAttribute(handle, attribute, ctypes.byref(value),
                                  ctypes.sizeof(value))

    set_attribute(20, ctypes.c_int(1))          # DWMWA_USE_IMMERSIVE_DARK_MODE
    set_attribute(34, _colorref(BORDER))        # DWMWA_BORDER_COLOR
    set_attribute(35, _colorref(BACKGROUND))    # DWMWA_CAPTION_COLOR
    set_attribute(36, _colorref(TEXT))          # DWMWA_TEXT_COLOR


def find_window(title: str) -> int:
    """The HWND of our top-level window, by title."""
    if sys.platform != "win32":
        return 0
    user32 = ctypes.WinDLL("user32")
    user32.FindWindowW.restype = ctypes.c_void_p
    return user32.FindWindowW(None, title) or 0
