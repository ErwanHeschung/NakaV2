"""What every connection has in common.

A connection is a service outside this PC: its settings in
[connections.<name>], its secrets in the credential store, a way to check it
works, and the tools it lends her. Its tools carry power="conn.<name>", so
the registry offers them only while the connection is both switched on and
set up.
"""

import logging
import time
from dataclasses import dataclass, field

import httpx

from .. import settings
from . import net, secrets

log = logging.getLogger("naka.connections")


@dataclass(frozen=True)
class Setting:
    """A field on the connection's card. Stored in settings.toml."""
    key: str
    label: str
    kind: str = "text"  # text | int | bool | choice
    help: str = ""
    options: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Secret:
    """A value kept in the credential store.

    `typed` is whether the person enters it. A refresh token is a secret the
    sign-in produces, which the card shows as connected or not, never as a
    box to paste into.
    """
    key: str
    label: str
    help: str = ""
    typed: bool = True


@dataclass(frozen=True)
class Step:
    """One line of the setup guide, with a link when there is somewhere to go."""
    text: str
    url: str = ""


@dataclass
class Status:
    ok: bool | None
    text: str
    at: float = field(default_factory=time.time)

    def view(self) -> dict:
        return {"ok": self.ok, "text": self.text, "at": self.at}


class Failed(RuntimeError):
    """Something the person should read: said plainly, never a traceback."""


class Connection:
    name = ""
    label = ""
    icon = "link"
    blurb = ""
    settings: list[Setting] = []
    secrets: list[Secret] = []
    # Whether Connect opens a sign-in page rather than checking a token.
    signs_in = False

    def __init__(self) -> None:
        self.status = Status(None, "Not checked yet.")

    # ---------------------------------------------------------- settings

    def conf(self) -> dict:
        return settings.CONNECTIONS.get(self.name, {})

    def on(self) -> bool:
        return bool(self.conf().get("enabled"))

    def secret(self, key: str) -> str | None:
        return secrets.get(self.name, key)

    def configured(self) -> bool:
        """Whether it has what it needs to work. Cheap: checked every turn."""
        return all(self.secret(s.key) for s in self.secrets)

    def ready(self) -> bool:
        return self.on() and self.configured()

    def save(self, **values) -> None:
        settings.save({f"connections.{self.name}.{k}": v
                       for k, v in values.items()})

    def guide(self) -> list[Step]:
        return []

    # ----------------------------------------------------------- network

    def http(self, method: str, url: str, **kwargs):
        try:
            return net.request(self.name, method, url, **kwargs)
        except net.Off as e:
            raise Failed(f"{self.label} is switched off.") from e
        except httpx.HTTPError as e:  # DNS, refused, timeout
            raise Failed(f"Could not reach {self.label}: {e}") from e

    # ----------------------------------------------------------- actions

    def check(self) -> Status:
        """What the panel's Test button does. Must not raise."""
        if not self.on():
            self.status = Status(None, "Switched off.")
            return self.status
        try:
            self.status = Status(True, self.test())
        except Failed as e:
            self.status = Status(False, str(e))
        except Exception as e:
            log.exception("%s test failed", self.name)
            self.status = Status(False, f"Unexpected error: {e}")
        return self.status

    def test(self) -> str:
        """A short sentence saying it works. Raises Failed if it does not."""
        raise NotImplementedError

    def connect(self) -> dict:
        """Start using it: a sign-in URL for OAuth ones, a test otherwise."""
        return {"status": self.check().view()}

    def finish(self, code: str, verifier: str) -> None:
        """Complete an OAuth sign-in. Only for connections that sign in."""
        raise NotImplementedError

    def disconnect(self) -> None:
        for s in self.secrets:
            secrets.drop(self.name, s.key)
        self.status = Status(None, "Disconnected.")

    def extra(self) -> dict:
        """Anything else the card needs to show."""
        return {}

    def view(self) -> dict:
        return {
            "name": self.name, "label": self.label, "icon": self.icon,
            "blurb": self.blurb, "on": self.on(),
            "configured": self.configured(), "ready": self.ready(),
            "signs_in": self.signs_in,
            "guide": [{"text": s.text, "url": s.url} for s in self.guide()],
            "fields": [
                {"key": f"settings.connections.{self.name}.{s.key}",
                 "label": s.label, "kind": s.kind, "help": s.help,
                 "options": s.options, "value": self.conf().get(s.key)}
                for s in self.settings
            ],
            "secrets": [
                {"key": s.key, "label": s.label, "help": s.help,
                 "typed": s.typed, "set": bool(self.secret(s.key))}
                for s in self.secrets
            ],
            "status": self.status.view(),
            **self.extra(),
        }
