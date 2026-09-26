"""The panel's side of connections: cards, secrets, tests, sign-ins.

Switches and plain settings go through PATCH /settings like every other
field (panel.py knows their keys). What is here is what a field cannot do:
secrets that must not be read back, a test that goes out to the service, an
OAuth round trip, and pairing a Telegram chat.
"""

import asyncio
import html
import logging
import webbrowser

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .. import events
from ..tools.registry import audit
from . import ALL, oauth, secrets
from .base import Connection, Failed, Status

log = logging.getLogger("naka.connections")

router = APIRouter()


def _find(name: str) -> Connection:
    connection = ALL.get(name)
    if connection is None:
        raise HTTPException(status_code=404, detail=f"no connection {name!r}")
    return connection


def _changed(connection: Connection) -> dict:
    events.publish("connections")
    return connection.view()


@router.get("/connections")
async def connections_list():
    return {"connections": [c.view() for c in ALL.values()],
            "redirect_uri": oauth.redirect_uri()}


class SecretIn(BaseModel):
    value: str


@router.put("/connections/{name}/secrets/{key}")
async def secret_save(name: str, key: str, body: SecretIn):
    connection = _find(name)
    spec = next((s for s in connection.secrets if s.key == key and s.typed),
                None)
    if spec is None:
        raise HTTPException(status_code=400, detail=f"no secret {key!r} here")
    value = body.value.strip()
    if not value:
        raise HTTPException(status_code=400, detail=f"{spec.label} is empty")
    try:
        await asyncio.to_thread(secrets.put, name, key, value)
    except Exception as e:
        raise HTTPException(status_code=500,
                            detail=f"Could not store it: {e}") from e
    # The value itself never goes in the audit log.
    audit(f"conn.{name}", {"secret": key}, "ok", "stored")
    connection.status = Status(None, f"{spec.label} saved.")
    return _changed(connection)


@router.post("/connections/{name}/test")
async def connection_test(name: str):
    connection = _find(name)
    status = await asyncio.to_thread(connection.check)
    audit(f"conn.{name}", {"test": True}, "ok" if status.ok else "error",
          status.text)
    return _changed(connection)


@router.post("/connections/{name}/connect")
async def connection_connect(name: str):
    connection = _find(name)
    try:
        started = await asyncio.to_thread(connection.connect)
    except Failed as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    url = started.get("url")
    if url:
        # The server runs as the person, on their desktop: their default
        # browser is where they are signed in. The panel's WebView is not.
        opened = await asyncio.to_thread(webbrowser.open, url)
        audit(f"conn.{name}", {"connect": True}, "started",
              "browser opened" if opened else "browser did not open")
    return {**_changed(connection), "url": url}


@router.post("/connections/{name}/disconnect")
async def connection_disconnect(name: str):
    connection = _find(name)
    await asyncio.to_thread(connection.disconnect)
    audit(f"conn.{name}", {"disconnect": True}, "ok")
    return _changed(connection)


class PairIn(BaseModel):
    chat_id: int


@router.post("/connections/telegram/pair")
async def telegram_pair(body: PairIn):
    connection = ALL["telegram"]
    try:
        await asyncio.to_thread(connection.pair, body.chat_id)
    except Failed as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _changed(connection)


PAGE = """<!doctype html><meta charset="utf-8"><title>Naka</title>
<style>body{{font:16px system-ui,sans-serif;background:#0b0c10;color:#e8e8ea;
display:grid;place-items:center;height:100vh;margin:0}}main{{max-width:28rem;
padding:2rem;text-align:center}}h1{{font-size:1.3rem}}p{{color:#a0a3ab}}</style>
<main><h1>{title}</h1><p>{text}</p></main>"""


@router.get("/connections/callback", response_class=HTMLResponse)
async def oauth_callback(state: str = "", code: str = "", error: str = ""):
    """Where Google and Spotify send the browser back to."""
    entry = oauth.take(state)
    if entry is None:
        return HTMLResponse(PAGE.format(
            title="That sign-in has expired",
            text="Press Connect in Naka's panel again."), status_code=400)
    connection = ALL[entry["connection"]]
    if error or not code:
        connection.status = Status(False, f"Not connected: {error or 'no code'}.")
        audit(f"conn.{connection.name}", {"callback": True}, "declined",
              error)
        events.publish("connections")
        return HTMLResponse(PAGE.format(
            title="Not connected",
            text=html.escape(f"{connection.label} said: {error or 'no code'}.")))
    try:
        await asyncio.to_thread(connection.finish, code, entry["verifier"])
        status = await asyncio.to_thread(connection.check)
    except Failed as e:
        status = connection.status = Status(False, str(e))
    audit(f"conn.{connection.name}", {"callback": True},
          "ok" if status.ok else "error", status.text)
    events.publish("connections")
    if not status.ok:
        return HTMLResponse(PAGE.format(title="Not connected",
                                        text=html.escape(status.text)),
                            status_code=400)
    return HTMLResponse(PAGE.format(
        title=f"{html.escape(connection.label)} is connected",
        text="You can close this tab and go back to Naka."))
