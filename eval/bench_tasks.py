"""The scenarios eval/agent_bench.py plays, and what counts as getting them right.

Web answers were established by Claude with its own web search on
2026-09-24; each carries that reference answer so a run can be read side by
side with it. Anything time-sensitive in here goes stale: re-check the
references before trusting an old run's web score.

Shell tasks run against a fixture folder rebuilt before every task, so each is
independent and its right answer is computed from the fixture, not written by
hand. A few read the real home folder, never writing to it.
"""

import csv
import hashlib
import json
import os
import platform
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

HOME = Path.home()
CODING = HOME / "Documents" / "Coding"


# ------------------------------------------------------------------ checks

def text_of(r) -> str:
    return r["said"].lower()


def said_any(*words):
    return lambda r: any(w.lower() in text_of(r) for w in words)


def said_all(*words):
    return lambda r: all(w.lower() in text_of(r) for w in words)


def said_number(value: int | float, tolerance: float = 0.0):
    """A number in the answer within tolerance (a fraction) of value."""
    import re

    def check(r):
        for raw in re.findall(r"\d[\d,]*(?:\.\d+)?", r["said"]):
            try:
                n = float(raw.replace(",", ""))
            except ValueError:
                continue
            if abs(n - value) <= max(abs(value) * tolerance, 0.0 if tolerance == 0 else 0.5):
                return True
        if tolerance:
            return False
        words = {0: "zero", 1: "one", 2: "two", 3: "three", 4: "four",
                 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine",
                 10: "ten", 11: "eleven", 12: "twelve"}
        said = text_of(r)
        if value == 0 and re.search(r"\b(empty|nothing|none|no files)\b", said):
            return True
        # Word boundaries, not split(): "just two." is a correct answer.
        return bool(re.search(rf"\b{words.get(value, '@')}\b", said))
    return check


def both(*checks):
    return lambda r: all(c(r) for c in checks)


def used(*tools):
    return lambda r: any(c["name"] in tools for c in r["calls"])


def searched_for(text):
    return lambda r: any(text in json.dumps(c["arguments"])
                         for c in r["calls"] if c["name"] == "web_search")


# --------------------------------------------------------------------- web

def web(tid, ask, check, reference, search=True):
    return {"id": f"web-{tid}", "kind": "web", "ask": [ask],
            "check": both(used("web_search", "fetch_page"), check)
            if search else check,
            "reference": reference}


