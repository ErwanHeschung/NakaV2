"""Opening apps and games by name: "open Fortnite", "launch Spotify".

She never runs a path or a command she made up. What can be opened is an
index built from what Windows and the game launchers already list, and the
tool picks from it by name:

  * the Start menu, through Get-StartApps: every desktop and Store app,
    launched as shell:AppsFolder\\<AppID>;
  * Steam's library manifests, launched as steam://rungameid/<appid>;
  * Epic's install manifests, launched through the Epic launcher's own URI.

Names are matched loosely, because they arrive through speech recognition:
"fort night", "helldivers two" and "league" should all find their game. A
close call between two entries is handed back for her to ask about rather
than guessed.

Everything is started with cmd's `start`, which is ShellExecute: it knows
shell:AppsFolder paths and every launcher's URI. explorer.exe was tried
first and does not: given Epic's URI it opened a File Explorer window. The
server runs in a job object that kills its children when Naka quits, and a
game started as its child would die with it, so `start` runs in a process
created outside the job (winjob allows that breakaway, and only on request).
"""

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from .registry import tool

log = logging.getLogger("naka.apps")

# Seconds an index is trusted before a miss rebuilds it: something installed
# a minute ago should be openable without restarting her.
FRESH = 60

# Start menu entries that are not something anyone means by "open X".
_NOISE = re.compile(r"\b(uninstall|readme|read me|help|support center|"
                    r"release notes|documentation|manual|website|license|"
                    r"crash report|safe mode)\b", re.IGNORECASE)
# Steam installs these as "games"; nobody plays them.
_STEAM_SKIP = {"228980"}  # Steamworks Common Redistributables

# What people say, for what the entry may be called. Speech is recognised in
# English, while a French Windows names its own apps in French, so the
# built-in ones carry both.
ALIASES = {
    "lol": ["league of legends"],
    "league": ["league of legends"],
    "cod": ["call of duty"],
    "wow": ["world of warcraft"],
    "cs": ["counter strike 2", "counter strike"],
    "cs2": ["counter strike 2"],
    "vscode": ["visual studio code"],
    "vs code": ["visual studio code"],
    "word": ["word", "microsoft word"],
    "excel": ["excel", "microsoft excel"],
    "powerpoint": ["powerpoint", "microsoft powerpoint"],
    "chrome": ["google chrome"],
    "edge": ["microsoft edge"],
    "calculator": ["calculator", "calculatrice"],
    "notepad": ["notepad", "bloc notes"],
    "clock": ["clock", "horloge"],
    "alarm": ["clock", "horloge"],
    "settings": ["settings", "parametres"],
    "file explorer": ["file explorer", "explorateur de fichiers"],
    "explorer": ["file explorer", "explorateur de fichiers"],
    "files": ["file explorer", "explorateur de fichiers"],
    "camera": ["camera"],
    "photos": ["photos"],
    "paint": ["paint"],
    "terminal": ["terminal"],
    "command prompt": ["command prompt", "invite de commandes"],
    "cmd": ["command prompt", "invite de commandes"],
    "task manager": ["task manager", "gestionnaire des taches"],
    "control panel": ["control panel", "panneau de configuration"],
    "snipping tool": ["snipping tool", "outil capture d ecran"],
    "store": ["microsoft store"],
    "mail": ["outlook", "mail"],
    "weather": ["weather", "meteo"],
    "maps": ["maps", "cartes"],
}

_VENDORS = {"microsoft", "google", "adobe", "the", "launcher", "app", "games",
            "game"}

_NUMBERS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
            "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
            "ii": "2", "iii": "3", "iv": "4", "v": "5"}


@dataclass(frozen=True)
class App:
    name: str
    # What explorer.exe is given: a shell:AppsFolder path or a launcher URI.
    target: str
    source: str  # start | steam | epic

    @property
    def game(self) -> bool:
        return self.source in ("steam", "epic")


