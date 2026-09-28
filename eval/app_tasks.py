"""App and game scenarios for eval/agent_bench.py: the real model, a fake PC.

The index is a fixed list shaped like a real one (a French Windows, Steam and
Epic games, launchers), and launching is recorded rather than done, so the
bench opens nothing. Checks read what was launched, not only what she said.
"""

from bench_tasks import said_any

from server.tools import apps

CATALOG = [
    apps.App("Fortnite", "com.epicgames.launcher://apps/fn%3Aid%3AFortnite?action=launch&silent=true", "epic"),
    apps.App("HELLDIVERS™ 2", "steam://rungameid/553850", "steam"),
    apps.App("Apex Legends", "steam://rungameid/1172470", "steam"),
    apps.App("Hollow Knight: Silksong", "steam://rungameid/1030300", "steam"),
    apps.App("Spotify", "shell:AppsFolder\\C:\\Spotify.exe", "start"),
    apps.App("Steam", "shell:AppsFolder\\Steam.exe", "start"),
    apps.App("Discord", "shell:AppsFolder\\com.squirrel.Discord.Discord", "start"),
    apps.App("League of Legends", "shell:AppsFolder\\Riot.LoL", "start"),
    apps.App("Minecraft Launcher", "shell:AppsFolder\\Minecraft", "start"),
    apps.App("Epic Games Launcher", "shell:AppsFolder\\Epic", "start"),
    apps.App("Calculatrice", "shell:AppsFolder\\Calculator", "start"),
    apps.App("Bloc-notes", "shell:AppsFolder\\Notepad", "start"),
    apps.App("Microsoft Edge", "shell:AppsFolder\\Edge", "start"),
    apps.App("Microsoft To Do", "shell:AppsFolder\\Todo", "start"),
    apps.App("Photos", "shell:AppsFolder\\Photos", "start"),
]

launched: list[apps.App] = []
closed: list[int] = []
WINDOWS = [
    {"Id": 11, "ProcessName": "FortniteClient-Win64-Shipping", "MainWindowTitle": "Fortnite"},
    {"Id": 12, "ProcessName": "Spotify", "MainWindowTitle": "Daft Punk - Get Lucky"},
    {"Id": 13, "ProcessName": "Discord", "MainWindowTitle": "#general - Discord"},
]


def setup() -> None:
    launched.clear()
    closed.clear()
    apps._index[:] = CATALOG
    apps._built = 1e12  # never stale: the real Start menu is never read
    apps.launch = launched.append
    apps.open_windows = lambda: [dict(w) for w in WINDOWS]
    apps.close = closed.extend


def opened(name: str):
    return lambda r: [a.name for a in launched] == [name]


def nothing_opened(r) -> bool:
    return not launched


def both(*checks):
    return lambda r: all(c(r) for c in checks)


def task(ident, ask, check, reference="", origin="pc"):
    return {"id": ident, "kind": "apps", "ask": [ask], "check": check,
            "reference": reference, "origin": origin, "before": None}


def tasks() -> list[dict]:
    return [
        task("app-fortnite", "Open Fortnite.", opened("Fortnite")),
        task("app-fortnight", "Open fortnight.", opened("Fortnite"),
             "misheard Fortnite"),
        task("app-launch-game", "Launch Helldivers 2 please.", opened("HELLDIVERS™ 2")),
        task("app-lets-play", "I want to play Apex.", opened("Apex Legends")),
        task("app-silksong", "Start Silksong.", opened("Hollow Knight: Silksong")),
        task("app-spotify", "Open Spotify.", opened("Spotify"),
             "the app, not a song"),
        task("app-steam", "Can you open Steam?", opened("Steam")),
        task("app-discord", "Open discord for me.", opened("Discord")),
        task("app-league", "Open League.", opened("League of Legends")),
        task("app-minecraft", "Open Minecraft.", opened("Minecraft Launcher")),
        task("app-calculator", "Open the calculator.", opened("Calculatrice"),
             "French Windows"),
        task("app-notepad", "Open notepad.", opened("Bloc-notes")),
        task("app-missing", "Open Photoshop.",
             both(nothing_opened, said_any("not", "don't", "isn't", "can't",
                                           "couldn't", "no ")),
             "not installed: says so, opens nothing"),
        task("app-missing-word", "Open Word.",
             both(nothing_opened, said_any("not", "don't", "isn't", "can't",
                                           "couldn't", "no ")),
             "not installed; must not open To Do"),
        task("app-close", "Close Fortnite.", lambda r: closed == [11]),
        task("app-close-misheard", "close fort night please",
             lambda r: closed == [11], "misheard Fortnite"),
        task("app-close-spotify", "Quit Spotify.", lambda r: closed == [12],
             "its window is titled with the song"),
        task("app-list-games", "What games do I have installed?",
             said_any("fortnite", "helldivers", "apex")),
        task("tgin-app", "open fortnite on my pc",
             both(opened("Fortnite"), lambda r: r["confirmations"] == 0),
             "from Telegram: opens it, trusted like the PC", origin="telegram"),
    ]
