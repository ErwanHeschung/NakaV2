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
        "created. The user is asked before it is written."
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
    label="Write a file",
    summary="Creates or changes a file anywhere you can. Always asks first.",
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
        "be part of the name, or a pattern like *.pdf."
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
    for root in roots:
        seen += [f for f in _find(root, name, max_depth) if f not in seen]
        if len(seen) >= FIND_LIMIT:
            break
    if not seen:
        where = " or ".join(str(r) for r in roots)
        return f"Nothing named like {name!r} under {where}."
    return "\n".join(seen[:FIND_LIMIT])


def _find(root: Path, name: str, max_depth: int) -> list[str]:
    pattern = name.lower().strip()
    if not any(ch in pattern for ch in "*?["):
        pattern = f"*{pattern}*"
    depth_limit = max(1, min(int(max_depth), 10))
    base = len(root.parts)
    deadline = time.monotonic() + FIND_SECONDS
    hits: list[str] = []
    for current, dirs, files in os.walk(root):
        depth = len(Path(current).parts) - base
        dirs[:] = [d for d in dirs if d.lower() not in SKIP
                   and not d.startswith(".")] if depth < depth_limit else []
        for entry in dirs + files:
            if fnmatch.fnmatch(entry.lower(), pattern):
                suffix = "\\" if entry in dirs else ""
                hits.append(str(Path(current) / entry) + suffix)
                if len(hits) >= FIND_LIMIT:
                    return hits + ["… (more; narrow the name)"]
        if time.monotonic() > deadline:
            hits.append(f"… (stopped after {FIND_SECONDS:.0f}s; give a folder "
                        "to search from)")
            break
    return hits
