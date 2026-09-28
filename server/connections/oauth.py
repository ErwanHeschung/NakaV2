"""OAuth 2 with PKCE and a loopback redirect, for Google and Spotify.

The browser is sent to the provider, the person agrees there, and the
provider redirects back to Naka's own server on 127.0.0.1 with a one-time
code. PKCE binds that code to this process: whoever intercepts it cannot
trade it for a token without the verifier, which never leaves here.

Nothing is listening for the redirect except the server that is already
running, so there is no second port to open or close.
"""

import base64
import hashlib
import secrets
import threading
import time
from urllib.parse import urlencode

from .. import settings

# How long a started sign-in waits for the browser to come back.
EXPIRES = 600

_pending: dict[str, dict] = {}
_lock = threading.Lock()


def redirect_uri() -> str:
    """The address both providers are told to send the person back to.

    127.0.0.1 rather than localhost: Spotify refuses "localhost" outright,
    and Google treats any port on the loopback address as the same client.
    """
    return f"http://127.0.0.1:{settings.SERVER['port']}/connections/callback"


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def begin(connection: str, authorize: str, params: dict) -> str:
    """The URL to open in a browser to start signing in."""
    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(24)
    now = time.time()
    with _lock:
        for key in [k for k, v in _pending.items() if v["at"] < now - EXPIRES]:
            del _pending[key]
        _pending[state] = {"connection": connection, "verifier": verifier,
                           "at": now}
    query = {**params, "response_type": "code",
             "redirect_uri": redirect_uri(), "state": state,
             "code_challenge": _challenge(verifier),
             "code_challenge_method": "S256"}
    return f"{authorize}?{urlencode(query)}"


def take(state: str) -> dict | None:
    """The sign-in this state belongs to, once. An unknown or stale state is
    a redirect Naka did not start, and gets nothing."""
    with _lock:
        entry = _pending.pop(state, None)
    if entry is None or entry["at"] < time.time() - EXPIRES:
        return None
    return entry
