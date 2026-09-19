"""The agentic loop, and the guardrails around it.

The model may chain allowlisted tools until it has an answer. Four things
bound that freedom, and none of them is optional:

  * a hard step ceiling and a wall-clock timeout, so a confused model cannot
    loop indefinitely;
  * the allowlist in tools.yaml, enforced in registry.call — there is no
    shell and no code execution to reach;
  * spoken confirmation before anything marked destructive, which suspends the
    loop and hands the decision back to the user;
  * a kill switch that stops the loop wherever it is.

Every call is audited whether it runs, is refused, or fails.
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator

from . import llm
from .tools import builtin  # noqa: F401  importing registers the tools
from .tools.registry import AGENT, call, get, schemas

log = logging.getLogger("naka.agent")

MAX_STEPS = AGENT["max_steps"]
TIMEOUT = AGENT["timeout"]

AFFIRMATIVE = {"yes", "yeah", "yep", "go", "do it", "confirm", "confirmed",
               "sure", "ok", "okay", "please do", "oui", "vas-y", "d'accord"}


class KillSwitch:
    """Stops the loop wherever it is. The client binds this to a hotkey."""

    def __init__(self) -> None:
        self._stop = asyncio.Event()

    def trip(self) -> None:
        self._stop.set()
        log.warning("kill switch tripped")

    def reset(self) -> None:
        self._stop.clear()

    @property
    def tripped(self) -> bool:
        return self._stop.is_set()


kill_switch = KillSwitch()

# A destructive call the model wanted to make, waiting on the user's word.
pending: dict | None = None


def is_affirmative(text: str) -> bool:
    cleaned = text.strip().lower().rstrip(".!")
    return cleaned in AFFIRMATIVE or cleaned.startswith(("yes", "yeah", "oui"))


async def resolve_pending(user_text: str) -> str | None:
    """Apply the user's answer to a waiting destructive call.

    Returns what to say, or None if nothing was pending. Anything that is not
    clearly a yes cancels: silence and ambiguity must not be treated as consent.
    """
    global pending
    if pending is None:
        return None

    request, pending = pending, None
    name, arguments = request["name"], request["arguments"]

    if not is_affirmative(user_text):
        from .tools.registry import audit
        audit(name, arguments, "declined", "user did not confirm")
        return "Left it alone."

    return call(name, arguments)


async def run(messages: list[dict]) -> AsyncIterator[str]:
    """Run the loop, yielding sentences as they are ready to speak."""
    global pending
    kill_switch.reset()
    started = time.perf_counter()
    working = list(messages)

    for step in range(MAX_STEPS):
        if kill_switch.tripped:
            yield "Stopped."
            return
        if time.perf_counter() - started > TIMEOUT:
            log.warning("agent timed out after %d steps", step)
            yield "That took too long, so I stopped."
            return

        message = await llm.complete_with_tools(working, schemas())
        calls = message.get("tool_calls") or []

        if not calls:
            text = (message.get("content") or "").strip()
            for sentence in llm.split_sentences(text):
                yield sentence
            return

        working.append(message)

        for request in calls:
            function = request.get("function", {})
            name = function.get("name", "")
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}

            tool_obj = get(name)
            if tool_obj is not None and tool_obj.destructive:
                # Suspend here. The loop does not resume by itself: the user
                # has to say yes out loud on the next turn.
                pending = {"name": name, "arguments": arguments}
                log.info("awaiting confirmation for %s(%s)", name, arguments)
                yield await _confirmation_question(name, arguments, working)
                return

            result = call(name, arguments)
            working.append({
                "role": "tool",
                "tool_call_id": request.get("id", name),
                "content": result,
            })

    log.warning("agent hit the %d step ceiling", MAX_STEPS)
    yield "I went in circles on that one, so I stopped."


CONFIRM_PROMPT = (
    "You are about to do something that cannot be undone. In one short "
    "spoken sentence, in your own voice, say plainly what it is and ask "
    "whether to go ahead. Do not name the tool or its parameters. Do not "
    "answer anything else."
)


def _plain_request(name: str, arguments: dict) -> str:
    detail = ", ".join(str(v) for v in arguments.values())
    return f"{name.replace('_', ' ')}: {detail}" if detail else name.replace("_", " ")


async def _confirmation_question(name: str, arguments: dict,
                                 context: list[dict]) -> str:
    """Ask in character rather than reciting the call.

    The generic version read "That means forget fact — fact X.. Shall I?",
    which names internals and breaks the persona at exactly the moment the
    user is being asked to trust a judgement. Worth one short generation.
    """
    request = _plain_request(name, arguments)
    # Her persona, so it sounds like her, then the one instruction.
    prompt = list(context[:1]) + [
        {"role": "system", "content": CONFIRM_PROMPT},
        {"role": "user", "content": f"About to: {request}"},
    ]
    try:
        spoken = (await llm.complete(prompt)).strip()
        if spoken:
            return spoken
    except Exception as e:
        log.warning("could not phrase confirmation (%s), using fallback", e)
    return f"About to {request.rstrip('.')}. Shall I?"
