"""In-memory backend for tests, evals and dry runs (BACKEND=fake)."""
from __future__ import annotations

from datetime import datetime, timezone

from .base import AuthExpired, CalendarInfo, Event, EventNotFound, EventPatch, NewEvent


class FakeBackend:
    name = "fake"

    def __init__(self, events: list[Event] | None = None):
        self.events: dict[str, Event] = {e.id: e for e in events or []}
        self.writes: list[tuple[str, str]] = []  # (op, event_id): lets tests assert "no write before ✅"
        self.auth_broken = False
        self._clock = 0

    def _check(self):
        if self.auth_broken:
            raise AuthExpired("Google auth expired")

    def _stamp(self) -> str:
        self._clock += 1
        return datetime.fromtimestamp(1_700_000_000 + self._clock, timezone.utc).isoformat()

    async def list_calendars(self) -> list[CalendarInfo]:
        self._check()
        return [CalendarInfo(id="primary", summary="Fake calendar", access_role="owner", primary=True)]

    async def list_events(self, time_min, time_max, query=None, limit=25) -> list[Event]:
        self._check()
        out = [e for e in self.events.values() if e.start < time_max and e.end > time_min]
        if query:
            q = query.casefold()
            out = [e for e in out if q in f"{e.title} {e.location} {e.description}".casefold()]
        return sorted(out, key=lambda e: e.start)[:limit]

    async def get_event(self, event_id: str) -> Event:
        self._check()
        try:
            return self.events[event_id]
        except KeyError:
            raise EventNotFound(f"Event {event_id} not found") from None

    async def create_event(self, event: NewEvent, event_id: str) -> Event:
        self._check()
        if event_id in self.events:
            return self.events[event_id]
        e = Event(id=event_id, title=event.title, start=event.start, end=event.end,
                  location=event.location, description=event.description,
                  recurring=bool(event.recurrence), updated=self._stamp(),
                  html_link=f"https://calendar.example/{event_id}")
        self.events[event_id] = e
        self.writes.append(("create", event_id))
        return e

    async def update_event(self, event_id: str, patch: EventPatch) -> Event:
        e = await self.get_event(event_id)
        for f in ("title", "start", "end", "location"):
            v = getattr(patch, f)
            if v is not None:
                setattr(e, f, v)
        e.updated = self._stamp()
        self.writes.append(("update", event_id))
        return e

    async def delete_event(self, event_id: str) -> None:
        await self.get_event(event_id)
        del self.events[event_id]
        self.writes.append(("delete", event_id))