WEB = [
    # Recent and changing: she has to look these up.
    web("python", "What's the latest stable version of Python?",
        said_any("3.14.7"), "Python 3.14.7, released 5 August 2026."),
    web("python315", "Is Python 3.15 out yet?",
        said_any("october", "candidate", "rc", "not yet", "isn't out", "not out"),
        "Not yet: 3.15.0rc2 shipped 1 September; the final is due in October."),
    web("silksong", "When did Hollow Knight Silksong come out?",
        said_any("2025"), "4 September 2025."),
    web("silksong-dlc", "Is there a Silksong expansion coming?",
        said_any("sea of sorrow"), "Sea of Sorrow, a free expansion, due in 2026 with no exact date."),
    web("silksong-dlc2", "When does the new Silksong DLC come out?",
        said_any("2026"), "Sea of Sorrow: 2026, no exact date yet."),
    web("f1-last", "Who won the last Formula 1 race?",
        said_any("antonelli"), "Kimi Antonelli, Spanish GP, 13 September 2026."),
    web("f1-leader", "Who's leading the Formula 1 championship?",
        said_any("antonelli"), "Kimi Antonelli, 292 points, 81 ahead of Russell."),
    web("f1-leader2", "Who's winning the F1 season right now?",
        said_any("antonelli"), "Kimi Antonelli."),
    web("f1-next", "Where is the next Formula 1 race?",
        said_any("azerbaijan", "baku"), "Azerbaijan GP in Baku, Saturday 26 September."),
    web("f1-next-date", "When is the next F1 race?",
        said_any("26", "saturday", "this weekend"), "Saturday 26 September, Baku."),
    web("f1-italy", "Who won the Italian Grand Prix this year?",
        said_any("antonelli"), "Kimi Antonelli, 6 September 2026."),
    web("rezero-next", "When is the next Re:Zero episode coming out?",
        said_any("30", "wednesday", "next week"),
        "Wednesday 30 September 2026, the season finale (episode 19)."),
    web("rezero-end", "When does this season of Re:Zero end?",
        said_any("30", "end of september"), "30 September 2026."),
    web("rezero-latest", "When did the latest Re:Zero episode come out?",
        said_any("23", "yesterday", "wednesday"),
        "Wednesday 23 September 2026 (episode 18)."),
    web("worldcup", "Who won the 2026 World Cup?",
        said_any("spain"), "Spain, 1-0 over Argentina after extra time, 19 July 2026."),
    web("worldcup-final", "Who did the World Cup winners beat in the final?",
        said_any("argentina"), "Argentina."),
    web("worldcup-goal", "Who scored the winning goal in the World Cup final?",
        said_any("ferran", "torres"), "Ferran Torres, 106th minute."),
    web("usopen-men", "Who won the US Open men's final this year?",
        said_any("zverev"), "Alexander Zverev, over Ben Shelton."),
    web("usopen-men2", "Who lost the US Open men's final?",
        said_any("shelton"), "Ben Shelton."),
    web("usopen-women", "Who won the women's US Open this year?",
        said_any("rybakina"), "Elena Rybakina, over Aryna Sabalenka."),
    web("tdf", "Who won the Tour de France this year?",
        said_any("pogacar", "pogačar"), "Tadej Pogačar, his fifth."),
    web("tdf-count", "How many times has Pogacar won the Tour de France?",
        said_any("five", "5"), "Five (2020, 2021, 2024, 2025, 2026)."),
    web("gta6", "When does GTA 6 come out?",
        said_all("november", "19"), "19 November 2026, PS5 and Xbox Series."),
    web("gta6-out", "Is GTA 6 out yet?",
        said_any("november"), "No, it comes out 19 November 2026."),
    web("claude", "What's the newest Claude model?",
        said_any("opus 5.5"), "Claude Opus 5.5, released 22 September 2026."),
    web("ucl", "Who won the Champions League this year?",
        said_any("psg", "paris"), "Paris Saint-Germain, on penalties against Arsenal."),
    web("ucl-final", "Who lost the Champions League final this year?",
        said_any("arsenal"), "Arsenal."),
    web("nba", "Who won the NBA championship this year?",
        said_any("knicks"), "New York Knicks, 4-1 over the Spurs."),
    web("nba-mvp", "Who was the NBA Finals MVP this year?",
        said_any("brunson"), "Jalen Brunson."),
    web("superbowl", "Who won the last Super Bowl?",
        said_any("seahawks", "seattle"), "Seattle Seahawks, 29-13 over the Patriots."),
    web("superbowl-score", "What was the score of the last Super Bowl?",
        said_all("29", "13"), "29-13."),
    web("oscars", "What won best picture at the Oscars this year?",
        said_any("one battle after another"), "One Battle After Another."),
    web("eurovision", "Who won Eurovision this year?",
        said_any("bulgaria", "dara"), "Bulgaria, Dara with Bangaranga."),
    web("eurovision-song", "What was the song that won Eurovision 2026?",
        said_any("bangaranga"), "Bangaranga."),
    web("uk-pm", "Who is the prime minister of the UK?",
        said_any("burnham"), "Andy Burnham, since 20 July 2026."),
    web("uk-pm2", "Who's running Britain right now?",
        said_any("burnham"), "Andy Burnham."),
    web("fr-pm", "Who is the French prime minister?",
        said_any("lecornu"), "Sébastien Lecornu."),
    web("olympics", "Which country topped the medal table at the last Winter Olympics?",
        said_any("norway"), "Norway, 18 golds and 41 medals."),
    web("switch2", "How much does a Nintendo Switch 2 cost now?",
        said_any("499"), "$499.99 in the US since 1 September 2026."),
    web("iphone", "What's the newest iPhone?",
        said_any("18"), "iPhone 18 Pro and Pro Max, out 18 September 2026."),
    web("iphone-price", "How much is the iPhone 18 Pro?",
        said_any("1,199", "1199", "1 199", "1,200", "1200"),
        "From $1,199 (\"about $1,200\" spoken is right too)."),
    web("node", "What's the current LTS version of Node.js?",
        said_any("24"), "Node 24 (Active LTS); Node 26 becomes LTS in October."),
    web("rust", "What's the latest version of Rust?",
        said_any("1.98"), "Rust 1.98.1, 3 September 2026."),
    web("android", "What's the latest version of Android?",
        said_any("17"), "Android 17, 16 June 2026."),
    web("ubuntu", "What's the latest Ubuntu LTS?",
        said_any("26.04"), "Ubuntu 26.04 LTS."),
    web("boxoffice", "What's the biggest movie of the year at the box office?",
        said_any("spider"),
        "Spider-Man: Brand New Day, past $2B worldwide. (First written here as "
        "The Odyssey, from a search that ranked Universal's films only; "
        "corrected after Naka answered Spider-Man and a second search agreed.)"),
    web("ballondor", "When is the Ballon d'Or ceremony this year?",
        said_all("october", "26"), "Monday 26 October 2026, London Palladium."),
    web("ballondor-where", "Where is the Ballon d'Or ceremony this year?",
        said_any("london"), "London."),
    web("fps-year", "What are some new multiplayer FPS games that came out this year?",
        searched_for("2026"), "Searches with 2026 in the query."),
    web("games-year", "What are the best games released this year?",
        searched_for("2026"), "Searches with 2026 in the query."),
    web("anime-season", "What are the popular anime airing this season?",
        searched_for("2026"), "Searches for the current (fall 2026) season."),
    web("news-ai", "What's the latest news about AI?",
        searched_for("2026"), "Searches with the current date."),
    web("mario-movie", "When did the Super Mario Galaxy Movie come out?",
        said_any("april"), "1 April 2026."),
    web("pogacar-margin", "Who came second in the Tour de France this year?",
        said_any("evenepoel"), "Remco Evenepoel."),
    web("rybakina-final", "Who did Rybakina beat in the US Open final?",
        said_any("sabalenka"), "Aryna Sabalenka."),
    web("zverev-slams", "How many Grand Slams has Zverev won now?",
        said_any("two", "2"), "Two: French Open and US Open 2026."),
    web("f1-second", "Who is second in the F1 championship?",
        said_any("russell"), "George Russell."),
    web("switch2-old", "How much did the Switch 2 cost at launch?",
        said_any("449"), "$449.99."),
    web("node26", "When does Node 26 become LTS?",
        said_any("october"), "October 2026."),
    web("rust-next", "When is the next Rust release?",
        said_any("october", "1.99"), "1.99.0 on 1 October 2026."),
    # Stable facts: a lookup is allowed but not required.
    *[web(tid, q, said_any(*a), ref, search=False) for tid, q, a, ref in [
        ("titanic", "Who directed Titanic?", ["cameron"], "James Cameron."),
        ("starry", "Who painted The Starry Night?", ["van gogh"], "Vincent van Gogh."),
        ("dune", "Who wrote Dune?", ["herbert"], "Frank Herbert."),
        ("moon", "Who was the first person to walk on the moon?", ["armstrong"], "Neil Armstrong."),
        ("minecraft", "What year did Minecraft come out?", ["2011"], "2011 (full release)."),
        ("hollow", "When did the first Hollow Knight come out?", ["2017"], "February 2017."),
        ("teamcherry", "Where is Team Cherry based?", ["adelaide"], "Adelaide, Australia."),
        ("python-creator", "Who created Python?", ["guido"], "Guido van Rossum."),
        ("linux", "Who created Linux?", ["torvalds"], "Linus Torvalds."),
        ("git", "Who created Git?", ["torvalds"], "Linus Torvalds."),
        ("win95", "What year did Windows 95 come out?", ["1995"], "1995."),
        ("kilimanjaro", "What's the highest mountain in Africa?", ["kilimanjaro"], "Kilimanjaro."),
        ("jupiter", "What's the biggest planet in the solar system?", ["jupiter"], "Jupiter."),
        ("monalisa", "Which museum has the Mona Lisa?", ["louvre"], "The Louvre."),
        ("gold", "What's the chemical symbol for gold?", ["au"], "Au."),
        ("bones", "How many bones are in the adult human body?", ["206"], "206."),
        ("yen", "What's the currency of Japan?", ["yen"], "The yen."),
        ("spirited", "Who directed Spirited Away?", ["miyazaki"], "Hayao Miyazaki."),
        ("rezero-author", "Who wrote Re:Zero?", ["nagatsuki"], "Tappei Nagatsuki."),
        ("eldenring", "Who made Elden Ring?", ["fromsoftware", "from software"], "FromSoftware."),
        ("botw", "What year did Zelda Breath of the Wild come out?", ["2017"], "2017."),
        ("telephone", "Who invented the telephone?", ["bell"], "Alexander Graham Bell."),
        ("ottawa", "What's the capital of Canada?", ["ottawa"], "Ottawa."),
        ("canberra", "What's the capital of Australia?", ["canberra"], "Canberra."),
        ("mariana", "What's the deepest point in the ocean?", ["mariana", "challenger"], "Challenger Deep, Mariana Trench."),
        ("blackwell", "Which company makes Blackwell GPUs?", ["nvidia"], "Nvidia."),
        ("5070ti", "How much VRAM does an RTX 5070 Ti have?", ["16"], "16 GB."),
        ("gemma", "Who makes the Gemma models?", ["google"], "Google."),
        ("llama", "Who makes the Llama models?", ["meta"], "Meta."),
        ("eiffel", "How tall is the Eiffel Tower?", ["330", "324"], "About 330 m."),
        ("light", "What's the speed of light?", ["299", "300,000", "300 000", "186"], "299,792 km/s."),
        ("boil-f", "What's the boiling point of water in Fahrenheit?", ["212"], "212 °F."),
        ("hunger", "Who wrote The Hunger Games?", ["collins"], "Suzanne Collins."),
        ("interstellar", "Who composed the music for Interstellar?", ["zimmer"], "Hans Zimmer."),
        ("hornet", "Who is the main character of Silksong?", ["hornet"], "Hornet."),
        ("html", "What does HTML stand for?", ["hypertext markup language"], "HyperText Markup Language."),
        ("everest", "How tall is Mount Everest?", ["8,849", "8849", "8,848", "8848", "29,0"], "8,849 m."),
        ("pacific", "What's the largest ocean?", ["pacific"], "The Pacific."),
        ("water", "What's the chemical formula for water?", ["h2o"], "H2O."),
        ("tolkien", "Who wrote The Lord of the Rings?", ["tolkien"], "J. R. R. Tolkien."),
        ("miyamoto", "Who created Mario?", ["miyamoto"], "Shigeru Miyamoto."),
        ("ps5", "What year did the PlayStation 5 come out?", ["2020"], "2020."),
        ("switch1", "What year did the first Nintendo Switch come out?", ["2017"], "2017."),
        ("js", "Who created JavaScript?", ["eich"], "Brendan Eich."),
        ("crunchyroll", "Who owns Crunchyroll?", ["sony"], "Sony."),
        ("aot", "Who wrote Attack on Titan?", ["isayama"], "Hajime Isayama."),
        ("onepiece", "Who wrote One Piece?", ["oda"], "Eiichiro Oda."),
        ("valve", "Which company made Half-Life?", ["valve"], "Valve."),
        ("doom", "What year did the original Doom come out?", ["1993"], "1993."),
        ("pokemon", "What year did the first Pokemon games come out?", ["1996"], "1996."),
    ]],
]


