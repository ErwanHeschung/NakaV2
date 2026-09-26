"""Telegram: a bot of the person's own, so she can be reached away from the PC.

Long polling from here rather than a webhook, so nothing has to be reachable
from the internet: the PC asks Telegram for new messages, and Telegram holds
the request open until there are some.

Exactly one chat is listened to. Anyone can find a bot and write to it, so
until the person pairs their own chat nothing is answered, and after that a
message from any other chat is dropped unread (and audited). Pairing is two
steps on purpose: /start in Telegram proposes a chat, and only a click in the
panel, on the PC, accepts it.

What arrives is still treated as untrusted. Owning the bot's chat means
owning the phone it is on, which is a weaker thing than sitting at the PC:
a text turn from Telegram is tainted like one that read a web page, so
anything that changes something asks first, and PowerShell is not offered
to it at all.
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable

from .. import events, paths
from ..tools.registry import audit, tool
from .base import Connection, Failed, Secret, Setting, Status, Step

log = logging.getLogger("naka.telegram")

API = "https://api.telegram.org"
OFFSET_FILE = paths.DATA / "telegram.json"
# Seconds Telegram may hold a poll open before answering with nothing.
LONG_POLL = 25
LIMIT = 4096


class Telegram(Connection):
    name = "telegram"
    label = "Telegram"
    icon = "message-circle"
    blurb = ("Text her from your phone, and get reminders, timers and notes "
             "sent there. Only your own chat is answered.")
    settings = [
        Setting("forward_reminders", "Send reminders here", "bool",
                "Reminders ring on the PC and arrive in Telegram too."),
        Setting("forward_timers", "Send timers here", "bool",
                "Timers that ring are sent too, for when you left the "
                "kitchen."),
    ]
    secrets = [
        Secret("bot_token", "Bot token",
               "From @BotFather, looks like 123456789:AAE...  Kept in "
               "Windows Credential Manager."),
    ]

    def __init__(self) -> None:
        super().__init__()
        # Chats that sent /start while nobody was paired, by id.
        self.candidates: dict[int, dict] = {}
        self.bot_name = ""
        # Set by the server: runs a text turn and returns her reply.
        self.on_message: Callable[[str], Awaitable[str]] | None = None

    def guide(self) -> list[Step]:
        return [
            Step("In Telegram, open @BotFather, send /newbot and choose a "
                 "name. It answers with a token.", "https://t.me/BotFather"),
            Step("Paste the token above and press Save and check."),
            Step("Open your new bot in Telegram and send it /start. Your chat "
                 "appears here: confirm it's you."),
        ]

    def owner(self) -> int:
        return int(self.conf().get("chat_id") or 0)

    def configured(self) -> bool:
        return bool(self.secret("bot_token")) and self.owner() != 0

    def extra(self) -> dict:
        return {
            "owner": self.owner(),
            "bot": self.bot_name,
            "candidates": [{"chat_id": k, **v}
                           for k, v in self.candidates.items()],
        }

    # ---------------------------------------------------------------- api

    def _call(self, method: str, wait: float = 15.0, **payload):
        token = self.secret("bot_token")
        if not token:
            raise Failed("No bot token yet.")
        response = self.http("POST", f"{API}/bot{token}/{method}",
                             json=payload, timeout=wait)
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code == 401:
            raise Failed("Telegram refused the bot token. Paste it again "
                         "from @BotFather.")
        if not body.get("ok"):
            raise Failed(f"Telegram said: {body.get('description') or response.status_code}")
        return body["result"]

    def test(self) -> str:
        me = self._call("getMe")
        self.bot_name = me.get("username", "")
        if self.owner():
            return f"@{self.bot_name} is paired with your chat."
        return f"@{self.bot_name} works. Send it /start from Telegram to pair."

    def send(self, text: str) -> None:
        chat = self.owner()
        if not chat:
            raise Failed("No chat is paired yet.")
        text = text.strip() or "…"
        for start in range(0, len(text), LIMIT):
            self._call("sendMessage", chat_id=chat,
                       text=text[start:start + LIMIT])

    def notify(self, text: str, kind: str) -> None:
        """Push a ring to the phone, if connected and wanted. Never raises."""
        if not self.ready() or not self.conf().get(f"forward_{kind}", True):
            return
        try:
            self.send(text)
            audit("conn.telegram", {"notify": kind}, "ok", text)
        except Failed as e:
            audit("conn.telegram", {"notify": kind}, "error", str(e))
            log.warning("could not forward %s to Telegram: %s", kind, e)

    def pair(self, chat_id: int) -> None:
        candidate = self.candidates.get(chat_id)
        if candidate is None:
            raise Failed("That chat has not asked to pair. Send /start to the "
                         "bot again.")
        self.save(chat_id=chat_id)
        self.candidates.clear()
        audit("conn.telegram", {"pair": chat_id}, "ok", json.dumps(candidate))
        self.status = Status(True, f"Paired with {candidate['name']}.")
        try:
            self.send("Paired. You can talk to me here now.")
        except Failed as e:
            log.warning("paired, but the welcome did not go: %s", e)

    def disconnect(self) -> None:
        super().disconnect()
        self.save(chat_id=0)
        self.candidates.clear()
        self.bot_name = ""

    # ------------------------------------------------------------ polling

    def _offset(self) -> int:
        try:
            return int(json.loads(OFFSET_FILE.read_text())["offset"])
        except (FileNotFoundError, ValueError, KeyError, TypeError):
            return 0

    def _keep_offset(self, offset: int) -> None:
        # Kept on disk so a restart does not answer the same message twice.
        OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
        OFFSET_FILE.write_text(json.dumps({"offset": offset}))

    def poll_once(self, timeout: int = LONG_POLL) -> list[dict]:
        updates = self._call("getUpdates", wait=timeout + 10,
                             offset=self._offset(), timeout=timeout,
                             allowed_updates=["message"])
        if updates:
            self._keep_offset(max(u["update_id"] for u in updates) + 1)
        return updates

    async def handle(self, update: dict) -> None:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = int(chat.get("id") or 0)
        text = (message.get("text") or "").strip()
        if not chat_id or not text:
            return
        # Groups and channels never: a bot added to a group would hear
        # everyone in it.
        if chat.get("type") != "private":
            audit("conn.telegram", {"chat": chat_id}, "refused",
                  "not a private chat")
            return

        owner = self.owner()
        if owner == 0:
            if text.startswith("/start"):
                name = " ".join(p for p in (chat.get("first_name"),
                                            chat.get("last_name")) if p)
                self.candidates[chat_id] = {
                    "name": name or "unknown",
                    "username": chat.get("username", ""),
                    "at": time.time()}
                audit("conn.telegram", {"chat": chat_id}, "pairing",
                      name)
                events.publish("connections")
                await asyncio.to_thread(
                    self._call, "sendMessage", chat_id=chat_id,
                    text="Seen. Confirm this chat in Naka's panel, on the PC, "
                         "under Connections.")
            return

        if chat_id != owner:
            # Unanswered: a reply would tell a stranger the bot is alive.
            audit("conn.telegram", {"chat": chat_id}, "refused",
                  "not the paired chat")
            return

        if text.startswith("/start"):
            await asyncio.to_thread(self.send, "Already paired. Just write.")
            return
        if self.on_message is None:
            return
        audit("conn.telegram", {"from": chat_id}, "received", text)
        try:
            reply = await self.on_message(text)
        except Exception as e:
            log.exception("a Telegram turn failed")
            reply = f"Something went wrong on my side: {e}"
        await asyncio.to_thread(self.send, reply)

    async def run(self) -> None:
        """Poll for as long as the server runs, whenever it is switched on."""
        while True:
            if not (self.on() and self.secret("bot_token")):
                await asyncio.sleep(3)
                continue
            try:
                updates = await asyncio.to_thread(self.poll_once)
                for update in updates:
                    await self.handle(update)
            except asyncio.CancelledError:
                raise
            except Failed as e:
                self.status = Status(False, str(e))
                log.warning("Telegram poll failed: %s", e)
                await asyncio.sleep(15)
            except Exception:
                log.exception("Telegram poller stumbled, continuing")
                await asyncio.sleep(15)


telegram = Telegram()


@tool(
    description=(
        "Send a text message to the user's phone, through Telegram. Use it "
        "when they ask to be sent something: a note, a list, an address. "
        "Read the thing first, then send its text here."),
    parameters={"text": {"type": "string",
                         "description": "The message, as it should read."}},
    required=["text"],
    power="conn.telegram",
    label="Send to Telegram",
    summary="Sends you a message on your phone.",
)
def send_telegram(text: str):
    telegram.send(text)
    return "Sent to their phone."
