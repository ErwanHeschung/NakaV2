"""Reading a document: "summarise this PDF", "what does the contract say".

PDF, Word, PowerPoint and plain text, read a part at a time: a part is a few
pages, small enough to fit beside the persona in the context window, and the
model asks for the next part when it needs more. No index, no vector store:
one person asking about one document does not need retrieval.

Where it may read depends on the shell power. Without it, only the person's
own document folders (Documents, Downloads, Desktop, the notes folder and the
workspace); with it, anywhere they can. A name without a folder is looked
for in those same places, so "the invoice I downloaded" can be found by name.

A document is written by someone else, so what comes back is untrusted: the
rest of the turn is treated like one that read a web page.
"""

import fnmatch
import html
import re
import time
import zipfile
from pathlib import Path

from .. import settings
from .registry import NOTES_DIR, tool

PART_CHARS = 6000
FIND_SECONDS = 4.0
TEXT = {".txt", ".md", ".markdown", ".csv", ".json", ".log", ".xml",
        ".yaml", ".yml", ".ini", ".toml", ".rtf"}
READABLE = TEXT | {".pdf", ".docx", ".pptx", ".html", ".htm"}
_SKIP = {"node_modules", ".git", ".venv", "venv", "__pycache__", "appdata"}


def roots() -> list[Path]:
    """The folders a document may come from without the shell power."""
    from .shell import workspace

    home = Path.home()
    found = [home / "Documents", home / "Downloads", home / "Desktop",
             NOTES_DIR, workspace()]
    # OneDrive moves Documents and Desktop when it backs them up.
    found += [p / name for p in home.glob("OneDrive*")
              for name in ("Documents", "Desktop")]
    return [p for p in found if p.exists()]


def _inside(path: Path, folders: list[Path]) -> bool:
    resolved = path.resolve()
    return any(resolved.is_relative_to(folder.resolve()) for folder in folders)


def _search(name: str, folders: list[Path]) -> list[Path]:
    """Files in these folders whose name fits, newest first."""
    pattern = name.lower()
    if not any(ch in pattern for ch in "*?"):
        pattern = f"*{pattern}*"
    deadline = time.monotonic() + FIND_SECONDS
    hits: list[Path] = []
    for folder in folders:
        for path in _walk(folder, 4, deadline):
            if path.suffix.lower() in READABLE and (
                    fnmatch.fnmatch(path.name.lower(), pattern)
                    or fnmatch.fnmatch(path.stem.lower().replace("_", " ")
                                       .replace("-", " "), pattern)):
                hits.append(path)
    return sorted(set(hits), key=lambda p: p.stat().st_mtime, reverse=True)