# ------------------------------------------------------------------- shell

def build(root: Path) -> None:
    """The fixture: a small, known tree. Rebuilt before every task."""
    def writable(func, path, _):
        # A task that made a file read-only would otherwise stop the
        # rebuild for every task after it.
        os.chmod(path, 0o666)
        func(path)
    if root.exists():
        shutil.rmtree(root, onexc=writable)
    files = {
        "Alpha/readme.txt": "alpha project\n",
        "Alpha/big.log": "x" * 50_000,
        "Alpha/sub/notes.md": "# notes\n",
        "report.txt": "quarterly report\n",
        "todo.txt": "buy stamps\nfix bike\nemail Paul\n",
        "names.txt": "Zoe\nadam\nMilo\nzoe\nBea\nMilo\nCarl\nadam\nDina\n",
        "Photos/img1.jpg": b"\xff\xd8" + b"1" * 30_000,
        "Photos/img2.jpg": b"\xff\xd8" + b"2" * 20_000,
        "Photos/img3.jpg": b"\xff\xd8" + b"3" * 10_000,
        "Photos/holiday.png": b"\x89PNG" + b"4" * 40_000,
        "Photos/logo.png": b"\x89PNG" + b"5" * 5_000,
        "Music/song1.mp3": b"ID3" + b"a" * 150_000,
        "Music/song2.mp3": b"ID3" + b"b" * 90_000,
        "Music/video.mp4": b"\x00" * 300_000,
        "Papers/invoice_01.pdf": b"%PDF" + b"0" * 2_000,
        "Papers/invoice_02.pdf": b"%PDF" + b"0" * 2_500,
        "Papers/cv.docx": b"PK" + b"0" * 8_000,
        "Papers/letter.txt": "Dear Sam,\nThanks for the parcel. It arrived on Monday "
                           "and the kids loved it.\nSee you soon,\nErwan\n",
        "Code/app.py": "import util\n\n# TODO: handle errors\ndef main():\n"
                       "    # TODO: add logging\n    print(util.greet('Naka'))\n\n"
                       "# TODO: tests\nif __name__ == '__main__':\n    main()\n",
        "Code/util.py": "def greet(name):\n    return f'Hello {name}'\n",
        "Code/test_app.py": "def test_ok():\n    assert True\n",
        "Code/notes.md": "# Bench project\n\nA tiny project for testing.\n",
        "Code/data.json": json.dumps({"name": "Naka", "version": 1, "tags": ["a", "b"]}),
        "Code/data.csv": "name,age,city\nAna,31,Lyon\nBen,25,Nice\nCeline,40,Paris\n"
                         "Dan,19,Lille\nEve,52,Nantes\n",
        "Logs/app.log": "".join(
            f"2026-09-2{i % 3} 10:{i:02d} {lvl} message {i}\n"
            for i, lvl in enumerate(["INFO", "ERROR", "INFO", "WARN", "INFO",
                                     "ERROR", "INFO", "INFO", "WARN", "ERROR",
                                     "INFO", "ERROR"])) + "2026-09-23 11:00 INFO shutdown complete\n",
        "Logs/old.log": "ancient\n",
        "Logs/a.tmp": "t", "Logs/b.tmp": "t", "Logs/c.tmp": "t",
        "Projects/ProjA/main.py": "print('a')\n",
        "Projects/ProjA/README.md": "# ProjA\n",
        "Projects/ProjB/src/deep/secret_plan.txt": "world domination\n",
        "Projects/ProjB/src/index.js": "console.log('b')\n",
        "Projects/ProjC/data.txt": "c\n",
        "Notes2/one.txt": "1", "Notes2/two.txt": "2", "Notes2/three.txt": "3",
        "Inbox/scan.pdf": b"%PDF", "Inbox/photo.jpg": b"\xff\xd8",
        "Inbox/song.mp3": b"ID3", "Inbox/memo.txt": "memo",
        "Inbox/other.pdf": b"%PDF",
    }
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    (root / "EmptyDir").mkdir()
    with zipfile.ZipFile(root / "archive.zip", "w") as z:
        z.writestr("inside/hello.txt", "hello from the zip\n")
        z.writestr("inside/readme.md", "# zipped\n")
    # Known ages: newest and oldest questions have one right answer.
    now = time.time()
    for rel, age_days in [("Papers/invoice_01.pdf", 30), ("Papers/invoice_02.pdf", 3),
                          ("Papers/cv.docx", 90), ("Papers/letter.txt", 10),
                          ("Logs/old.log", 400), ("Logs/app.log", 1),
                          ("report.txt", 20), ("todo.txt", 2)]:
        t = now - age_days * 86400
        os.utime(root / rel, (t, t))


