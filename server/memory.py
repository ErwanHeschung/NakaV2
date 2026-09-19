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
FIRST_PERSON = re.compile(r"\b(i|i'm|im|my|mine|me|i've|i'll|j'|je|mon|ma|mes)\b",
                          re.IGNORECASE)

FACT_PROMPT = (
    "You extract durable facts about the user from one exchange. Reply with a "
    "single short sentence in the third person if — and only if — the user "
    "stated something about themselves that will still matter in six months: "
    "health, allergies, people close to them, strong preferences, "
    "constraints, work lasting months, how they want to be treated. Reply "
    "with exactly NONE for anything passing: what they did today, what they "
    "feel now, questions, opinions about the immediate topic, or anything "
    "already obvious. Most exchanges are NONE. Reply with the fact or NONE, "
    "nothing else."
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
            parts.append("## What you know about him\n\n" +
                         "\n".join(f"- {fact}" for fact in self.facts))
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

    async def consider_fact(self, user_text: str, reply: str) -> str | None:
        """Decide, after the fact, whether the exchange held something durable.

        Asking the model to notice this mid-conversation does not work: given
        eleven tools and a conversational turn, a 12B model answers warmly and
        never reaches for one — it says "I'll remember that" and remembers
        nothing, which is worse than not offering to. A separate pass with one
        job and one question behaves much better, and runs in the background so
        it costs no latency.
        """
        from . import llm, settings
        from .tools.builtin import remember

        # Nothing self-referential was said, so there is nothing to keep.
        if not FIRST_PERSON.search(user_text):
            return None

        verdict = (await llm.complete([
            {"role": "system", "content": FACT_PROMPT},
            {"role": "user", "content":
                f"{settings.IDENTITY['user']}: {user_text}\n"
                f"{settings.IDENTITY['assistant']}: {reply}"},
        ])).strip().strip('"')

        if not verdict or verdict.upper().startswith("NONE"):
            return None

        # Goes through the tool, so the cap, length limit and duplicate check
        # apply exactly as they would if she had called it herself.
        outcome = remember(verdict)
        log.info("fact considered: %r -> %s", verdict, outcome)
        return verdict

    def forget(self) -> None:
        self.recent.clear()
        self.pending.clear()
        self.summary = ""
        log.info("conversation cleared")


memory = Memory()
