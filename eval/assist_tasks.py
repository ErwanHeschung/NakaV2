"""Clipboard, document and history scenarios for eval/agent_bench.py.

The real model, with the clipboard, the document folders and the
conversation history replaced by fixtures: nothing reads or writes the
person's own clipboard, files or history.
"""

import zipfile
from pathlib import Path

from bench_tasks import said_any

from server import conversation
from server.tools import clipboard, documents

state = {"clipboard": ""}

HISTORY = [
    {"id": 1, "at": "2026-09-19T20:14:00", "user": "Any good co-op game for this weekend?",
     "naka": "Try Deep Rock Galactic: four dwarves, a cave, and a lot of shouting.",
     "via": "voice", "tools": [], "interrupted": False},
    {"id": 2, "at": "2026-09-21T09:02:00", "user": "Remind me what my sister's birthday gift idea was",
     "naka": "You wanted to get her a pottery class in Lyon.", "via": "voice",
     "tools": [], "interrupted": False},
    {"id": 3, "at": "2026-09-24T18:40:00", "user": "What's a good pasta for tonight?",
     "naka": "Cacio e pepe: pasta, pecorino, black pepper, done in fifteen minutes.",
     "via": "text", "tools": [], "interrupted": False},
]

COPIED = ("hi team, i wont be able to make it to the meeting tomorow, "
          "can we moved it to thursday? sorry for the late notice")


def setup(folder: Path) -> None:
    state["clipboard"] = COPIED
    clipboard.read = lambda: state["clipboard"]
    clipboard.write = lambda text: state.update(clipboard=text)
    conversation._turns = [dict(t) for t in HISTORY]
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.iterdir():
        old.unlink()
    with zipfile.ZipFile(folder / "lease-agreement.docx", "w") as z:
        z.writestr("word/document.xml",
                   "<w:document><w:body>"
                   + "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in (
                       "Residential lease, 12 Rue Victor Hugo, Nice.",
                       "Rent: 950 EUR per month, due on the 5th.",
                       "Notice: the tenant may leave with one month's notice.",
                       "Pets are allowed with the landlord's written consent."))
                   + "</w:body></w:document>")
    (folder / "packing-list.txt").write_text(
        "Passport\nCharger\nHiking boots\nSunscreen\n", encoding="utf-8")
    documents.roots = lambda: [folder]


def clip_has(*words):
    return lambda r: all(w.lower() in state["clipboard"].lower() for w in words)


def called(name):
    return lambda r: any(c["name"] == name for c in r["calls"])


def both(*checks):
    return lambda r: all(c(r) for c in checks)


def task(ident, ask, check, reference=""):
    return {"id": ident, "kind": "assist", "ask": [ask], "check": check,
            "reference": reference, "origin": "pc", "before": None}


def tasks() -> list[dict]:
    return [
        task("clip-summary", "What does the text I just copied say? Short version.",
             both(called("read_clipboard"), said_any("thursday", "meeting")),
             "wants to move the meeting to Thursday"),
        task("clip-fix", "Fix the spelling in what I copied and put it back in my clipboard.",
             both(clip_has("tomorrow", "move"), lambda r: "tomorow" not in state["clipboard"]),
             "corrected text in the clipboard"),
        task("clip-translate", "Translate my clipboard into French and copy it back.",
             clip_has("jeudi"), "French text with jeudi"),
        task("doc-rent", "How much is the rent in my lease agreement?",
             both(called("read_document"), said_any("950")), "950 EUR"),
        task("doc-pets", "Does my lease allow pets?",
             said_any("consent", "permission", "written", "allowed", "yes"),
             "yes, with written consent"),
        task("doc-packing", "Read me my packing list.",
             said_any("passport", "boots"), "passport, charger, boots, sunscreen"),
        task("doc-missing", "Summarise my tax return PDF.",
             said_any("find", "couldn't", "can't", "no ", "not "),
             "no such document: says so"),
        task("hist-game", "What was that co-op game you recommended last week?",
             said_any("deep rock"), "Deep Rock Galactic"),
        task("hist-gift", "What was my gift idea for my sister again?",
             both(called("search_conversations"), said_any("pottery")),
             "pottery class in Lyon"),
    ]