def read(path: Path) -> str:
    """A file's text in whatever encoding it was written in.

    Windows PowerShell's Out-File and > write UTF-16 with a byte order mark;
    a file sorted correctly and read as UTF-8 looked like noise.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-8-sig", errors="replace")


def lines(path: Path) -> list[str]:
    return [l for l in read(path).splitlines() if l.strip()]


def shell_tasks(root: Path) -> list[dict]:
    R = root
    T = []

    # Tasks that only ask a question: their check reads what she said and
    # nothing on disk, so bench_report can re-judge them after the fact.
    offline = True

    def task(tid, ask, check, reference):
        T.append({"id": f"sh-{tid}", "kind": "shell", "ask": [ask],
                  "check": check, "reference": reference, "offline": offline})

    W = "in my workspace"
    app_log = read(R / "Logs/app.log") if (R / "Logs/app.log").exists() else ""
    names = [n.strip() for n in "Zoe adam Milo zoe Bea Milo Carl adam Dina".split()]

    # ---- looking --------------------------------------------------------
    task("list-alpha", f"What's in the Alpha folder {W}?",
         said_all("readme", "big", "sub"), "readme.txt, big.log and a sub folder.")
    task("count-photos", f"How many files are in the Photos folder {W}?",
         said_number(5), "5.")
    task("count-jpg", f"How many JPG pictures are in my Photos folder {W}?",
         said_number(3), "3.")
    task("count-png", f"How many PNG files are in Photos {W}?",
         said_number(2), "2.")
    task("count-docs", f"How many files are in the Papers folder {W}?",
         said_number(4), "4.")
    task("read-letter", f"Who is the letter in my Papers folder addressed to? It's {W}.",
         said_any("sam"), "Sam.")
    task("letter-day", f"Read letter.txt in Papers {W}. What day did the parcel arrive?",
         said_any("monday"), "Monday.")
    task("todo-first", f"What's the first thing on my todo list? It's todo.txt {W}.",
         said_any("stamps"), "Buy stamps.")
    task("todo-count", f"How many items are on my todo list, todo.txt {W}?",
         said_number(3), "3.")
    task("names-lines", f"How many lines are in names.txt {W}?",
         said_number(9), "9.")
    task("names-unique", f"How many different names are in names.txt {W}, ignoring upper and lower case?",
         said_number(6), "6: Zoe, Adam, Milo, Bea, Carl, Dina.")
    task("log-errors", f"How many ERROR lines are in Logs/app.log {W}?",
         said_number(4), "4.")
    task("log-warn", f"How many warnings are in the app log in the Logs folder {W}?",
         said_number(2), "2.")
    task("log-last", f"What's the last line of app.log in the Logs folder {W}?",
         said_any("shutdown"), "…INFO shutdown complete.")
    task("biggest-alpha", f"What's the biggest file in the Alpha folder {W}?",
         said_any("big.log", "big log", "big dot log"), "big.log, 50 KB.")
    task("biggest-all", f"What's the biggest file anywhere {W}?",
         said_any("video"), "Music/video.mp4, 300 KB.")
    task("newest-docs", f"Which file in Papers {W} was changed most recently?",
         said_any("invoice_02", "invoice 02", "invoice_2", "invoice 2", "second invoice"),
         "invoice_02.pdf (3 days ago).")
    task("oldest-log", f"Which file in the Logs folder {W} is the oldest?",
         said_any("old.log", "old log", "old dot log"), "old.log.")
    task("find-secret", f"Somewhere {W} there's a file called secret_plan.txt. Where is it?",
         said_any("projb", "proj b", "project b"), "Projects/ProjB/src/deep.")
    task("find-secret-content", f"Find secret_plan.txt {W} and tell me what it says.",
         said_any("domination"), "world domination.")
    task("projects", f"What project folders are in Projects {W}?",
         both(said_any("proja", "proj a", "project a"),
              said_any("projc", "proj c", "project c")),
         "ProjA, ProjB, ProjC.")
    task("todo-grep", f"Which Python files in the Code folder {W} have TODO comments?",
         said_any("app.py", "app dot py", "app"), "app.py only.")
    task("todo-count-py", f"How many TODOs are in app.py in the Code folder {W}?",
         said_number(3), "3.")
    task("json-version", f"What version number is in data.json in the Code folder {W}?",
         said_number(1), "1.")
    task("json-name", f"What's the name field in Code/data.json {W}?",
         said_any("naka"), "Naka.")
    task("csv-rows", f"How many people are listed in data.csv in the Code folder {W}? Don't count the header.",
         said_number(5), "5.")
    task("csv-oldest", f"In Code/data.csv {W}, who is the oldest person?",
         said_any("eve"), "Eve, 52.")
    task("csv-paris", f"In data.csv in my Code folder {W}, who lives in Paris?",
         said_any("celine", "céline"), "Celine.")
    task("photos-size", f"How big is the Photos folder {W}, in kilobytes?",
         said_number(105_000 / 1024, 0.2), "About 103 KB (105,010 bytes).")
    task("doc-types", f"What kinds of files are in the Papers folder {W}?",
         both(said_any("pdf"), said_any("docx", "word")), "PDF, DOCX and TXT.")
    task("where-cv", f"Where is my CV {W}? It's a docx.",
         said_any("papers"), "Papers/cv.docx.")
    task("sub-projects", f"How many folders are directly inside Projects {W}?",
         said_number(3), "3.")
    task("count-projects", f"How many files are in the Projects folder {W}, including subfolders?",
         said_number(5), "5.")
    task("largest-code", f"Which file in the Code folder {W} is the largest?",
         said_any("app.py", "app dot py", "app"), "app.py.")
    task("letter-words", f"How many words are in Papers/letter.txt {W}?",
         said_number(len(read(R / "Papers/letter.txt").split()) if (R / "Papers/letter.txt").exists() else 22, 0.1),
         "22 words.")
    task("notes-title", f"What's the title in Code/notes.md {W}?",
         said_any("bench project"), "Bench project.")
    task("count-md", f"How many markdown files are there {W}, in all the folders?",
         said_number(3), "3: Alpha/sub/notes.md, Code/notes.md, Projects/ProjA/README.md.")
    task("newer", f"Which is newer {W}, report.txt or todo.txt?",
         said_any("todo"), "todo.txt (2 days vs 20).")
    task("music-big", f"How many files in the Music folder {W} are bigger than 100 KB?",
         said_number(2), "2: song1.mp3 and video.mp4.")
    task("zip-contents", f"What's inside archive.zip {W}? Don't extract it.",
         said_any("hello"), "inside/hello.txt and inside/readme.md.")
    task("hash", f"What are the first 8 characters of the SHA-256 hash of report.txt {W}?",
         said_any(hashlib.sha256(b"quarterly report\n").hexdigest()[:8],
                  hashlib.sha256(b"quarterly report\r\n").hexdigest()[:8]),
         hashlib.sha256(b"quarterly report\n").hexdigest()[:8] + ".")
    task("logs-count", f"If I deleted the Logs folder {W}, how many files would go?",
         said_number(5), "5.")
    task("empty-dir", f"Is there an empty folder {W}?",
         said_any("emptydir", "empty dir", "emptydir"), "EmptyDir.")
    # The real machine, read only.
    task("coding", "What's in my Coding folder? It's in my Documents.",
         both(said_any("nakav2", "naka v2", "naka"), said_any("vortraum")),
         "blockthespot, naka-freezetest, NakaV2, VorTraum.")
    task("find-nakav2", "Find my NakaV2 project folder for me.",
         said_any("coding"), f"{CODING / 'NakaV2'}.")
    task("count-py-real", "How many Python files are in my NakaV2 project in the Coding "
         "folder in Documents, not counting the .venv and build folders?",
         # Counted when checked: the project gains files during a long run.
         lambda r: said_number(_py_count())(r), "Counted at check time.")
    task("disk", "How much free space is left on my C drive?",
         said_number(_free_gb(), 0.03), f"{_free_gb():.0f} GB.")
    task("hostname", "What's this computer's name?",
         said_any(platform.node().lower()), platform.node() + ".")
    task("username", "What's my Windows user name?",
         said_any(os.environ.get("USERNAME", "erwan").lower()), os.environ.get("USERNAME", "") + ".")
    task("winver", "Which version of Windows am I running?",
         said_any(_windows()), f"Windows {_windows()}.")
    task("ram", "How much RAM does this PC have?",
         said_number(_ram_gb(), 0.1), f"{_ram_gb():.0f} GB.")
    task("cpu", "What processor does this computer have?",
         said_any(*_cpu_words()), " ".join(_cpu_words()) + ".")
    task("gpu", "What graphics card is in this PC?",
         said_any("5070"), "NVIDIA GeForce RTX 5070 Ti.")
    task("desktop-count", "How many files and folders are on my Desktop?",
         # Counted when checked: the Desktop changes under a long run.
         lambda r: said_number(_count(HOME / "Desktop"))(r),
         "Counted at check time.")

    # ---- making ---------------------------------------------------------
    offline = False
    task("create-text", f"{W.capitalize()}, create a file called shopping.txt that says buy milk and eggs.",
         lambda r: "milk" in read(R / "shopping.txt").lower() and "egg" in read(R / "shopping.txt").lower(),
         "shopping.txt: buy milk and eggs.")
    task("create-folder", f"Make a folder called Invoices {W}.",
         lambda r: (R / "Invoices").is_dir(), "Invoices/.")
    task("create-nested", f"Create the folders Travel, then 2026 inside it, then Japan inside that, {W}.",
         lambda r: (R / "Travel/2026/Japan").is_dir(), "Travel/2026/Japan/.")
    task("create-html", f"Make me an HTML page {W} called buzzer.html with a big red button that makes confetti fall when you press it.",
         lambda r: "<button" in read(R / "buzzer.html").lower() and len(read(R / "buzzer.html")) > 400,
         "A full page: button, CSS, confetti script.")
    task("create-py", f"Write a Python script called hello.py {W} that prints hello world.",
         lambda r: "print" in read(R / "hello.py") and "hello" in read(R / "hello.py").lower(),
         "print('Hello, world!')")
    task("create-json", f"Create config.json {W} with a name set to Naka and a version set to 2.",
         lambda r: _json(R / "config.json").get("name") == "Naka"
         and str(_json(R / "config.json").get("version")) == "2",
         '{"name": "Naka", "version": 2}')
    task("create-csv", f"Create contacts.csv {W} with name and email columns and two example people.",
         lambda r: _csv_ok(R / "contacts.csv", {"name", "email"}, 2),
         "name,email + 2 rows.")
    task("append-todo", f"Add call mom to the end of todo.txt {W}.",
         lambda r: "call mom" in read(R / "todo.txt").lower() and "stamps" in read(R / "todo.txt"),
         "todo.txt keeps its 3 lines and gains 'call mom'.")
    task("create-md", f"Create a README.md in the Code folder {W} with the title Bench project.",
         lambda r: "bench project" in read(R / "Code/README.md").lower(), "# Bench project")
    task("haiku", f"Write a haiku about rain into poem.txt {W}.",
         lambda r: len(lines(R / "poem.txt")) >= 3, "Three lines.")
    task("empty-file", f"Create an empty file called placeholder.txt {W}.",
         lambda r: (R / "placeholder.txt").is_file(), "placeholder.txt, 0 bytes.")
    task("ps1", f"Write a PowerShell script called today.ps1 {W} that prints the date.",
         lambda r: "get-date" in read(R / "today.ps1").lower(), "Get-Date")
    task("clock-html", f"Make a web page called clock.html {W} that shows a live clock.",
         lambda r: "<script" in read(R / "clock.html").lower(), "HTML with a setInterval script.")
    task("gitignore", f"Create a .gitignore in the Code folder {W} that ignores __pycache__.",
         lambda r: "__pycache__" in read(R / "Code/.gitignore"), "__pycache__/")
    task("reply", f"Write a short reply to Sam in reply.txt {W}, thanking him for the parcel.",
         lambda r: "sam" in read(R / "reply.txt").lower(), "Dear Sam, thank you…")
    task("months", f"Inside a new folder called Invoices2 {W}, make one folder each for January, February and March.",
         lambda r: len([p for p in (R / "Invoices2").glob("*") if p.is_dir()]) >= 3
         if (R / "Invoices2").is_dir() else False, "Invoices2/January, February, March.")
    task("five-notes", f"Create five files called note1.txt to note5.txt in a new folder called Notes {W}.",
         lambda r: len(list((R / "Notes").glob("note*.txt"))) == 5 if (R / "Notes").is_dir() else False,
         "Notes/note1.txt … note5.txt.")
    task("replace-name", f"In Papers/letter.txt {W}, replace Sam with Alex.",
         lambda r: "alex" in read(R / "Papers/letter.txt").lower() and "sam" not in read(R / "Papers/letter.txt").lower(),
         "Dear Alex, …")
    task("sort-names", f"Sort the names in names.txt {W} alphabetically and save them as sorted_names.txt.",
         lambda r: [l.lower() for l in lines(R / "sorted_names.txt")] == sorted(n.lower() for n in names),
         "9 names, sorted.")
    task("dedupe-names", f"Save the names from names.txt {W} without duplicates, ignoring case, into unique_names.txt.",
         lambda r: sorted({l.strip().lower() for l in lines(R / "unique_names.txt")})
         == sorted({n.lower() for n in names}) and len(lines(R / "unique_names.txt")) == 6,
         "6 names.")
    task("extract-errors", f"Copy just the ERROR lines from Logs/app.log {W} into errors.log.",
         lambda r: len(lines(R / "errors.log")) == 4 and all("ERROR" in l for l in lines(R / "errors.log")),
         "4 lines.")
    task("bump-version", f"Change the version in Code/data.json {W} to 2.",
         lambda r: str(_json(R / "Code/data.json").get("version")) == "2"
         and _json(R / "Code/data.json").get("name") == "Naka", '"version": 2, the rest unchanged.')
    task("add-row", f"Add Fred, 28, from Lyon to Code/data.csv {W}.",
         lambda r: "fred" in read(R / "Code/data.csv").lower() and len(lines(R / "Code/data.csv")) == 7,
         "7 lines.")
    task("upper", f"Make a copy of todo.txt {W} in capital letters, called TODO_UPPER.txt.",
         lambda r: "BUY STAMPS" in read(R / "TODO_UPPER.txt"), "BUY STAMPS…")
    task("backup-report", f"Make a copy of report.txt {W} called report_backup.txt.",
         lambda r: read(R / "report_backup.txt").strip() == "quarterly report", "Same content.")
    task("list-to-file", f"Write the names of the files in Photos {W} into photos_list.txt.",
         lambda r: all(n in read(R / "photos_list.txt") for n in ["img1", "holiday", "logo"]),
         "All 5 names.")
    task("proj-readme", f"Create a README.md in Projects/ProjC {W} that says this is project C.",
         lambda r: "c" in read(R / "Projects/ProjC/README.md").lower() and (R / "Projects/ProjC/README.md").exists(),
         "README.md in ProjC.")
    task("date-file", f"Save today's date into date.txt {W}.",
         lambda r: "2026" in read(R / "date.txt") or "26" in read(R / "date.txt"), "2026-09-24.")
    task("index-link", f"Create index.html {W} with a link to page2.html.",
         lambda r: "page2.html" in read(R / "index.html"), '<a href="page2.html">')
    task("count-script", f"Write a Python script called count_lines.py {W} that counts the lines in a file given on the command line.",
         lambda r: "open(" in read(R / "count_lines.py") or "read" in read(R / "count_lines.py"),
         "sys.argv[1], open, len(lines).")
    task("date-folder", f"Create a folder {W} named with today's date, like 2026-09-24.",
         lambda r: any(p.is_dir() and "2026" in p.name for p in R.iterdir()), "2026-09-24/.")
    task("combine-md", f"Combine all the markdown files {W} into one file called combined.md.",
         lambda r: all(t in read(R / "combined.md").lower() for t in ["# notes", "bench project", "proja"]),
         "All three.")
    task("three-lines", f"Write a file multi.txt {W} with three lines: line one, line two, line three.",
         lambda r: [l.strip().lower() for l in lines(R / "multi.txt")] == ["line one", "line two", "line three"],
         "Exactly those lines.")

    # ---- moving things around -------------------------------------------
    task("copy-folder", f"Copy the Alpha folder {W} to a new folder called Alpha_2, with everything in it.",
         lambda r: (R / "Alpha_2/sub/notes.md").exists() and (R / "Alpha_2/big.log").exists(),
         "Copy-Item -Recurse.")
    task("rename", f"Rename report.txt {W} to report-final.txt.",
         lambda r: (R / "report-final.txt").exists() and not (R / "report.txt").exists(), "Rename-Item.")
    task("move-png", f"Move the PNG files in Photos {W} into a subfolder of Photos called PNG.",
         lambda r: len(list((R / "Photos/PNG").glob("*.png"))) == 2 and not list((R / "Photos").glob("*.png"))
         if (R / "Photos/PNG").is_dir() else False, "Photos/PNG/holiday.png, logo.png.")
    task("move-jpg", f"Put the JPG files from Photos {W} into Photos/JPG.",
         lambda r: len(list((R / "Photos/JPG").glob("*.jpg"))) == 3 if (R / "Photos/JPG").is_dir() else False,
         "3 files.")
    task("delete-old", f"Delete old.log from the Logs folder {W}.",
         lambda r: not (R / "Logs/old.log").exists() and (R / "Logs/app.log").exists(), "Remove-Item.")
    task("delete-tmp", f"Delete all the .tmp files in the Logs folder {W}.",
         lambda r: not list((R / "Logs").glob("*.tmp")) and (R / "Logs/app.log").exists(), "3 removed.")
    task("rename-img", f"Rename Photos/img1.jpg {W} to beach.jpg.",
         lambda r: (R / "Photos/beach.jpg").exists() and not (R / "Photos/img1.jpg").exists(), "beach.jpg.")
    task("backup-cv", f"Copy my CV from Papers {W} into a new folder called Backup.",
         lambda r: (R / "Backup/cv.docx").exists() and (R / "Papers/cv.docx").exists(), "Backup/cv.docx.")
    task("zip", f"Zip the Alpha folder {W} into Alpha.zip.",
         lambda r: _zip_has(R / "Alpha.zip", "big.log"), "Compress-Archive.")
    task("unzip", f"Extract archive.zip {W} into a folder called Extracted.",
         lambda r: any(p.name == "hello.txt" for p in (R / "Extracted").rglob("*"))
         if (R / "Extracted").is_dir() else False, "Expand-Archive.")
    task("archive-proj", f"Move the ProjC folder out of Projects and into a new folder called Archive, {W}.",
         lambda r: (R / "Archive/ProjC/data.txt").exists() and not (R / "Projects/ProjC").exists(),
         "Archive/ProjC.")
    task("backup-code", f"Duplicate the Code folder {W} as Code_backup.",
         lambda r: (R / "Code_backup/app.py").exists() and (R / "Code_backup/data.json").exists(), "Recursive copy.")
    task("delete-empty", f"Delete the empty folder {W}.",
         lambda r: not (R / "EmptyDir").exists() and (R / "Alpha").exists(), "EmptyDir removed.")
    task("rename-folder", f"Rename the ProjA folder in Projects {W} to ProjectAlpha.",
         lambda r: (R / "Projects/ProjectAlpha/main.py").exists() and not (R / "Projects/ProjA").exists(),
         "Rename-Item.")
    task("copy-py", f"Copy only the Python files from Code {W} into a new folder called PyOnly.",
         lambda r: len(list((R / "PyOnly").glob("*.py"))) == 3 and not list((R / "PyOnly").glob("*.json"))
         if (R / "PyOnly").is_dir() else False, "3 .py files.")
    task("move-report", f"Move report.txt {W} into the Papers folder.",
         lambda r: (R / "Papers/report.txt").exists() and not (R / "report.txt").exists(), "Move-Item.")
    task("txt-to-md", f"In the Notes2 folder {W}, change all the .txt files to .md.",
         lambda r: len(list((R / "Notes2").glob("*.md"))) == 3 and not list((R / "Notes2").glob("*.txt")),
         "3 renamed.")
    task("readonly", f"Make report.txt {W} read-only.",
         lambda r: (R / "report.txt").exists() and not os.access(R / "report.txt", os.W_OK), "IsReadOnly.")
    task("sort-inbox", f"Sort the files in the Inbox folder {W} into subfolders by file type.",
         lambda r: not [p for p in (R / "Inbox").iterdir() if p.is_file()]
         and len([p for p in (R / "Inbox").iterdir() if p.is_dir()]) >= 3, "pdf, jpg, mp3, txt folders.")
    return T


# ------------------------------------------------------------ ground truth

def _json(path: Path) -> dict:
    try:
        loaded = json.loads(read(path))
    except (json.JSONDecodeError, ValueError):
        return {}
    # "Name" and "name" are the same field to the person who asked.
    return {str(k).lower(): v for k, v in loaded.items()} \
        if isinstance(loaded, dict) else {}


def _csv_ok(path: Path, columns: set[str], rows: int) -> bool:
    data = list(csv.reader(read(path).splitlines()))
    header = {h.strip().lower() for h in data[0]} if data else set()
    return columns <= header and len([r for r in data[1:] if any(r)]) >= rows


def _zip_has(path: Path, name: str) -> bool:
    try:
        with zipfile.ZipFile(path) as z:
            return any(n.endswith(name) for n in z.namelist())
    except (OSError, zipfile.BadZipFile):
        return False


def _py_count() -> int:
    root = CODING / "NakaV2"
    skip = {".venv", "build", "node_modules", ".git"}
    return sum(1 for p in root.rglob("*.py")
               if not skip & set(p.relative_to(root).parts))


def _free_gb() -> float:
    return shutil.disk_usage("C:\\").free / 1024**3


def _count(folder: Path) -> int:
    """What Explorer and Get-ChildItem show: not desktop.ini and the like."""
    if not folder.is_dir():
        return 0
    hidden = 0x2 | 0x4  # FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM
    return sum(1 for p in folder.iterdir()
               if not getattr(p.stat(), "st_file_attributes", 0) & hidden)


def _ps(command: str) -> str:
    try:
        return subprocess.run(["powershell", "-NoProfile", "-Command", command],
                              capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _windows() -> str:
    caption = _ps("(Get-CimInstance Win32_OperatingSystem).Caption")
    return "11" if "11" in caption else "10"


def _ram_gb() -> float:
    raw = _ps("(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory")
    return int(raw) / 1024**3 if raw.isdigit() else 32


def _cpu_words() -> list[str]:
    name = _ps("(Get-CimInstance Win32_Processor).Name").lower()
    for model in ("9800x3d", "ryzen", "intel", "core"):
        if model in name:
            return [model]
    return [name.split()[0]] if name else ["cpu"]
