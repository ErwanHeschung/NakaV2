"""The tools themselves.

Each one is narrow on purpose. Notes are confined to NOTES_DIR and filenames
are sanitised to a single path component, so nothing here can reach the rest
of the filesystem however the model phrases its arguments.
"""

import re
import subprocess
import threading
import time
from datetime import datetime

from .registry import NOTES_DIR, tool

_timers: dict[str, dict] = {}
_timer_lock = threading.Lock()
_SAFE_NAME = re.compile(r"[^a-z0-9 _-]")


def _note_path(name: str):
    """Resolve a note name to a file inside NOTES_DIR, and nowhere else."""
    cleaned = _SAFE_NAME.sub("", name.strip().lower()).strip()
    cleaned = re.sub(r"\s+", "-", cleaned)[:60]
    if not cleaned:
        raise ValueError("that is not a usable note name")
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    path = (NOTES_DIR / f"{cleaned}.md").resolve()
    if path.parent != NOTES_DIR.resolve():
        raise ValueError("note names cannot contain paths")
    return path


@tool(
    description="Get the current date and time.",
    parameters={},
)
def get_time():
    return datetime.now().strftime("It is %H:%M on %A %d %B %Y.")


@tool(
    description="Report how much GPU memory is free, and how much the models "
                "are using. Useful before starting a game.",
    parameters={},
)
def gpu_status():
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used,memory.total",
         "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=10,
    )
    used, total = (int(x) for x in result.stdout.strip().split(","))
    return (f"{used / 1024:.1f} GB of {total / 1024:.1f} GB in use, "
            f"{(total - used) / 1024:.1f} GB free.")


@tool(
    description="Start a countdown timer.",
    parameters={
        "duration_seconds": {"type": "integer",
                             "description": "How long, in seconds."},
        "label": {"type": "string", "description": "What the timer is for."},
    },
    required=["duration_seconds"],
)
def set_timer(duration_seconds: int, label: str = "timer"):
    if duration_seconds <= 0:
        raise ValueError("a timer needs a positive duration")
    if duration_seconds > 24 * 3600:
        raise ValueError("timers are capped at 24 hours")
    with _timer_lock:
        _timers[label] = {"due": time.time() + duration_seconds, "label": label}
    minutes, seconds = divmod(duration_seconds, 60)
    spoken = f"{minutes} minutes" if not seconds else f"{minutes}m {seconds}s"
    return f"Timer '{label}' set for {spoken if minutes else f'{seconds} seconds'}."


@tool(description="List timers that are still running.", parameters={})
def list_timers():
    now = time.time()
    with _timer_lock:
        live = {k: v for k, v in _timers.items() if v["due"] > now}
        _timers.clear()
        _timers.update(live)
        if not live:
            return "No timers running."
        return "; ".join(
            f"{v['label']}: {int(v['due'] - now)}s left" for v in live.values()
        )


@tool(
    description="Cancel a running timer.",
    parameters={"label": {"type": "string", "description": "Which timer."}},
    required=["label"],
)
def cancel_timer(label: str):
    with _timer_lock:
        if label not in _timers:
            return f"There is no timer called '{label}'."
        del _timers[label]
    return f"Cancelled '{label}'."


@tool(
    description="Search the user's notes and return matching lines.",
    parameters={
        "query": {"type": "string", "description": "Text to look for."},
        "limit": {"type": "integer", "description": "Max results, default 5."},
    },
    required=["query"],
)
def search_notes(query: str, limit: int = 5):
    if not NOTES_DIR.exists():
        return "There are no notes yet."
    hits = []
    needle = query.lower()
    for path in sorted(NOTES_DIR.glob("*.md")):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if needle in line.lower():
                hits.append(f"{path.stem} line {number}: {line.strip()}")
                if len(hits) >= limit:
                    return "; ".join(hits)
    return "; ".join(hits) if hits else f"Nothing in the notes about {query}."


@tool(
    description="Read a note by name.",
    parameters={"name": {"type": "string", "description": "The note's name."}},
    required=["name"],
)
def read_note(name: str):
    path = _note_path(name)
    if not path.exists():
        return f"There is no note called '{name}'."
    return path.read_text().strip()[:2000] or "That note is empty."


@tool(
    description="Write a note, or append to it if it already exists.",
    parameters={
        "name": {"type": "string", "description": "The note's name."},
        "content": {"type": "string", "description": "What to write."},
    },
    required=["name", "content"],
)
def write_note(name: str, content: str):
    path = _note_path(name)
    existed = path.exists()
    with path.open("a") as f:
        f.write(content.rstrip() + "\n")
    return f"{'Appended to' if existed else 'Created'} note '{path.stem}'."


@tool(
    description="Delete a note permanently.",
    parameters={"name": {"type": "string", "description": "The note's name."}},
    required=["name"],
)
def delete_note(name: str):
    path = _note_path(name)
    if not path.exists():
        return f"There is no note called '{name}'."
    path.unlink()
    return f"Deleted note '{path.stem}'."


def _facts_limits():
    from .registry import FACTS
    return FACTS.get("max", 25), FACTS.get("max_chars", 120)


