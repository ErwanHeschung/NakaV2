"""Spotify playback, through the person's own developer app.

Since February 2026 the Web API's playback control needs Spotify Premium and
an app of one's own from the developer dashboard (development mode, up to
five users, a reduced set of endpoints). PKCE, so there is no client secret
to keep: the client id alone is not a credential.

Spotify plays on a device, not on the API. If nothing is playing anywhere,
play is sent to whichever device Spotify lists, preferring this computer, so
"play some jazz" works with the app merely open.
"""

import logging
import time

from ..tools.registry import tool
from . import oauth, secrets
from .base import Connection, Failed, Secret, Setting, Status, Step

log = logging.getLogger("naka.spotify")

AUTHORIZE = "https://accounts.spotify.com/authorize"
TOKEN = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
SCOPES = ("user-read-playback-state user-modify-playback-state "
          "user-read-currently-playing")
KINDS = ("track", "album", "playlist", "artist")


class Spotify(Connection):
    name = "spotify"
    label = "Spotify"
    icon = "music"
    blurb = ("Play, pause, skip and queue music on Spotify, by name. Needs "
             "Premium and your own developer app.")
    signs_in = True
    settings = [
        Setting("client_id", "Client ID",
                help="From your app on the Spotify developer dashboard."),
    ]
    secrets = [Secret("refresh_token", "Spotify account", typed=False)]

    def __init__(self) -> None:
        super().__init__()
        self._access: tuple[str, float] | None = None

    def guide(self) -> list[Step]:
        return [
            Step("Open the Spotify developer dashboard and create an app.",
                 "https://developer.spotify.com/dashboard"),
            Step(f"Redirect URI: {oauth.redirect_uri()} exactly. Tick Web "
                 "API. Save."),
            Step("Copy the app's Client ID here and save."),
            Step("Press Connect and allow access in the browser. Spotify "
                 "must be open on some device to play."),
        ]

    def configured(self) -> bool:
        return bool(self.conf().get("client_id")) and \
            bool(self.secret("refresh_token"))

    # -------------------------------------------------------------- oauth

    def connect(self) -> dict:
        if not self.on():
            raise Failed("Switch it on first.")
        client_id = str(self.conf().get("client_id", "")).strip()
        if not client_id:
            raise Failed("Save the client ID first.")
        url = oauth.begin(self.name, AUTHORIZE,
                          {"client_id": client_id, "scope": SCOPES})
        self.status = Status(None, "Waiting for you in the browser…")
        return {"url": url}

    def _tokens(self, data: dict) -> None:
        response = self.http("POST", TOKEN, data={
            **data, "client_id": self.conf().get("client_id", "")})
        body = response.json()
        if response.status_code != 200:
            if body.get("error") == "invalid_grant":
                secrets.drop(self.name, "refresh_token")
                self.status = Status(False, "Spotify withdrew access. "
                                     "Connect again.")
            raise Failed("Spotify refused: "
                         f"{body.get('error_description') or body.get('error') or response.status_code}")
        # Spotify may rotate the refresh token on every refresh; the old one
        # stops working once a new one has been issued.
        if body.get("refresh_token"):
            secrets.put(self.name, "refresh_token", body["refresh_token"])
        self._access = (body["access_token"],
                        time.time() + body.get("expires_in", 3600) - 60)

    def finish(self, code: str, verifier: str) -> None:
        self._tokens({"grant_type": "authorization_code", "code": code,
                      "redirect_uri": oauth.redirect_uri(),
                      "code_verifier": verifier})

    def token(self) -> str:
        if self._access and self._access[1] > time.time():
            return self._access[0]
        refresh = self.secret("refresh_token")
        if not refresh:
            raise Failed("Spotify is not connected.")
        self._tokens({"grant_type": "refresh_token", "refresh_token": refresh})
        return self._access[0]

    def disconnect(self) -> None:
        super().disconnect()
        self._access = None

    def api(self, method: str, path: str, **kwargs):
        response = self.http(method, f"{API}{path}", headers={
            "Authorization": f"Bearer {self.token()}"}, **kwargs)
        if response.status_code == 204 or not response.content:
            return response.status_code, {}
        try:
            body = response.json()
        except ValueError:
            # Some refusals come back as plain text, and the text is the only
            # place Spotify says why: "Active premium subscription required
            # for the owner of the app…" arrives this way.
            body = {"error": {"message": response.text.strip()[:300]}}
        return response.status_code, body

    @staticmethod
    def _reason(status: int, body: dict) -> str:
        error = body.get("error") if isinstance(body, dict) else None
        message = error.get("message") if isinstance(error, dict) else error
        if status == 403 and message:
            return f"Spotify refused: {message}"
        if status == 403:
            return ("Spotify refused. Playback control needs Premium, and "
                    "in development mode your account must be added under "
                    "User Management in the app's dashboard.")
        return f"Spotify said: {message or status}"

    def test(self) -> str:
        status, body = self.api("GET", "/me/player/devices")
        if status != 200:
            raise Failed(self._reason(status, body))
        devices = body.get("devices", [])
        if not devices:
            return "Connected. No Spotify app is open right now."
        return "Connected. Devices: " + ", ".join(d["name"] for d in devices)

    # ------------------------------------------------------------ playback

    def device(self) -> str | None:
        status, body = self.api("GET", "/me/player/devices")
        if status != 200:
            raise Failed(self._reason(status, body))
        devices = body.get("devices", [])
        if not devices:
            return None
        chosen = next((d for d in devices if d.get("is_active")), None) or \
            next((d for d in devices if d.get("type") == "Computer"), None) or \
            devices[0]
        return chosen["id"]

    def control(self, method: str, path: str, **kwargs) -> None:
        """A player command, retried on a device when nothing is active."""
        status, body = self.api(method, path, **kwargs)
        if status == 404:
            device = self.device()
            if device is None:
                raise Failed("Spotify isn't open anywhere. Open it on the PC "
                             "or the phone first.")
            params = dict(kwargs.pop("params", {}) or {})
            params["device_id"] = device
            status, body = self.api(method, path, params=params, **kwargs)
        if status >= 400:
            raise Failed(self._reason(status, body))

    def search(self, query: str, kind: str) -> dict:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        status, body = self.api("GET", "/search", params={
            "q": query, "type": kind, "limit": 5})
        if status != 200:
            raise Failed(self._reason(status, body))
        items = [i for i in (body.get(f"{kind}s", {}).get("items") or []) if i]
        if not items:
            raise Failed(f"Nothing on Spotify for {query!r}.")
        return items[0]


