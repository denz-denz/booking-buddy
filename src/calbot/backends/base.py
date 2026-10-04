"""CalendarBackend protocol and plain data types (spec §5.3)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Protocol


class BackendError(Exception):
    """A calendar call failed; the message is safe to show to the model and the owner."""


class AuthExpired(BackendError):
    """Google refresh token is invalid/revoked; the owner must re-run scripts/google_auth.py."""


class EventNotFound(BackendError):
    pass


@dataclass
class Event:
    id: str
    title: str
    start: datetime
    end: datetime
    location: str = ""
    description: str = ""
    recurring: bool = False
    updated: str = ""  # server timestamp, used for the stale-proposal check
    html_link: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["start"] = self.start.isoformat()
        d["end"] = self.end.isoformat()
        return d


@dataclass
class NewEvent:
    """What the bot asks the backend to write. Timezone-aware datetimes only."""
    title: str
    start: datetime
    end: datetime
    timezone: str
    location: str = ""
    description: str = ""
    recurrence: list[str] = field(default_factory=list)


@dataclass
class EventPatch:
    title: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    timezone: str | None = None
    location: str | None = None


@dataclass
class CalendarInfo:
    id: str
    summary: str
    access_role: str
    primary: bool = False


class CalendarBackend(Protocol):
    name: str

    async def list_calendars(self) -> list[CalendarInfo]: ...

    async def list_events(self, time_min: datetime, time_max: datetime, query: str | None = None,
                          limit: int = 25) -> list[Event]: ...

    async def get_event(self, event_id: str) -> Event: ...

    async def create_event(self, event: NewEvent, event_id: str) -> Event:
        """Idempotent on event_id: if it already exists, return the existing event."""
        ...

    async def update_event(self, event_id: str, patch: EventPatch) -> Event: ...

    async def delete_event(self, event_id: str) -> None: ...
