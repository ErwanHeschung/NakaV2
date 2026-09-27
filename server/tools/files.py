"""Files, without going through a command line.

Everything here could be done with run_command, and the model did try: an HTML
page written through Set-Content needs every quote in the page escaped for
PowerShell inside a JSON string, and a 12B model got that wrong or ran out of
room every time. Writing a file is a path and some text, so that is the tool.

Finding things is here for a similar reason. Asked to look everywhere, the
model ran Get-ChildItem over all of C: and hit the time limit; the same search
over the home folder, a few levels deep and skipping caches, takes a fraction
of a second.

These belong to the shell power: they reach the whole disk, as the person.
Writing always asks first, through tools.yaml.
"""

import fnmatch
import os
import re
import time
from pathlib import Path

from .registry import tool
from .shell import workspace

READ_CHARS = 3000
FIND_LIMIT = 30
FIND_SECONDS = 8.0
# Folders that are large, generated, or not the person's own files. Skipping
# them is most of why a home-folder search is fast.
SKIP = {"appdata", "node_modules", ".git", ".venv", "venv", "__pycache__",
        "$recycle.bin", "windows", "program files", "program files (x86)",
        "programdata", ".cache", ".npm", ".nuget", ".gradle", "site-packages"}


def resolve(path: str) -> Path:
    """A path as the person would mean it: ~ and %VARS% expanded, and a
    relative path taken from the workspace rather than wherever the server
    happened to start."""
    expanded = Path(os.path.expandvars(path.strip().strip('"'))).expanduser()
    return expanded if expanded.is_absolute() else workspace() / expanded


@tool(
    description=(
        "Create or overwrite a text file with the given content, or add to "
        "the end of it with append. Use this for writing any file — notes, "
        "code, HTML — rather than a PowerShell command. Parent folders are "
        "created. Replacing a file that exists is put to the user first."
    ),
    parameters={
        "path": {"type": "string",
                 "description": "Full path, or a name inside the workspace."},
        "content": {"type": "string", "description": "The whole text."},
        "append": {"type": "boolean",
                   "description": "Add to the end instead of replacing."},
    },
    required=["path", "content"],
    power="shell",
    # A new file or an append loses nothing. Replacing one that exists does.
    confirm=lambda arguments, tainted: tainted or (
        not arguments.get("append") and resolve(arguments.get("path", "")).exists()),
    label="Write a file",
    summary="Creates or adds to a file anywhere you can. Asks before replacing one.",
)
def write_file(path: str, content: str, append: bool = False):
    target = resolve(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    with target.open("a" if append else "w", encoding="utf-8") as f:
        f.write(content)
    verb = "Appended to" if append else ("Replaced" if existed else "Created")
    return f"{verb} {target} ({target.stat().st_size} bytes)."


@tool(
    description=(
        "Read a text file. Long files are cut; give start_line to read "
        "further on."
    ),
    parameters={
        "path": {"type": "string",
                 "description": "Full path, or a name inside the workspace."},
        "start_line": {"type": "integer",
                       "description": "First line to read, from 1."},
    },
    required=["path"],
    power="shell",
    label="Read a file",
    summary="Reads a text file.",
)
def read_file(path: str, start_line: int = 1):
    target = resolve(path)
    if not target.is_file():
        raise ValueError(f"{target} is not a file")
    if target.suffix.lower() in (".pdf", ".docx", ".pptx"):
        # Their bytes are not text; read as text they are pages of noise.
        return (f"{target.name} is a document, not a text file: read it "
                f"with read_document, name {str(target)!r}.")
    raw = target.read_bytes()
    # Windows PowerShell's Out-File writes UTF-16, byte order mark first.
    encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    lines = raw.decode(encoding, errors="replace").splitlines()
    first = max(1, int(start_line))
    text = "\n".join(lines[first - 1:])
    if len(text) > READ_CHARS:
        shown = text[:READ_CHARS].count("\n") + first
        text = (text[:READ_CHARS]
                + f"\n… (cut; {len(lines)} lines in all, continue at "
                  f"start_line={shown + 1})")
    return f"{target}, from line {first}:\n{text or '(empty)'}"


@tool(
    description=(
        "Find files or folders by name when you do not know where they are. "
        "Searches the workspace, then the home folder (or the folder you "
        "give), a few levels deep, skipping AppData and caches. The name can "
        "be part of the name, or a pattern like *.pdf. To read a document "
        "(PDF, Word, a list, a letter) call read_document with its name "
        "directly: it finds it by itself."
    ),
    parameters={
        "name": {"type": "string",
                 "description": "All or part of the name, or a pattern."},
        "folder": {"type": "string",
                   "description": "Where to start. Defaults to the workspace, "
                                  "then the home folder."},
        "max_depth": {"type": "integer",
                      "description": "How many levels down, default 5."},
    },
    required=["name"],
    power="shell",
    label="Find files",
    summary="Searches your folders by name, quickly.",
)
def find_files(name: str, folder: str = "", max_depth: int = 5):
    if folder:
        roots = [resolve(folder)]
    else:
        # The workspace first: it is where she works and where "my file" most
        # often is, and it may sit somewhere the home search skips.
        roots = [workspace(), Path.home()]
    roots = [r for r in roots if r.is_dir()]
    if not roots:
        raise ValueError(f"{resolve(folder)} is not a folder")
    seen: list[str] = []
    near: list[str] = []
    for root in roots:
        seen += [f for f in _find(root, name, max_depth, near) if f not in seen]
        if len(seen) >= FIND_LIMIT:
            break
    if not seen:
        where = " or ".join(str(r) for r in roots)
        if near:
            # Something to interpret rather than a dead end: "credit song"
            # misheard may well be one of these.
            return (f"Nothing named {name!r} under {where}. Closest, sharing "
                    "some of the words:\n" + "\n".join(dict.fromkeys(near)))
        return f"Nothing named like {name!r} under {where}."
    return "\n".join(seen[:FIND_LIMIT])


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _find(root: Path, name: str, max_depth: int,
          near: list[str] | None = None) -> list[str]:
    """Paths under root whose name fits. A pattern (*.pdf) is matched as one;
    plain words match in any order and around any separator, so "credit
    song" finds Credit_Song.mp3 and song-credit.wav. Entries sharing only
    some of the words are put in `near`, for when nothing fits."""
    pattern = name.lower().strip()
    glob = any(ch in pattern for ch in "*?[")
    wanted = _words(pattern)
    depth_limit = max(1, min(int(max_depth), 10))
    base = len(root.parts)
    deadline = time.monotonic() + FIND_SECONDS
    hits: list[str] = []
    for current, dirs, files in os.walk(root):
        depth = len(Path(current).parts) - base
        dirs[:] = [d for d in dirs if d.lower() not in SKIP
                   and not d.startswith(".")] if depth < depth_limit else []
        for entry in dirs + files:
            lowered = entry.lower()
            path = str(Path(current) / entry) + ("\\" if entry in dirs else "")
            if glob:
                matched = fnmatch.fnmatch(lowered, pattern)
            else:
                matched = bool(wanted) and all(w in lowered for w in wanted)
                if (not matched and near is not None and len(near) < 10
                        and any(len(w) > 3 and w in lowered for w in wanted)):
                    near.append(path)
            if matched:
                hits.append(path)
                if len(hits) >= FIND_LIMIT:
                    return hits + ["… (more; narrow the name)"]
        if time.monotonic() > deadline:
            hits.append(f"… (stopped after {FIND_SECONDS:.0f}s; give a folder "
                        "to search from)")
            break
    return hits
