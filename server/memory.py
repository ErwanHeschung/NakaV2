"""Conversation memory: persona, stable facts, a rolling summary, recent turns.

Flat files, no vector store — at one user and one conversation, retrieval
would add a dependency and buy nothing.

Prompt order matters for latency, not just for sense. llama.cpp caches the
longest common prefix of a prompt, so the pieces are assembled stable-first:
persona, then facts, then the summary, then the recent turns. Everything up to
the first change stays cached, which is why the volatile parts go last.
"""

import json
import logging
import re
import time
from collections import deque
from pathlib import Path

log = logging.getLogger("naka.memory")

CONFIG = Path(__file__).resolve().parent.parent / "config"

RECENT_TURNS = 3
# Turns beyond the recent window accumulate here until there are enough to be
# worth one summarisation call.
SUMMARISE_AFTER = 6

# Cheap gate: if he said nothing about himself, there is nothing to extract
# and the extra generation is not worth spending.
RECONCILE_LINE = re.compile(
    r"^(ADD|UPDATE|DELETE)\s*(\d+)?\s*:?\s*(.*)$", re.IGNORECASE)


def _fact_limits():
    from .tools.registry import FACTS
    return FACTS.get("max", 25), FACTS.get("max_chars", 120)


# Cheap gate, to avoid spending a generation on turns that cannot change
# anything. It has to catch two shapes: the user saying something about
# themselves, and the user asking for the memory itself to change. "Can you
# remove the thing about hiking" contains no first-person pronoun at all, and
# the earlier first-person-only gate silently swallowed every such request.
WORTH_CHECKING = re.compile(
    r"\b(i|i'm|im|my|mine|me|i've|i'll|j'|je|mon|ma|mes|"
    r"remember|remembers|forget|forgets|remove|removes|delete|deletes|"
    r"drop|erase|clear|save|store|keep|oublie|retiens|souviens|"
    r"supprime|enleve|enl\u00e8ve|efface)\b",
    re.IGNORECASE)

# The memory layer decides what to DO, not merely what to extract. Following
# the shape Mem0 uses: a candidate is compared against what is already known
# and the model picks an operation rather than always appending. An
# extract-only stage cannot express "that is now wrong", which is why
# retractions previously became negated duplicates and deletions depended on
# the model choosing to call a tool mid-conversation — which it often did not,
# while saying it had.
#
# No vector search: at a couple of dozen facts the whole list fits in the
# prompt, and showing all of it avoids a retrieval step that can miss the very
# fact being contradicted.
RECONCILE_PROMPT = (
    "You maintain a short list of durable facts about the user.\n\n"
    "Current facts, numbered:\n{known}\n\n"
    "Read the conversation and decide what should change. Reply with one "
    "operation per line, and nothing else:\n"
    "  ADD: <a new fact, one short sentence, third person>\n"
    "  UPDATE <n>: <the corrected wording of fact n>\n"
    "  DELETE <n>\n"
    "  NOOP\n\n"
    "Rules:\n"
    "- DELETE when the user asks you to forget something, or says a fact is "
    "wrong. Do not add a negated version — remove it.\n"
    "- UPDATE when a fact is still about the same thing but the details have "
    "changed.\n"
    "- ADD only for something durable and genuinely new: health, people close "
    "to them, strong preferences, constraints, long-running work, how they "
    "want to be treated. An explicit request to remember always counts.\n"
    "- NOOP for anything passing, already known, or merely reworded. Most "
    "turns are NOOP.\n"
    "- Resolve references from the conversation. Never write a fact "
    "containing 'this', 'that' or 'it' — name the thing.\n"
    "- Judge the final exchange; earlier turns are context only."
)

SUMMARY_PROMPT = (
    "Summarise this conversation in at most four short sentences. Keep "
    "decisions, preferences and unresolved questions; drop pleasantries and "
    "anything already obvious. Write plain prose, no lists."
)


