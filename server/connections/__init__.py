"""Opt-in connections to services outside this PC.

Each lives in its own module and lends her tools gated as power
"conn.<name>": offered only while the connection is switched on in the panel
and set up. All of them are off by default, nothing goes out while one is
off (net.request refuses), secrets live in the credential store, and every
call is audited like any other tool.
"""

from .base import Connection, Failed  # noqa: F401
from .calendar import calendar as _calendar
from .spotify import spotify as _spotify
from .telegram import telegram as _telegram
from .weather import weather as _weather

ALL: dict[str, Connection] = {c.name: c for c in
                              (_telegram, _calendar, _weather, _spotify)}


def get(name: str) -> Connection | None:
    return ALL.get(name)


def ready(name: str) -> bool:
    connection = ALL.get(name)
    return connection is not None and connection.ready()


def telegram():
    return _telegram
