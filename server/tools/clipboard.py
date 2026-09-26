"""The clipboard: "summarise what I copied", "put that in my clipboard".

Read and written through the Win32 clipboard API directly, with ctypes: no
dependency, and a few milliseconds where PowerShell's Get-Clipboard takes a
third of a second to start.

What is in the clipboard came from anywhere (a web page, a message from a
stranger), so reading it taints the turn the way reading a page does.
Writing replaces what the person had copied, so it asks first once the turn
has been steered from outside.
"""

import ctypes
import sys
import time

from .registry import tool

MAX_CHARS = 6000
_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002

if sys.platform == "win32":
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # Handles are pointer-sized: without these, a 64-bit handle is cut to
    # 32 bits and the clipboard reads garbage or crashes.
    _user32.OpenClipboard.argtypes = (wintypes.HWND,)
    _user32.OpenClipboard.restype = wintypes.BOOL
    _user32.CloseClipboard.restype = wintypes.BOOL
    _user32.EmptyClipboard.restype = wintypes.BOOL
    _user32.GetClipboardData.argtypes = (wintypes.UINT,)
    _user32.GetClipboardData.restype = wintypes.HANDLE
    _user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
    _user32.SetClipboardData.restype = wintypes.HANDLE
    _user32.IsClipboardFormatAvailable.argtypes = (wintypes.UINT,)
    _user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    _kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
    _kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    _kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
    _kernel32.GlobalLock.restype = wintypes.LPVOID
    _kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
    _kernel32.GlobalFree.argtypes = (wintypes.HGLOBAL,)


def _open() -> None:
    # Another program may hold it for a moment (a clipboard manager, the
    # app that is copying); a few short retries cover that.
    for _ in range(10):
        if _user32.OpenClipboard(None):
            return
        time.sleep(0.03)
    raise RuntimeError("the clipboard is busy; try again in a moment")


def read() -> str:
    if sys.platform != "win32":
        raise RuntimeError("the clipboard needs Windows")
    _open()
    try:
        if not _user32.IsClipboardFormatAvailable(_CF_UNICODETEXT):
            return ""
        handle = _user32.GetClipboardData(_CF_UNICODETEXT)
        if not handle:
            return ""
        pointer = _kernel32.GlobalLock(handle)
        try:
            return ctypes.wstring_at(pointer) if pointer else ""
        finally:
            _kernel32.GlobalUnlock(handle)
    finally:
        _user32.CloseClipboard()


def write(text: str) -> None:
    if sys.platform != "win32":
        raise RuntimeError("the clipboard needs Windows")
    data = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(data)
    handle = _kernel32.GlobalAlloc(_GMEM_MOVEABLE, size)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    pointer = _kernel32.GlobalLock(handle)
    ctypes.memmove(pointer, data, size)
    _kernel32.GlobalUnlock(handle)
    _open()
    try:
        _user32.EmptyClipboard()
        if not _user32.SetClipboardData(_CF_UNICODETEXT, handle):
            _kernel32.GlobalFree(handle)
            raise ctypes.WinError(ctypes.get_last_error())
        # The clipboard owns the memory now; it must not be freed here.
    finally:
        _user32.CloseClipboard()


@tool(
    description=(
        "Read the text the user has copied to the clipboard. Use it when "
        "they say 'what I copied', 'my clipboard', 'this text I copied': to "
        "summarise, translate, explain or fix it."),
    parameters={},
    label="Read the clipboard",
    summary="The text you copied, to summarise, translate or fix.",
    # Copied from anywhere, a web page included.
    untrusted=True,
)
def read_clipboard():
    text = read()
    if not text.strip():
        return "The clipboard has no text in it."
    clipped = text[:MAX_CHARS]
    more = (f"\n(…cut: {len(text) - MAX_CHARS} more characters)"
            if len(text) > MAX_CHARS else "")
    return f"<<clipboard>>\n{clipped}{more}\n<</clipboard>>"


@tool(
    description=(
        "Put text in the user's clipboard, replacing what was there, so they "
        "can paste it: a fixed version of what they copied, a translation, "
        "an address. Saying it is in the clipboard does not put it there: "
        "this call is the only thing that does, so make it before saying "
        "so. Put the whole text in it rather than reading it out."),
    parameters={"text": {"type": "string",
                         "description": "Exactly what to put there."}},
    required=["text"],
    confirm=lambda arguments, tainted: tainted,
    label="Copy to the clipboard",
    summary="Puts text in your clipboard, ready to paste.",
)
def write_clipboard(text: str):
    write(text)
    return f"Copied {len(text)} characters to the clipboard."
