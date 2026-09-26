"""Where tokens live: Windows Credential Manager, never settings.toml.

settings.toml is a plain file the panel rewrites, that people copy between
machines and paste into bug reports. A bot token in it is a bot anyone with
the file can drive, and a refresh token is their calendar. Credential Manager
ties them to this Windows account instead, and shows them under "Naka" in
the control panel where they can be removed by hand.

Read through a small cache: the gate on every connection tool asks whether
its token exists, on every turn, and the vault is not free to open.
"""

import logging
import threading

log = logging.getLogger("naka.secrets")

SERVICE = "Naka"


class _Keyring:
    def get(self, account: str) -> str | None:
        import keyring
        return keyring.get_password(SERVICE, account)

    def put(self, account: str, value: str) -> None:
        import keyring
        keyring.set_password(SERVICE, account, value)

    def drop(self, account: str) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError
        try:
            keyring.delete_password(SERVICE, account)
        except PasswordDeleteError:
            pass


class Memory:
    """A vault that forgets at exit. For the tests and the bench, which must
    never write into the person's real Credential Manager."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, account: str) -> str | None:
        return self.values.get(account)

    def put(self, account: str, value: str) -> None:
        self.values[account] = value

    def drop(self, account: str) -> None:
        self.values.pop(account, None)


_store = _Keyring()
_cache: dict[str, str | None] = {}
_lock = threading.Lock()


def use(store) -> None:
    """Swap the vault, and forget everything read from the old one."""
    global _store
    with _lock:
        _store = store
        _cache.clear()


def _account(connection: str, key: str) -> str:
    return f"{connection}.{key}"


def get(connection: str, key: str) -> str | None:
    account = _account(connection, key)
    with _lock:
        if account in _cache:
            return _cache[account]
    try:
        value = _store.get(account)
    except Exception as e:  # no backend, a locked vault: say so, carry on
        log.error("could not read %s from the credential store: %s", account, e)
        return None
    with _lock:
        _cache[account] = value
    return value


def put(connection: str, key: str, value: str) -> None:
    account = _account(connection, key)
    _store.put(account, value)
    with _lock:
        _cache[account] = value


def drop(connection: str, key: str) -> None:
    account = _account(connection, key)
    _store.drop(account)
    with _lock:
        _cache[account] = None
