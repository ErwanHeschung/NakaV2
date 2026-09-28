"""One reply, two readers: the chat shows it, the voice says it.

The model writes Markdown. The chat renders it, so it needs the reply as
written: its line breaks, its lists, a code block whole. The voice needs
only what a person would say aloud: no code, no addresses, no table grid, no
drive paths, no hashes. Both are cut from the same stream, a segment at a
time, so the first sentence is still spoken while the rest is being written.

Segments keep their whitespace. A reply is the concatenation of its
segments, which is how the line breaks between paragraphs survive, and
join() puts a space back only where two pieces would otherwise run
together ("Let me look that up." followed by the answer).
"""

import re

# A sentence ends at its punctuation followed by space, or at a line break.
# Mid-token dots ("18.5", "file.txt") have no space after them and do not
# end anything.
_BOUNDARY = re.compile(r"[.!?]+[\"')\]*_]*[ \t]+|\n+")
# A fence opens at the start of a line: ``` or ~~~, with a language or not.
_FENCE = re.compile(r"(?:^|\n)[ \t]*(```|~~~)")


class Segmenter:
    """Cut a streamed reply into segments, keeping code blocks whole."""

    def __init__(self) -> None:
        self.buffer = ""
        # The marker of the open code block, and where its body starts in
        # the buffer, so the opening fence is never taken for the closing one.
        self.fence: str | None = None
        self.body = 0
        self.carry = ""  # whitespace waiting to lead the next segment

    def _emit(self, text: str) -> list[str]:
        text = self.carry + text
        self.carry = ""
        if not text.strip():
            # Blank lines belong to the text around them. Held rather than
            # sent alone, so the voice is never handed nothing to say.
            self.carry = text
            return []
        return [text]

    def _take(self, end: int) -> str:
        taken, self.buffer = self.buffer[:end], self.buffer[end:]
        return taken

    def feed(self, piece: str) -> list[str]:
        self.buffer += piece
        out: list[str] = []
        while True:
            if self.fence is not None:
                # Inside a block: wait for its closing fence and the end of
                # that line, then send the block as one segment.
                close = re.compile(rf"\n[ \t]*{re.escape(self.fence)}[ \t]*\n")
                found = close.search(self.buffer, self.body)
                if found is None:
                    return out
                self.fence = None
                out += self._emit(self._take(found.end()))
                continue

            fence = _FENCE.search(self.buffer)
            boundary = _BOUNDARY.search(self.buffer)
            if fence and (boundary is None or fence.start() <= boundary.start()):
                # What came before the block goes first, with the newline
                # that ended it; the block is then held until it closes.
                head_end = fence.start() + (1 if fence.group(0).startswith("\n") else 0)
                if head_end:
                    out += self._emit(self._take(head_end))
                self.fence = fence.group(1)
                self.body = self.buffer.index(self.fence) + len(self.fence)
                continue

            if boundary is None:
                return out
            out += self._emit(self._take(boundary.end()))

    def flush(self) -> list[str]:
        """Whatever is left when the stream ends, an unclosed block included."""
        rest, self.buffer = self.buffer, ""
        self.fence = None
        if not (self.carry + rest).strip():
            self.carry = ""
            return []
        return self._emit(rest)


def join(parts: list[str]) -> str:
    """The reply as written, from its segments."""
    out = ""
    for part in parts:
        if out and part and not out[-1].isspace() and not part[0].isspace():
            out += " "
        out += part
    return out.strip()


# ------------------------------------------------------------------ voice

# Not ending on punctuation: the full stop after an address ends the sentence.
_URL = re.compile(r"<?(?:https?://|\bwww\.)[^\s<>()]*[^\s<>().,;:!?'\"]>?")
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
# Something in backticks that is a name, not code: a file, a command word.
_NAMELIKE = re.compile(r"[\w.\- ]{1,40}")
_WIN_PATH = re.compile(r"(?:[A-Za-z]:\\|\\\\)[^\s,;]*")
_UNIX_PATH = re.compile(r"(?<![\w/])(?:~|/[\w.-]+)(?:/[\w.\- ]*[\w.-])+")
_HEX = re.compile(r"\b(?=[0-9a-fA-F]*\d)[0-9a-fA-F]{16,}\b")
_LONG_TOKEN = re.compile(r"\S{40,}")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")
_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]+", re.MULTILINE)
_LIST = re.compile(r"^[ \t]*(?:[-*+•]|\d{1,3}[.)])[ \t]+", re.MULTILINE)
_QUOTE = re.compile(r"^[ \t]*>[ \t]?", re.MULTILINE)
_RULE = re.compile(r"^[ \t]*(?:[-*_][ \t]*){3,}$", re.MULTILINE)
_TABLE_ROW = re.compile(r"^[ \t]*\|.*\|[ \t]*$", re.MULTILINE)
_EMPHASIS = re.compile(r"(\*\*|__|\*|~~)(?=\S)(.+?)(?<=\S)\1")
_STRAY = re.compile(r"[*`#~|<>{}\[\]\\]+")


def is_code(segment: str) -> bool:
    stripped = segment.lstrip()
    return stripped.startswith(("```", "~~~"))


def _last_part(path: str) -> str:
    """A path said the way a person says it: by the thing at its end."""
    name = re.split(r"[\\/]", path.rstrip("\\/"))[-1]
    return name or ""


def _inline_code(match: re.Match) -> str:
    inside = match.group(1).strip()
    # `notes.txt`, `git status` are words. `x = f(y)` is code.
    if _NAMELIKE.fullmatch(inside) and not re.search(r"[=(){};$<>]", inside):
        return _last_part(inside) if ("\\" in inside or "/" in inside) else inside
    # Something is still being pointed at: "add a line like `print(x)`"
    # would otherwise end on "like".
    return "this"


def spoken(segment: str) -> str:
    """What the voice says for this segment. Empty means say nothing."""
    if is_code(segment):
        return ""
    text = _TABLE_ROW.sub("", segment)
    text = _RULE.sub("", text)
    text = _LINK.sub(r"\1", text)
    text = _URL.sub("the link in the chat", text)
    text = _INLINE_CODE.sub(_inline_code, text)
    text = _WIN_PATH.sub(lambda m: _last_part(m.group(0)), text)
    text = _UNIX_PATH.sub(lambda m: _last_part(m.group(0)), text)
    text = _HEX.sub("", text)
    text = _LONG_TOKEN.sub("", text)
    text = _EMOJI.sub("", text)
    text = _HEADING.sub("", text)
    text = _LIST.sub("", text)
    text = _QUOTE.sub("", text)
    text = _EMPHASIS.sub(r"\2", text)
    # Underscores inside a word are part of it (file_name.txt); markup left
    # over after all that is not.
    text = re.sub(r"(?<=\w)_(?=\w)", "\x00", text)
    text = _STRAY.sub(" ", text).replace("_", " ").replace("\x00", "_")
    text = " ".join(text.split())
    # Punctuation that lost its words: " , ." after a removed address.
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"^[,.;:]+\s*", "", text)
    return "" if not re.search(r"\w", text) else text


# Said once per reply in place of the first code block, so a voice answer
# that is mostly code is not silence.
CODE_NOTE = "I've put the code in the chat."
