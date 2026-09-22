# PyInstaller spec for Naka.exe — run by packaging/build.py, not by hand.
#
# Onedir, not onefile: onefile unpacks itself into %TEMP% on every launch,
# which costs seconds at sign-in and looks exactly like what antivirus
# software is paid to distrust.
#
# Only the tray is frozen. The server runs under the runtime venv's Python
# from plain source files that build.py lays out beside Naka.exe, so nothing
# of torch, Whisper or Kokoro belongs in here — and the excludes make sure an
# accidental import cannot drag them in.

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent

datas, binaries, hiddenimports = [], [], []
# WebView2Loader.dll and pywebview's own JS are data files PyInstaller does
# not find by following imports.
for package in ("webview",):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += [
    # Each picks its platform backend by name at runtime.
    "pystray._win32",
    "pynput.keyboard._win32",
    "pynput.mouse._win32",
    # Imported inside functions, on the first run only.
    "setup.wizard",
    "setup.steps",
    "tomlkit",
]

a = Analysis(
    [str(ROOT / "packaging" / "naka.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=[
        "torch", "torchaudio", "ctranslate2", "faster_whisper", "kokoro",
        "av", "matplotlib", "tkinter", "spacy", "IPython",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

ICON = ROOT / "build" / "wizard" / "naka.ico"

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Naka",
    console=False,
    # Drawn by packaging/assets.py before the freeze; the window, the taskbar
    # and the installer all take theirs from here.
    icon=str(ICON) if ICON.exists() else None,
    # UPX-packed executables are another classic antivirus trigger, for a
    # few megabytes saved.
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="Naka")