spotify = Spotify()


def _describe(item: dict) -> str:
    artists = ", ".join(a["name"] for a in item.get("artists", []))
    owner = (item.get("owner") or {}).get("display_name")
    by = artists or owner
    return f"{item['name']}" + (f" by {by}" if by else "")


@tool(
    description=(
        "Play music on the user's Spotify: a song, an album, a playlist or an "
        "artist, found by search."),
    parameters={
        "query": {"type": "string",
                  "description": "What to search for, e.g. 'Bohemian "
                                 "Rhapsody Queen' or 'lofi beats'."},
        "kind": {"type": "string", "enum": list(KINDS),
                 "description": "What it is; default track."},
    },
    required=["query"],
    power="conn.spotify",
    label="Play on Spotify",
    summary="A song, album, playlist or artist, by name.",
)
def spotify_play(query: str, kind: str = "track"):
    item = spotify.search(query, kind)
    body = ({"uris": [item["uri"]]} if kind == "track"
            else {"context_uri": item["uri"]})
    spotify.control("PUT", "/me/player/play", json=body)
    return f"Playing {_describe(item)}."


@tool(
    description="Pause Spotify.",
    parameters={},
    power="conn.spotify",
    label="Pause",
    summary="Pauses the music.",
)
def spotify_pause():
    spotify.control("PUT", "/me/player/pause")
    return "Paused."


@tool(
    description="Resume Spotify where it was paused.",
    parameters={},
    power="conn.spotify",
    label="Resume",
    summary="Carries on playing.",
)
def spotify_resume():
    spotify.control("PUT", "/me/player/play")
    return "Resumed."


@tool(
    description="Skip to the next song on Spotify.",
    parameters={},
    power="conn.spotify",
    label="Next song",
    summary="Skips ahead.",
)
def spotify_next():
    spotify.control("POST", "/me/player/next")
    return "Skipped."


@tool(
    description="The song playing right now on the user's Spotify. Use it "
                "for 'what song is this', 'who sings this', 'what's "
                "playing'.",
    parameters={},
    power="conn.spotify",
    label="What's playing",
    summary="The song on right now.",
)
def spotify_now_playing():
    status, body = spotify.api("GET", "/me/player/currently-playing")
    if status == 204 or not body or not body.get("item"):
        return "Nothing is playing on Spotify."
    if status != 200:
        raise Failed(spotify._reason(status, body))
    state = "Playing" if body.get("is_playing") else "Paused on"
    return f"{state} {_describe(body['item'])}."


@tool(
    description="Add a song to the Spotify queue, to play after the current "
                "one.",
    parameters={"query": {"type": "string",
                          "description": "The song to search for."}},
    required=["query"],
    power="conn.spotify",
    label="Queue a song",
    summary="Plays it next.",
)
def spotify_queue(query: str):
    item = spotify.search(query, "track")
    spotify.control("POST", "/me/player/queue", params={"uri": item["uri"]})
    return f"Queued {_describe(item)}."