class Memory:
    def __init__(self) -> None:
        self.persona = self._load_persona()
        self.facts = self._load_facts()
        self.recent: deque[tuple[str, str]] = deque(maxlen=RECENT_TURNS)
        self.pending: list[tuple[str, str]] = []
        self.summary = ""

    @staticmethod
    def _load_persona() -> str:
        """Load the persona, filling in who everyone is.

        Names live in settings so the app can ship to someone else without
        editing prompts embedded in code.
        """
        from . import settings
        text = (CONFIG / "persona.md").read_text().strip()
        try:
            return text.format(**settings.IDENTITY)
        except KeyError as e:
            log.warning("persona.md refers to unknown placeholder %s", e)
            return text

    @staticmethod
    def _load_facts() -> list[str]:
        path = CONFIG / "facts.json"
        if not path.exists():
            return []
        return json.loads(path.read_text()).get("facts", [])

    def save_facts(self) -> None:
        """Persist facts, keeping the file's own guidance comment intact.

        Written to a temporary file and renamed, so an interrupted write
        cannot leave her with no facts at all.
        """
        path = CONFIG / "facts.json"
        existing = json.loads(path.read_text()) if path.exists() else {}
        existing["facts"] = self.facts
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n")
        temp.replace(path)
        log.info("facts saved (%d)", len(self.facts))

    def reload(self) -> None:
        """Re-read persona and facts so they can be edited without a restart."""
        self.persona = self._load_persona()
        self.facts = self._load_facts()
        log.info("persona and facts reloaded")

    def system_prompt(self) -> str:
        parts = [self.persona]
        if self.facts:
            # Numbered so they can be referred to exactly. Deleting by matching
            # the wording was ambiguous — every fact shares the user's name —
            # and once removed the wrong one.
            parts.append(
                "## What you know about them\n\n"
                "These are numbered so you can drop one precisely; the numbers "
                "are for you, never say them aloud.\n\n"
                + "\n".join(f"{i}. {fact}"
                             for i, fact in enumerate(self.facts, 1))
            )
        if self.summary:
            parts.append("## Earlier in this conversation\n\n" + self.summary)
        return "\n\n".join(parts)

    def messages(self, user_text: str, note: str | None = None) -> list[dict]:
        messages = [{"role": "system", "content": self.system_prompt()}]
        for spoken, answered in self.recent:
            messages.append({"role": "user", "content": spoken})
            messages.append({"role": "assistant", "content": answered})
        # A note about this turn only — never stored, so it cannot leak into
        # the summary or colour later answers.
        if note:
            messages.append({"role": "system", "content": note})
        messages.append({"role": "user", "content": user_text})
        return messages

    def add_turn(self, user_text: str, reply: str) -> None:
        if len(self.recent) == self.recent.maxlen:
            self.pending.append(self.recent[0])
        self.recent.append((user_text, reply))

    def needs_summary(self) -> bool:
        return len(self.pending) >= SUMMARISE_AFTER

    async def summarise(self) -> None:
        """Fold older turns into the summary.

        Called off the response path: it is an extra generation, and making the
        user wait for it would put a second inference inside the latency budget.
        """
        from . import llm

        from . import settings
        who, me = settings.IDENTITY["user"], settings.IDENTITY["assistant"]
        transcript = "\n".join(f"{who}: {spoken}\n{me}: {answered}"
                               for spoken, answered in self.pending)
        if self.summary:
            transcript = f"Summary so far: {self.summary}\n\n{transcript}"

        start = time.perf_counter()
        self.summary = (await llm.complete([
            {"role": "system", "content": SUMMARY_PROMPT},
            {"role": "user", "content": transcript},
        ])).strip()
        self.pending.clear()
        log.info("summarised in %.0fms: %r",
                 (time.perf_counter() - start) * 1000, self.summary[:120])

    async def reconcile(self, user_text: str, reply: str) -> list[str]:
        """Bring the fact list in line with what was just said.

        One pass decides and applies ADD, UPDATE, DELETE or NOOP. It runs in
        the background, and it is the only thing that writes facts — the model
        is not asked to manage its own memory mid-conversation, because it
        reliably claims to have done so without doing it.
        """
        from . import llm, settings
        from .tools.builtin import validate_fact

        if not WORTH_CHECKING.search(user_text):
            return []

        who = settings.IDENTITY["user"]
        me = settings.IDENTITY["assistant"]
        known = "\n".join(f"{i}. {f}" for i, f in enumerate(self.facts, 1)) \
            or "(none yet)"
        history = "\n".join(f"{who}: {spoken}\n{me}: {answered}"
                            for spoken, answered in self.recent)
        exchange = f"{who}: {user_text}\n{me}: {reply}"

        raw = await llm.complete([
            {"role": "system", "content": RECONCILE_PROMPT.format(known=known)},
            {"role": "user", "content":
                (f"{history}\n{exchange}" if history else exchange)},
        ])

        applied = []
        # Deletions are applied last so earlier operations are not thrown off
        # by the renumbering, and highest-first for the same reason.
        deletions = []
        for line in raw.strip().splitlines():
            line = line.strip().strip("-*` ")
            if not line or line.upper().startswith("NOOP"):
                continue
            match = RECONCILE_LINE.match(line)
            if not match:
                continue
            op, number, text = match.group(1).upper(), match.group(2), match.group(3)

            if op == "DELETE" and number:
                deletions.append(int(number))
            elif op == "UPDATE" and number and text:
                index = int(number) - 1
                if 0 <= index < len(self.facts):
                    problem = validate_fact(text)
                    if problem:
                        log.info("rejected update: %s", problem)
                        continue
                    applied.append(f"updated {self.facts[index]!r} -> {text!r}")
                    self.facts[index] = text
            elif op == "ADD" and text:
                problem = validate_fact(text)
                if problem:
                    log.info("rejected add: %s", problem)
                    continue
                if any(f.lower() == text.lower() for f in self.facts):
                    continue
                max_facts, _ = _fact_limits()
                if len(self.facts) >= max_facts:
                    log.info("at the %d-fact limit, not adding %r",
                             max_facts, text)
                    continue
                self.facts.append(text)
                applied.append(f"added {text!r}")

        for number in sorted(set(deletions), reverse=True):
            if 1 <= number <= len(self.facts):
                applied.append(f"deleted {self.facts.pop(number - 1)!r}")

        if applied:
            self.save_facts()
            log.info("memory: %s", "; ".join(applied))
        else:
            log.info("memory: nothing to change after %r", user_text[:50])
        return applied

    def forget(self) -> None:
        self.recent.clear()
        self.pending.clear()
        self.summary = ""
        log.info("conversation cleared")


memory = Memory()