def _walk(folder: Path, depth: int, deadline: float):
    if depth < 0 or time.monotonic() > deadline:
        return
    try:
        entries = list(folder.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.is_dir():
            if entry.name.lower() not in _SKIP and not entry.name.startswith("."):
                yield from _walk(entry, depth - 1, deadline)
        else:
            yield entry


def locate(name: str) -> tuple[Path, list[str]]:
    """The file meant by a path or a name, and the names of any others that
    fit as well. Raises ValueError saying why when there is none."""
    anywhere = bool(settings.POWERS.get("shell"))
    allowed = roots()
    raw = Path(name.strip().strip('"')).expanduser()
    if raw.is_absolute():
        if not raw.exists():
            raise ValueError(f"there is no file at {raw}")
        if not anywhere and not _inside(raw, allowed):
            raise ValueError("without PowerShell switched on, documents can "
                             "only be read from Documents, Downloads, the "
                             "Desktop, the notes folder and the workspace")
        return raw, []
    for folder in allowed:
        if (folder / raw).is_file():
            return folder / raw, []
    hits = _search(name, allowed)
    if not hits:
        raise ValueError(f"no document called {name!r} in Documents, "
                         "Downloads, the Desktop, the notes or the workspace")
    # The newest is usually the one meant ("the invoice I just downloaded");
    # the others are named so she can say which she read.
    return hits[0], [h.name for h in hits[1:4]]


# ------------------------------------------------------------------ readers


def _xml_text(xml: str, paragraph: str) -> str:
    """The visible text of an Office XML part, a line per paragraph."""
    xml = re.sub(rf"</{paragraph}>", "\n", xml)
    xml = re.sub(r"<[^>]+>", "", xml)
    return html.unescape(xml)


def _docx(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml").decode("utf-8", "replace")
    xml = re.sub(r"<w:tab/>", "\t", xml)
    return [_xml_text(xml, "w:p")]


def _pptx(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        slides = sorted(
            (n for n in archive.namelist()
             if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
            key=lambda n: int(re.search(r"(\d+)", n).group(1)))
        return [f"Slide {i}:\n" + _xml_text(archive.read(n).decode(
                    "utf-8", "replace"), "a:p")
                for i, n in enumerate(slides, 1)]


def _pdf(path: Path) -> list[str]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            raise ValueError("that PDF is password-protected") from None
    return [page.extract_text() or "" for page in reader.pages]


def _html(path: Path) -> list[str]:
    import trafilatura

    page = path.read_text(encoding="utf-8", errors="replace")
    return [trafilatura.extract(page) or re.sub(r"<[^>]+>", " ", page)]


def pages(path: Path) -> list[str]:
    """The document as a list of pages (or slides, or one long page)."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _pdf(path)
    if suffix == ".docx":
        return _docx(path)
    if suffix == ".pptx":
        return _pptx(path)
    if suffix in (".html", ".htm"):
        return _html(path)
    if suffix in TEXT:
        return [path.read_text(encoding="utf-8", errors="replace")]
    raise ValueError(f"{path.suffix or 'that'} files cannot be read; PDF, "
                     "Word, PowerPoint and text can")


def parts(pages_text: list[str]) -> list[tuple[int, int, str]]:
    """Pages grouped into parts of about PART_CHARS: (first, last, text).
    A single long page is cut into pieces instead."""
    out: list[tuple[int, int, str]] = []
    first, buffer = 1, ""
    for number, page in enumerate(pages_text, 1):
        page = re.sub(r"[ \t]+\n", "\n", page).strip()
        while len(page) > PART_CHARS:
            if buffer:
                out.append((first, number - 1, buffer))
                buffer, first = "", number
            out.append((number, number, page[:PART_CHARS]))
            page = page[PART_CHARS:]
        if buffer and len(buffer) + len(page) > PART_CHARS:
            out.append((first, number - 1, buffer))
            buffer, first = "", number
        buffer = f"{buffer}\n\n{page}" if buffer else page
    if buffer or not out:
        out.append((first, len(pages_text), buffer))
    return out


@tool(
    description=(
        "Read one of the user's documents (PDF, Word, PowerPoint or text: a "
        "lease, an invoice, a list, a letter) to answer a question about it "
        "or summarise it. Call it straight away with the words they used "
        "for it, e.g. 'lease agreement' or 'packing list': it searches "
        "Documents, Downloads, the Desktop, the notes and the workspace by "
        "itself, so do not look for the file first. Long documents come in "
        "parts; ask for the next part only if the answer is not in this "
        "one."),
    parameters={
        "name": {"type": "string",
                 "description": "Path or file name, e.g. 'contract.pdf'."},
        "part": {"type": "integer",
                 "description": "Which part to read, from 1. Default 1."},
    },
    required=["name"],
    label="Read a document",
    summary="PDF, Word, PowerPoint or text, from your document folders.",
    # Written by someone else, like a web page.
    untrusted=True,
)
def read_document(name: str, part: int = 1):
    path, others = locate(name)
    chunks = parts(pages(path))
    if not any(text.strip() for _, _, text in chunks):
        return (f"{path.name} has no text that can be read: probably a "
                "scan, which would need OCR.")
    if not 1 <= part <= len(chunks):
        return f"{path.name} has {len(chunks)} part(s); there is no part {part}."
    first, last, text = chunks[part - 1]
    total = len(chunks)
    where = f"pages {first} to {last}" if last > first else f"page {first}"
    head = (f"{path.name} ({path.parent}), part {part} of {total}, {where}.")
    if others:
        head += f" Other files with a similar name: {', '.join(others)}."
    more = (f"\n(Part {part + 1} of {total} is next.)" if part < total else "")
    return (f"{head}\n<<untrusted document>>\n{text}\n"
            f"<</untrusted document>>{more}")
