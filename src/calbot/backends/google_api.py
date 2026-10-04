"""Direct Google Calendar API v3 backend (spec §5.3 A).

The google client is synchronous, so every call runs in a worker thread. A fresh service
object is built per call because googleapiclient service objects are not thread-safe."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .base import AuthExpired, BackendError, CalendarInfo, Event, EventNotFound, EventPatch, NewEvent

log = logging.getLogger(__name__)

# Narrowest set that covers events CRUD + checking write access via calendarList.get.
# Conflict checks use events.list, so the broader freebusy scope is not needed.
SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
]


class GoogleApiBackend:
    name = "google_api"

    def __init__(self, token_file: Path, calendar_id: str, tz: ZoneInfo):
        self.token_file = token_file
        self.calendar_id = calendar_id
        self.tz = tz
        self._lock = threading.Lock()
        self._creds: Credentials | None = None

    # ---------- auth ----------
    def _credentials(self) -> Credentials:
        with self._lock:
            if self._creds is None:
                if not self.token_file.exists():
                    raise AuthExpired(f"No Google token at {self.token_file}. Run scripts/google_auth.py.")
                self._creds = Credentials.from_authorized_user_file(str(self.token_file), SCOPES)
            if not self._creds.valid:
                try:
                    self._creds.refresh(Request())
                except RefreshError as e:
                    raise AuthExpired(f"Google auth expired or revoked ({e.__class__.__name__}). Re-run scripts/google_auth.py.") from None
                except TransportError as e:
                    raise BackendError(f"Network error talking to Google: {e}") from None
                self.token_file.write_text(self._creds.to_json())
            return self._creds

    def _service(self):
        return build("calendar", "v3", credentials=self._credentials(), cache_discovery=False)

    async def _run(self, fn):
        def wrapped():
            try:
                return fn(self._service())
            except HttpError as e:
                status = e.resp.status
                if status in (404, 410):
                    raise EventNotFound("Event not found (it may have been deleted)") from None
                if status == 401:
                    raise AuthExpired("Google rejected the credentials. Re-run scripts/google_auth.py.") from None
                raise BackendError(f"Google Calendar error {status}: {e.reason}") from None
            except RefreshError:
                raise AuthExpired("Google auth expired or revoked. Re-run scripts/google_auth.py.") from None
        return await asyncio.to_thread(wrapped)

    # ---------- conversions ----------
    def _parse_when(self, w: dict) -> datetime:
        if "dateTime" in w:
            return datetime.fromisoformat(w["dateTime"]).astimezone(self.tz)
        return datetime.combine(date.fromisoformat(w["date"]), time(0), self.tz)

    def _to_event(self, raw: dict) -> Event:
        return Event(
            id=raw["id"],
            title=raw.get("summary", "(no title)"),
            start=self._parse_when(raw["start"]),
            end=self._parse_when(raw["end"]),
            location=raw.get("location", ""),
            description=raw.get("description", ""),
            recurring=bool(raw.get("recurringEventId") or raw.get("recurrence")),
            updated=raw.get("updated", ""),
            html_link=raw.get("htmlLink", ""),
        )

    @staticmethod
    def _when(dt: datetime, tz_name: str) -> dict:
        return {"dateTime": dt.isoformat(), "timeZone": tz_name}

    # ---------- API ----------
    async def list_calendars(self) -> list[CalendarInfo]:
        raw = await self._run(lambda s: s.calendarList().list(minAccessRole="writer").execute())
        return [CalendarInfo(id=c["id"], summary=c.get("summary", ""), access_role=c.get("accessRole", ""),
                             primary=c.get("primary", False)) for c in raw.get("items", [])]

    async def check_write_access(self) -> str:
        raw = await self._run(lambda s: s.calendarList().get(calendarId=self.calendar_id).execute())
        return raw.get("accessRole", "")

    async def list_events(self, time_min, time_max, query=None, limit=25) -> list[Event]:
        def call(s):
            kw = dict(calendarId=self.calendar_id, timeMin=time_min.isoformat(), timeMax=time_max.isoformat(),
                      singleEvents=True, orderBy="startTime", maxResults=limit)
            if query:
                kw["q"] = query
            return s.events().list(**kw).execute()
        raw = await self._run(call)
        return [self._to_event(e) for e in raw.get("items", []) if e.get("status") != "cancelled"]

    async def get_event(self, event_id: str) -> Event:
        raw = await self._run(lambda s: s.events().get(calendarId=self.calendar_id, eventId=event_id).execute())
        if raw.get("status") == "cancelled":
            raise EventNotFound("Event was deleted")
        return self._to_event(raw)

    async def create_event(self, event: NewEvent, event_id: str) -> Event:
        body = {
            "id": event_id,  # deterministic → idempotent (base32hex chars; a uuid4 hex fits)
            "summary": event.title,
            "start": self._when(event.start, event.timezone),
            "end": self._when(event.end, event.timezone),
        }
        if event.location:
            body["location"] = event.location
        if event.description:
            body["description"] = event.description
        if event.recurrence:
            body["recurrence"] = event.recurrence

        def call(s):
            try:
                return s.events().insert(calendarId=self.calendar_id, body=body, sendUpdates="none").execute()
            except HttpError as e:
                if e.resp.status == 409:  # already created by an earlier tap/retry
                    return s.events().get(calendarId=self.calendar_id, eventId=event_id).execute()
                raise
        return self._to_event(await self._run(call))

    async def update_event(self, event_id: str, patch: EventPatch) -> Event:
        body: dict = {}
        if patch.title is not None:
            body["summary"] = patch.title
        if patch.location is not None:
            body["location"] = patch.location
        if patch.start is not None:
            body["start"] = self._when(patch.start, patch.timezone or str(self.tz))
        if patch.end is not None:
            body["end"] = self._when(patch.end, patch.timezone or str(self.tz))
        raw = await self._run(lambda s: s.events().patch(
            calendarId=self.calendar_id, eventId=event_id, body=body, sendUpdates="none").execute())
        return self._to_event(raw)

    async def delete_event(self, event_id: str) -> None:
        await self._run(lambda s: s.events().delete(
            calendarId=self.calendar_id, eventId=event_id, sendUpdates="none").execute())

    def token_age_days(self) -> float | None:
        """Days since scripts/google_auth.py last ran (it writes a .meta.json sidecar)."""
        meta = self.token_file.with_suffix(".meta.json")
        if not meta.exists():
            return None
        authorized_at = datetime.fromisoformat(json.loads(meta.read_text())["authorized_at"])
        return (datetime.now(authorized_at.tzinfo) - authorized_at).total_seconds() / 86400
