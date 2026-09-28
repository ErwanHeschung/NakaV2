"""The one way a connection reaches the network.

Everything outbound goes through request(), which refuses while the
connection's switch is off. That is the promise the panel makes ("nothing
goes out while it is off"), kept in one place rather than remembered in
every module.

The transport is swappable so the tests and the bench can answer with canned
responses: nothing in eval/ talks to Telegram, Google or Spotify.
"""

import logging

import httpx

from .. import settings

log = logging.getLogger("naka.connections")

TIMEOUT = 15.0
USER_AGENT = ("Naka/0.1 (personal voice assistant; "
              "+https://github.com/ErwanHeschung/NakaV2)")

transport: httpx.BaseTransport | None = None
_client: httpx.Client | None = None


class Off(RuntimeError):
    """The connection is switched off, so nothing was sent."""


def use(new_transport: httpx.BaseTransport | None) -> None:
    """Route every request through this transport from now on."""
    global transport, _client
    transport = new_transport
    if _client is not None:
        _client.close()
    _client = None


def client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(transport=transport, timeout=TIMEOUT,
                               headers={"User-Agent": USER_AGENT},
                               follow_redirects=False)
    return _client


def is_on(connection: str) -> bool:
    return bool(settings.CONNECTIONS.get(connection, {}).get("enabled"))


def request(connection: str, method: str, url: str, **kwargs) -> httpx.Response:
    if not is_on(connection):
        raise Off(f"the {connection} connection is switched off")
    return client().request(method, url, **kwargs)
