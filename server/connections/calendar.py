"""Google Calendar, read and write, through the person's own Google project.

Their own OAuth client rather than one shipped with Naka: a shared client
would need Google's verification and would put every user's calendar behind
one app's quota and one app's keys. The consent screen has to be published
"In production" (unverified is fine for one person): left in "Testing",
Google revokes the refresh token after seven days and she quietly loses the
calendar every week.

Only the primary calendar, and only its events (the calendar.events scope):
enough to read the day and add, move or remove things, and nothing about
the account beyond that.
"""

import logging
import time
from datetime import date, datetime, timedelta

from ..tools.registry import tool
from . import oauth
from .base import Connection, Failed, Secret, Setting, Status, Step

log = logging.getLogger("naka.calendar")

AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
EVENTS = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
SCOPE = "https://www.googleapis.com/auth/calendar.events"
MAX_DAYS = 31


class Calendar(Connection):
    name = "calendar"
    label = "Google Calendar"
    icon = "calendar"
    blurb = ("Read your day, add events, move or delete them. Moving and "
             "deleting always ask first.")
    signs_in = True
    settings = [
        Setting("client_id", "Client ID",
                help="From your OAuth client, ends in "
                     ".apps.googleusercontent.com."),
    ]
    secrets = [
        Secret("client_secret", "Client secret",
               "From the same OAuth client. Kept in Windows Credential "
               "Manager."),
        Secret("refresh_token", "Google account", typed=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._access: tuple[str, float] | None = None

    def guide(self) -> list[Step]:
        return [
            Step("Create a project in the Google Cloud console.",
                 "https://console.cloud.google.com/projectcreate"),
            Step("Enable the Google Calendar API for it.",
                 "https://console.cloud.google.com/apis/library/calendar-json.googleapis.com"),
            Step("OAuth consent screen: user type External, add yourself, then "
                 "Publish app so it reads In production. Left in Testing, "
                 "Google signs you out every 7 days.",
                 "https://console.cloud.google.com/auth/audience"),
            Step("Credentials: create an OAuth client ID of type Desktop app. "
                 "Copy its client ID and secret here.",
                 "https://console.cloud.google.com/auth/clients"),
            Step("Press Connect and allow access in the browser. Google warns "
                 "the app is unverified: it's yours, choose Continue."),
        ]

    def configured(self) -> bool:
        return bool(self.conf().get("client_id")) and \
            bool(self.secret("client_secret")) and \
            bool(self.secret("refresh_token"))

    # -------------------------------------------------------------- oauth

    def connect(self) -> dict:
        if not self.on():
            raise Failed("Switch it on first.")
        client_id = str(self.conf().get("client_id", "")).strip()
        if not client_id or not self.secret("client_secret"):
            raise Failed("Save the client ID and secret first.")
        url = oauth.begin(self.name, AUTHORIZE, {
            "client_id": client_id, "scope": SCOPE,
            # offline and consent: without both, Google gives no refresh
            # token on a second sign-in, and the first access token lasts
            # an hour.
            "access_type": "offline", "prompt": "consent"})
        self.status = Status(None, "Waiting for you in the browser…")
        return {"url": url}

    def finish(self, code: str, verifier: str) -> None:
        response = self.http("POST", TOKEN, data={
            "code": code, "client_id": self.conf().get("client_id", ""),
            "client_secret": self.secret("client_secret") or "",
            "redirect_uri": oauth.redirect_uri(),
            "grant_type": "authorization_code", "code_verifier": verifier})
        body = response.json()
        if response.status_code != 200 or "refresh_token" not in body:
            raise Failed("Google did not hand over access: "
                         f"{body.get('error_description') or body.get('error') or response.status_code}")
        from . import secrets
        secrets.put(self.name, "refresh_token", body["refresh_token"])
        self._access = (body["access_token"],
                        time.time() + body.get("expires_in", 3600) - 60)

    def token(self) -> str:
        if self._access and self._access[1] > time.time():
            return self._access[0]
        refresh = self.secret("refresh_token")
        if not refresh:
            raise Failed("Google Calendar is not connected.")
        response = self.http("POST", TOKEN, data={
            "client_id": self.conf().get("client_id", ""),
            "client_secret": self.secret("client_secret") or "",
            "refresh_token": refresh, "grant_type": "refresh_token"})
        body = response.json()
        if response.status_code != 200:
            if body.get("error") == "invalid_grant":
                # Revoked, expired, or the consent screen is still in
                # Testing. Dropping it makes the card say "not connected"
                # rather than failing the same way on every question.
                from . import secrets
                secrets.drop(self.name, "refresh_token")
                self.status = Status(False, "Google withdrew access. Connect "
                                     "again; if it keeps happening, publish "
                                     "the consent screen.")
                raise Failed("Google withdrew access to the calendar; it "
                             "needs connecting again in the panel.")
            raise Failed(f"Google refused: {body.get('error', response.status_code)}")
        self._access = (body["access_token"],
                        time.time() + body.get("expires_in", 3600) - 60)
        return self._access[0]

    def api(self, method: str, url: str, **kwargs):
        headers = {"Authorization": f"Bearer {self.token()}"}
        response = self.http(method, url, headers=headers, **kwargs)
        if response.status_code == 404:
            raise Failed("No such event. List the day again for its id.")
        if response.status_code >= 400:
            try:
                detail = response.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                detail = response.status_code
            raise Failed(f"Google Calendar said: {detail}")
        return response.json() if response.content else {}

    def disconnect(self) -> None:
        from . import secrets
        secrets.drop(self.name, "refresh_token")
        self._access = None
        self.status = Status(None, "Disconnected. The client ID and secret "
                             "are kept.")

    def test(self) -> str:
        if not self.secret("refresh_token"):
            raise Failed("Not connected yet: press Connect.")
        today = self.between(date.today(), date.today())
        return f"Connected. {len(today)} event(s) today."

    # -------------------------------------------------------------- events

    def between(self, first: date, last: date) -> list[dict]:
        start = datetime.combine(first, datetime.min.time()).astimezone()
        end = datetime.combine(last + timedelta(days=1),
                               datetime.min.time()).astimezone()
        body = self.api("GET", EVENTS, params={
            "timeMin": start.isoformat(), "timeMax": end.isoformat(),
            "singleEvents": "true", "orderBy": "startTime",
            "maxResults": 50})
        return body.get("items", [])


calendar = Calendar()


def _day(text: str) -> date:
    word = (text or "today").strip().lower()
    today = date.today()
    if word == "today":
        return today
    if word == "tomorrow":
        return today + timedelta(days=1)
    try:
        return date.fromisoformat(word)
    except ValueError:
        raise ValueError(f"{text!r} is not a date: use YYYY-MM-DD, today or "
                         "tomorrow") from None


def _clock(text: str) -> tuple[int, int]:
    from ..tools.reminders import _parse_time
    return _parse_time(text)


def _moment(day: date, at: str) -> datetime:
    hour, minute = _clock(at)
    return datetime.combine(day, datetime.min.time()).replace(
        hour=hour, minute=minute).astimezone()


def _when(event: dict) -> str:
    start, end = event.get("start", {}), event.get("end", {})
    if "date" in start:
        return f"{date.fromisoformat(start['date']):%a %d %b}, all day"
    begins = datetime.fromisoformat(start["dateTime"]).astimezone()
    line = f"{begins:%a %d %b %H:%M}"
    if "dateTime" in end:
        line += f"-{datetime.fromisoformat(end['dateTime']).astimezone():%H:%M}"
    return line


def _line(event: dict) -> str:
    line = f"[{event['id']}] {_when(event)} {event.get('summary') or '(no title)'}"
    if event.get("location"):
        line += f" at {event['location']}"
    return line


@tool(
    description=(
        "List the events in the user's Google Calendar for a day, or from "
        "one day to another. Each comes with an id in brackets, which "
        "calendar_move and calendar_delete need."),
    parameters={
        "day": {"type": "string",
                "description": "YYYY-MM-DD, 'today' or 'tomorrow'."},
        "until": {"type": "string",
                  "description": "Optional last day of a range, YYYY-MM-DD."},
    },
    required=["day"],
    power="conn.calendar",
    label="Read the calendar",
    summary="What's on for a day or a range.",
    # Titles and descriptions of invitations are written by whoever sent
    # them.
    untrusted=True,
)
def calendar_events(day: str, until: str = ""):
    first = _day(day)
    last = _day(until) if until else first
    if last < first:
        first, last = last, first
    if (last - first).days > MAX_DAYS:
        raise ValueError(f"ask for {MAX_DAYS} days at most")
    events = calendar.between(first, last)
    span = f"{first:%A %d %B}" + (f" to {last:%A %d %B}" if last != first else "")
    if not events:
        return f"Nothing in the calendar for {span}."
    return f"{span}: " + "; ".join(_line(e) for e in events)


@tool(
    description="Add an event to the user's Google Calendar.",
    parameters={
        "title": {"type": "string", "description": "What it is."},
        "day": {"type": "string",
                "description": "YYYY-MM-DD, 'today' or 'tomorrow'."},
        "at": {"type": "string",
               "description": "Start time, 24-hour HH:MM. Leave out for an "
                              "all-day event."},
        "minutes": {"type": "integer",
                    "description": "How long, default 60."},
        "location": {"type": "string", "description": "Optional."},
    },
    required=["title", "day"],
    power="conn.calendar",
    # Asks first only when the turn has been steered by text from outside:
    # an invitation's description, a web page, a Telegram message.
    confirm=lambda arguments, tainted: tainted,
    label="Add an event",
    summary="Puts something in your calendar.",
)
def calendar_create(title: str, day: str, at: str = "", minutes: int = 60,
                    location: str = ""):
    when = _day(day)
    body: dict = {"summary": title.strip() or "Event"}
    if location:
        body["location"] = location
    if at:
        if not 1 <= minutes <= 24 * 60:
            raise ValueError("minutes must be between 1 and 1440")
        start = _moment(when, at)
        body["start"] = {"dateTime": start.isoformat()}
        body["end"] = {"dateTime": (start + timedelta(minutes=minutes)).isoformat()}
    else:
        body["start"] = {"date": when.isoformat()}
        body["end"] = {"date": (when + timedelta(days=1)).isoformat()}
    created = calendar.api("POST", EVENTS, json=body)
    return f"Added: {_line(created)}."


@tool(
    description="Move an event in the user's Google Calendar to a new day "
                "and time, keeping its length. Needs the id from "
                "calendar_events.",
    parameters={
        "event_id": {"type": "string", "description": "The id in brackets."},
        "day": {"type": "string",
                "description": "New day: YYYY-MM-DD, 'today' or 'tomorrow'."},
        "at": {"type": "string",
               "description": "New start time, 24-hour HH:MM."},
    },
    required=["event_id", "day", "at"],
    power="conn.calendar",
    label="Move an event",
    summary="Changes when something is.",
)
def calendar_move(event_id: str, day: str, at: str):
    event = calendar.api("GET", f"{EVENTS}/{event_id.strip('[] ')}")
    start = _moment(_day(day), at)
    old_start, old_end = event.get("start", {}), event.get("end", {})
    if "dateTime" in old_start and "dateTime" in old_end:
        length = (datetime.fromisoformat(old_end["dateTime"])
                  - datetime.fromisoformat(old_start["dateTime"]))
    else:
        length = timedelta(hours=1)
    moved = calendar.api("PATCH", f"{EVENTS}/{event['id']}", json={
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": (start + length).isoformat()}})
    return f"Moved: {_line(moved)}."


@tool(
    description="Delete an event from the user's Google Calendar. Needs the "
                "id from calendar_events.",
    parameters={"event_id": {"type": "string",
                             "description": "The id in brackets."}},
    required=["event_id"],
    power="conn.calendar",
    label="Delete an event",
    summary="Removes something from your calendar.",
)
def calendar_delete(event_id: str):
    ident = event_id.strip("[] ")
    event = calendar.api("GET", f"{EVENTS}/{ident}")
    calendar.api("DELETE", f"{EVENTS}/{ident}")
    return f"Deleted {event.get('summary') or 'the event'} ({_when(event)})."