def normal(text: str) -> str:
    """Lowercase words, no accents or symbols, numbers as digits."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[®™©]", "", text)
    words = re.findall(r"[a-z0-9]+", text)
    return " ".join(_NUMBERS.get(w, w) for w in words)


def _compact(text: str) -> str:
    return normal(text).replace(" ", "")


# ------------------------------------------------------------------ sources


def _powershell(script: str) -> str:
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return result.stdout.decode("utf-8", errors="replace")


def _start_menu() -> list[App]:
    raw = _powershell("[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
                      "Get-StartApps | ConvertTo-Json -Compress")
    try:
        entries = json.loads(raw or "[]")
    except json.JSONDecodeError:
        log.warning("Get-StartApps returned something unreadable")
        return []
    if isinstance(entries, dict):
        entries = [entries]
    apps = []
    for entry in entries:
        name, ident = entry.get("Name") or "", entry.get("AppID") or ""
        if not name or not ident or _NOISE.search(name):
            continue
        if ident.startswith(("http://", "https://")):
            continue
        apps.append(App(name, f"shell:AppsFolder\\{ident}", "start"))
    return apps


def _registry_value(key: str, value: str) -> str | None:
    if sys.platform != "win32":
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            return str(winreg.QueryValueEx(handle, value)[0])
    except OSError:
        return None


def _acf_fields(text: str) -> dict[str, str]:
    return dict(re.findall(r'"(\w+)"\s+"([^"]*)"', text))


def _steam() -> list[App]:
    root = _registry_value(r"Software\Valve\Steam", "SteamPath")
    if not root:
        return []
    libraries = {Path(root)}
    folders = Path(root) / "steamapps" / "libraryfolders.vdf"
    if folders.exists():
        for path in re.findall(r'"path"\s+"([^"]+)"',
                               folders.read_text(encoding="utf-8", errors="replace")):
            libraries.add(Path(path.replace("\\\\", "\\")))
    apps = []
    for library in libraries:
        for manifest in (library / "steamapps").glob("appmanifest_*.acf"):
            fields = _acf_fields(manifest.read_text(encoding="utf-8",
                                                    errors="replace"))
            appid, name = fields.get("appid"), fields.get("name")
            if appid and name and appid not in _STEAM_SKIP:
                apps.append(App(name, f"steam://rungameid/{appid}", "steam"))
    return apps


def _epic() -> list[App]:
    folder = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / \
        "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    apps = []
    for manifest in folder.glob("*.item"):
        try:
            item = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        app_name = item.get("AppName", "")
        # Add-ons carry their game's name in MainGameAppName; only the game
        # itself is something to open.
        main_game = item.get("MainGameAppName") or app_name
        if (not app_name or item.get("bIsIncompleteInstall")
                or main_game != app_name):
            continue
        ident = "%3A".join((item.get("CatalogNamespace", ""),
                            item.get("CatalogItemId", ""), app_name))
        apps.append(App(item.get("DisplayName") or app_name,
                        f"com.epicgames.launcher://apps/{ident}"
                        "?action=launch&silent=true", "epic"))
    return apps


SOURCES = (_steam, _epic, _start_menu)

# ------------------------------------------------------------------- index

_index: list[App] = []
_built = 0.0
_lock = threading.Lock()


def build() -> list[App]:
    """Read every source again. Games first, so an exact game name wins a
    tie with a Start menu shortcut of the same name."""
    found: list[App] = []
    for source in SOURCES:
        try:
            found += source()
        except Exception:
            log.exception("could not read %s", source.__name__)
    seen, unique = set(), []
    for app in found:
        key = _compact(app.name)
        if key and key not in seen:
            seen.add(key)
            unique.append(app)
    global _index, _built
    with _lock:
        _index, _built = unique, time.time()
    log.info("indexed %d apps and games", len(unique))
    return unique


def index(max_age: float | None = None) -> list[App]:
    with _lock:
        stale = not _index or (max_age is not None
                               and time.time() - _built > max_age)
        current = list(_index)
    return build() if stale else current


def score(query: str, app: App) -> float:
    """How well a spoken name fits an entry, from 0 to 1."""
    said = normal(query)
    return max(_score(form, app) for form in [said, *ALIASES.get(said, [])])


def _score(said: str, app: App) -> float:
    name = normal(app.name)
    a, b = said.replace(" ", ""), name.replace(" ", "")
    if not a:
        return 0.0
    if a == b:
        return 1.0
    words = name.split()
    # "helldivers" for "helldivers 2", "minecraft" for "minecraft launcher":
    # the whole of what was said, at the start of the name.
    if b.startswith(a) and len(a) >= 4:
        return 0.9 - 0.02 * (len(words) - len(said.split()))
    # Every word said appears among the name's words.
    if set(said.split()) <= set(words):
        return 0.85
    # Said as two words where the name has one, or the reverse:
    # "silk song" is in "Hollow Knight: Silksong".
    if len(a) >= 5 and a in b:
        return 0.8
    # Every word said sounds like one of the name's: "hollow night" is
    # Hollow Knight, misheard.
    if len(said.split()) > 1 and all(
            max(SequenceMatcher(None, w, n).ratio() for n in words) >= 0.8
            for w in said.split()):
        return 0.8
    # The name is the start of what was said: "photoshop" is not "Photos",
    # it is something longer that is not installed.
    if a.startswith(b):
        return 0.5
    # Vendors' names are shared by half the Start menu, and made "word"
    # resemble "Microsoft To Do". Compared without them.
    a2 = "".join(w for w in said.split() if w not in _VENDORS) or a
    b2 = "".join(w for w in words if w not in _VENDORS) or b
    return SequenceMatcher(None, a2, b2).ratio() * 0.9


def find(query: str, apps: list[App] | None = None) -> list[tuple[float, App]]:
    ranked = sorted(((score(query, app), app) for app in apps or index()),
                    key=lambda pair: -pair[0])
    # Below this a match is a coincidence of letters: "chrome" and
    # "Horloge" share enough to score 0.55.
    return [pair for pair in ranked if pair[0] >= 0.7][:5]


_BREAKAWAY = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB
_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


def launch(app: App) -> None:
    if sys.platform != "win32":
        raise RuntimeError("opening apps needs Windows")
    if '"' in app.target:
        raise ValueError("that entry cannot be opened safely")
    # One string, quoted by hand: Epic's URI carries an &, which cmd would
    # otherwise read as the end of the command. The target comes from the
    # index, never from the model.
    command = f'cmd.exe /d /c start "" "{app.target}"'
    try:
        process = subprocess.Popen(command, creationflags=_BREAKAWAY | _NO_WINDOW,
                                   stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
    except PermissionError:
        # A job that does not allow breakaway: a server started by an older
        # tray. It still opens, but will close when Naka does.
        log.warning("could not start %s outside the job", app.name)
        process = subprocess.Popen(command, creationflags=_NO_WINDOW,
                                   stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
    try:
        code = process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        return
    if code != 0:
        raise RuntimeError(f"Windows could not open {app.name}")


def warm() -> None:
    """Build the index in the background, so the first request is instant."""
    threading.Thread(target=build, daemon=True, name="apps-index").start()


def _choose(name: str) -> tuple[App | None, list[App]]:
    matches = find(name, index(FRESH if not _index else None))
    if not matches or matches[0][0] < 0.75:
        # A miss may be something installed since: look once more.
        matches = find(name, index(FRESH))
    if not matches:
        return None, []
    best_score, best = matches[0]
    close = [app for s, app in matches[1:] if best_score - s < 0.05]
    # Alone and near enough is a mishearing of it: "fortnight" is Fortnite.
    if (best_score >= 0.75 and not close) or len(matches) == 1:
        return best, []
    return None, [app for _, app in matches[:4]]


@tool(
    description=(
        "Open an app or a game on this PC by its name: 'open Fortnite', "
        "'launch Spotify', 'start Steam', 'open Discord', 'I want to play "
        "Apex'. Call it straight away with the name as said: it finds the "
        "installed app even when misheard or named in another language, so "
        "do not look it up with list_apps first, and do not ask whether to "
        "go ahead. To play a particular song, use the Spotify tools instead "
        "when they are available."),
    parameters={"name": {"type": "string",
                         "description": "The app or game, as the user said "
                                        "it."}},
    required=["name"],
    # From the PC, opening a game is what was asked for. Steered by a web
    # page or sent from a phone, it asks first.
    confirm=lambda arguments, tainted: tainted,
    label="Open an app",
    summary="Starts an app or game that is installed, by name.",
)
def open_app(name: str):
    app, options = _choose(name)
    if app is not None:
        launch(app)
        kind = "game" if app.game else "app"
        return f"Opening the {kind} {app.name}."
    if options:
        return ("Several things could be meant: "
                + "; ".join(o.name for o in options)
                + ". Ask the user which one.")
    return f"Nothing called {name!r} is installed on this PC."


@tool(
    description="List the apps or games installed on this PC, for 'what "
                "games do I have'. Not needed before open_app.",
    parameters={
        "kind": {"type": "string", "enum": ["games", "apps", "all"],
                 "description": "Games only (Steam and Epic), apps, or "
                                "both. Default all."},
        "contains": {"type": "string",
                     "description": "Optional: only names like this."},
    },
    label="List apps and games",
    summary="What is installed that she can open.",
)
def list_apps(kind: str = "all", contains: str = ""):
    apps = index()
    if contains:
        # Matched the way open_app matches, and across both kinds: League of
        # Legends comes from the Start menu, not a game library, and
        # "calculator" is called Calculatrice on a French Windows.
        apps = [a for _, a in find(contains, apps)]
    elif kind == "games":
        apps = [a for a in apps if a.game]
    elif kind == "apps":
        apps = [a for a in apps if not a.game]
    if not apps:
        return "Nothing installed matches that."
    names = sorted(a.name for a in apps)
    shown = names[:40]
    more = f" and {len(names) - 40} more" if len(names) > 40 else ""
    return f"{len(names)} found: " + ", ".join(shown) + more + "."