# Verbs and fillers that say how someone relates to a subject rather than what
# the subject is. Ignoring them means "loves hiking" and "does not like hiking"
# compare as the same topic — which is the point, because a fact that reverses
# must replace its predecessor rather than sit beside it.
_PREDICATES = {
    "loves", "love", "likes", "like", "liked", "hates", "hate", "prefers",
    "prefer", "wants", "want", "enjoys", "enjoy", "dislikes", "dislike",
    "does", "doesn", "didn", "isn", "aren", "avoid", "avoids", "really",
    "very", "much", "more", "most", "always", "never", "still", "longer",
    "anymore", "that", "this", "with", "from", "about", "their", "them",
    "they", "have", "has", "had", "been", "being", "will", "would",
    # Naming words: "a cat called Pixel" and "a dog called Rex" share only
    # "called", which is not a shared subject.
    "called", "named", "name",
}


def _identity_words() -> set[str]:
    """The user's and assistant's own names carry no information here.

    Every fact is about the same person, so their name appears throughout and
    distinguishes nothing — treating it as a subject made "His name is Erwan"
    match "Erwan does not like hiking", and the wrong fact was deleted.
    """
    from ..settings import IDENTITY
    return {w for value in IDENTITY.values()
            for w in re.findall(r"[a-z']+", str(value).lower())}


def _topic(text: str) -> set[str]:
    words = {w for w in re.findall(r"[a-z']+", text.lower()) if len(w) > 3}
    return words - _PREDICATES - _identity_words() or words


def _same_subject(a: str, b: str) -> bool:
    """Whether two facts are about the same thing, however they judge it.

    Deliberately compares subjects rather than whole sentences: the case that
    matters is a fact being revised, and a revision shares its subject while
    contradicting the claim.
    """
    ta, tb = _topic(a), _topic(b)
    if not ta or not tb:
        return a.lower().strip() == b.lower().strip()
    shared = ta & tb
    if not shared:
        return False
    # One shared word is only convincing when that is all either fact is
    # about; otherwise two subjects must line up before one replaces another.
    if len(shared) == 1 and min(len(ta), len(tb)) > 1:
        return False
    return len(shared) / min(len(ta), len(tb)) >= 0.6


# Kept as an alias: the guardrail tests and forget_fact both use it for
# matching an existing fact loosely.
_similar = _same_subject


@tool(
    description=(
        "Save a fact about the user so it survives restarts. Call this "
        "whenever they tell you something durable about themselves: allergies "
        "and health, people close to them, strong preferences, constraints, "
        "work that runs for months, how they want to be spoken to. Saying you "
        "will remember does NOT remember it — this call is the only thing "
        "that persists anything, so make it the first time you hear something "
        "worth keeping. Do not save passing detail, what was said moments ago, "
        "or anything this conversation already carries: the list is capped, "
        "and trivia in it crowds out what matters."
    ),
    parameters={
        "fact": {
            "type": "string",
            "description": "One short sentence, written in the third person, "
                           "e.g. 'They prefer Rust over Go.'",
        }
    },
    required=["fact"],
)
def remember(fact: str):
    from ..memory import memory

    fact = " ".join(fact.split())
    max_facts, max_chars = _facts_limits()

    if len(fact) < 8:
        raise ValueError("that is too short to be a useful fact")
    if len(fact) > max_chars:
        raise ValueError(
            f"that is {len(fact)} characters; keep a fact under {max_chars}"
        )

    for existing in list(memory.facts):
        if not _same_subject(fact, existing):
            continue
        if existing.lower().strip() == fact.lower().strip():
            return f"Already known: '{existing}'"
        # Same subject, different claim: the newer statement wins. Keeping both
        # is how a facts list ends up asserting that he loves and hates hiking.
        memory.facts[memory.facts.index(existing)] = fact
        memory.save_facts()
        return f"Updated: '{existing}' is now '{fact}'"

    if len(memory.facts) >= max_facts:
        # Refusing rather than evicting something itself: which fact matters
        # least is a judgement, and it is not the registry's to make.
        return (
            f"At the limit of {max_facts} facts, so nothing new fits. Decide "
            f"which of these matters least and forget it first: "
            + "; ".join(memory.facts[-8:])
        )

    memory.facts.append(fact)
    memory.save_facts()
    return f"Noted, permanently. ({len(memory.facts)}/{max_facts} remembered)"


@tool(
    description=(
        "Delete one remembered fact, by its number in the numbered list you "
        "were given. Call this whenever the user asks you to forget "
        "something, or tells you a fact is wrong or out of date. Saying you "
        "have forgotten it does NOT forget it — this call is the only thing "
        "that removes anything, so make the call in the same turn rather than "
        "promising to."
    ),
    parameters={
        "number": {"type": "integer",
                   "description": "Which fact to drop, as numbered in the "
                                  "list of things you know about them."}
    },
    required=["number"],
)
def forget_fact(number: int):
    from ..memory import memory

    if not memory.facts:
        return "There is nothing remembered to forget."
    if not 1 <= number <= len(memory.facts):
        return (f"There is no fact {number}; they run from 1 to "
                f"{len(memory.facts)}.")

    dropped = memory.facts.pop(number - 1)
    memory.save_facts()
    # The remaining numbering is returned because it has just shifted, and a
    # second deletion in the same turn would otherwise use stale numbers.
    remaining = "; ".join(f"{i}. {f}" for i, f in enumerate(memory.facts, 1))
    return f"Forgotten: '{dropped}'. Now: {remaining or 'nothing remembered'}"
