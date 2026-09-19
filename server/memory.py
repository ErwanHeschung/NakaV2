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
import time
from collections import deque
from pathlib import Path

log = logging.getLogger("naka.memory")

CONFIG = Path(__file__).resolve().parent.parent / "config"

RECENT_TURNS = 3
# Turns beyond the recent window accumulate here until there are enough to be
# worth one summarisation call.
SUMMARISE_AFTER = 6

SUMMARY_PROMPT = (
    "Summarise this conversation in at most four short sentences. Keep "
    "decisions, preferences and unresolved questions; drop pleasantries and "
    "anything already obvious. Write plain prose, no lists."
)


class Memory:
    def __init__(self) -> None:
        self.persona = (CONFIG / "persona.md").read_text().strip()
        self.facts = self._load_facts()
        self.recent: deque[tuple[str, str]] = deque(maxlen=RECENT_TURNS)
        self.pending: list[tuple[str, str]] = []
        self.summary = ""

    @staticmethod
    def _load_facts() -> list[str]:
        path = CONFIG / "facts.json"
        if not path.exists():
            return []
        return json.loads(path.read_text()).get("facts", [])

    def reload(self) -> None:
        """Re-read persona and facts so they can be edited without a restart."""
        self.persona = (CONFIG / "persona.md").read_text().strip()
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

        transcript = "\n".join(f"Erwan: {spoken}\nNaka: {answered}"
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

    def forget(self) -> None:
        self.recent.clear()
        self.pending.clear()
        self.summary = ""
        log.info("conversation cleared")


memory = Memory()
