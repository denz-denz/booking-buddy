"""Proposal lifecycle (spec §6.4, §5.8): build proposals from validated actions, commit on a tap.

The agent can only *propose*. Only commit() writes to the calendar, and it is called by bot
code in response to the owner's button tap (or CONFIRM_MODE=never for creates)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from dateutil.rrule import rrulestr

from .backends.base import (AuthExpired, BackendError, CalendarBackend, Event, EventNotFound, EventPatch,
                            NewEvent)
from .cards import DONE_LABEL, fmt_span
from .config import Settings
from .roster import Student, people_title, resolve_people
from .schemas import BookEvent, DeleteEvent, EditEvent, _blank, missing_required
from .store import Proposal, Session, Store

log = logging.getLogger(__name__)

PAST_GRACE = timedelta(minutes=5)
MAX_AHEAD = timedelta(days=730)


class ProposalError(Exception):
    """Validation failure; the message goes back to the model as an error tool_result."""


@dataclass
class CommitOutcome:
    ok: bool
    message: str
    proposal: Proposal | None = None
    event: Event | None = None
    auth_expired: bool = False


def _ev_dict(e: Event) -> dict:
    return {"title": e.title, "start": e.start.isoformat(), "end": e.end.isoformat(), "location": e.location}


class Proposals:
    def __init__(self, settings: Settings, store: Store, backend: CalendarBackend, roster: list[Student]):
        self.s = settings
        self.store = store
        self.backend = backend
        self.roster = roster

    # ---------- shared checks ----------
    def _check_window(self, start: datetime, end: datetime) -> None:
        now = self.s.now()
        if end <= start:
            raise ProposalError("End must be after start.")
        if end - start > timedelta(hours=24):
            raise ProposalError("Events longer than 24 hours are not supported. Check the duration with the user.")
        if start < now - PAST_GRACE:
            raise ProposalError(f"{start:%a %d %b %Y %H:%M} is in the past (now is {now:%a %d %b %Y %H:%M}). "
                                "Check the date/time with the user.")
        if start > now + MAX_AHEAD:
            raise ProposalError("That date is more than 2 years away; probably a typo. Check with the user.")

    async def _conflicts(self, start: datetime, end: datetime, exclude_id: str | None = None) -> list[dict]:
        events = await self.backend.list_events(start, end, limit=10)
        return [{"title": e.title, "start": e.start.isoformat(), "end": e.end.isoformat()}
                for e in events if e.id != exclude_id]

    def _supersede_revision(self, session: Session) -> None:
        old = session.revising_proposal_id
        if old:
            self.store.transition(old, "pending", "superseded")
            session.revising_proposal_id = None

    def _require_known(self, session: Session, event_id: str) -> None:
        if event_id not in session.known_event_ids:
            raise ProposalError(f"Unknown event_id {event_id!r}. Event IDs must come from list_events/get_event "
                                "results in this conversation. Look the event up first; never guess an ID.")

    # ---------- create ----------
    async def propose_create(self, session: Session, a: BookEvent) -> Proposal:
        res = resolve_people(a.people, self.roster)
        if res.ambiguous:
            parts = [f"'{typed}' could be: {', '.join(c)}" for typed, c in res.ambiguous.items()]
            raise ProposalError("Ambiguous people. " + "; ".join(parts) +
                                ". Ask the user which one with ask_user (one option per candidate). "
                                "Keep every other detail; do not re-ask them.")
        single = res.resolved[0] if len(res.ordered) == 1 and len(res.resolved) == 1 else None
        defaults = {"location": single.default_location} if single else {}
        missing = missing_required(a, defaults)
        if missing:
            raise ProposalError(f"Missing: {', '.join(missing)}. Ask the user ONE short question covering all of "
                                "these. Do not guess or use placeholders.")

        flags = {}
        location = a.location.strip() if not _blank(a.location) else None
        if location is None:
            location, flags["location_defaulted"] = single.default_location, True
        if a.duration_min:
            duration = a.duration_min
        else:
            duration = (single.default_duration_min if single and single.default_duration_min
                        else self.s.default_duration_min)
            flags["duration_defaulted"] = True
        if not 5 <= duration <= 24 * 60:
            raise ProposalError(f"Duration {duration} min is not plausible. Check with the user.")

        start = datetime.combine(a.on_date, a.start_time.replace(tzinfo=None), self.s.tz)
        end = start + timedelta(minutes=duration)
        self._check_window(start, end)

        if _blank(a.title):
            base, flags["title_defaulted"] = self.s.default_event_title, True
        else:
            base = a.title.strip()
        title = people_title(base, res.ordered)
        desc_lines = []
        if len(res.ordered) > 3:
            desc_lines.append("Students: " + ", ".join(res.ordered))
        if a.notes and not _blank(a.notes):
            desc_lines.append(a.notes.strip())

        recurrence = []
        if a.recurrence and not _blank(a.recurrence):
            rule = a.recurrence.strip()
            if not rule.upper().startswith("RRULE:"):
                rule = "RRULE:" + rule
            body = rule[6:].upper()
            if "COUNT=" not in body and "UNTIL=" not in body:
                raise ProposalError("Recurring events need an end: ask how many times (COUNT) or until when (UNTIL).")
            try:
                rrulestr(rule[6:], dtstart=start.replace(tzinfo=None))
            except (ValueError, TypeError) as e:
                raise ProposalError(f"Invalid RRULE: {e}") from None
            recurrence = [rule]

        event = {"title": title, "start": start.isoformat(), "end": end.isoformat(), "timezone": self.s.timezone,
                 "location": location or "", "description": "\n".join(desc_lines), "recurrence": recurrence}
        payload = {
            "event": event, "flags": flags, "unknown_people": res.unknown,
            "description_names": res.ordered if len(res.ordered) > 3 else [],
            "conflicts": await self._conflicts(start, end),
        }
        self._supersede_revision(session)
        return self.store.add_proposal(session.chat_id, "create", a.model_dump(mode="json"), payload)

    # ---------- update ----------
    async def propose_update(self, session: Session, a: EditEvent) -> Proposal:
        self._require_known(session, a.event_id)
        ev = await self._get(a.event_id)
        if ev.recurring:
            raise ProposalError("This is a recurring event; changing recurring events isn't supported yet. Tell the user.")
        duration = ev.end - ev.start
        new_date = a.new_date or ev.start.date()
        new_time = a.new_start_time.replace(tzinfo=None) if a.new_start_time else ev.start.time()
        new_start = datetime.combine(new_date, new_time, self.s.tz)
        new_dur = timedelta(minutes=a.new_duration_min) if a.new_duration_min else duration
        new_end = new_start + new_dur
        patch: dict = {}
        if new_start != ev.start or new_end != ev.end:
            self._check_window(new_start, new_end)
            patch.update(start=new_start.isoformat(), end=new_end.isoformat())
        if a.new_title and a.new_title.strip() != ev.title:
            patch["title"] = a.new_title.strip()
        if a.new_location and a.new_location.strip() != ev.location:
            patch["location"] = a.new_location.strip()
        if not patch:
            raise ProposalError("Those values match the event already; nothing would change. Ask what to change.")
        after = {**_ev_dict(ev), **{k: v for k, v in patch.items()}}
        payload = {
            "event_id": ev.id, "updated": ev.updated, "before": _ev_dict(ev), "after": after, "patch": patch,
            "conflicts": await self._conflicts(new_start, new_end, exclude_id=ev.id) if "start" in patch else [],
        }
        self._supersede_revision(session)
        return self.store.add_proposal(session.chat_id, "update", a.model_dump(mode="json"), payload)

    # ---------- delete ----------
    async def propose_delete(self, session: Session, a: DeleteEvent) -> Proposal:
        self._require_known(session, a.event_id)
        ev = await self._get(a.event_id)
        if ev.recurring:
            raise ProposalError("This is a recurring event; deleting recurring events isn't supported yet. Tell the user.")
        payload = {"event_id": ev.id, "updated": ev.updated, "event": _ev_dict(ev)}
        self._supersede_revision(session)
        return self.store.add_proposal(session.chat_id, "delete", a.model_dump(mode="json"), payload)

    async def _get(self, event_id: str) -> Event:
        try:
            return await self.backend.get_event(event_id)
        except EventNotFound:
            raise ProposalError(f"Event {event_id} no longer exists. Tell the user.") from None

    # ---------- commit / cancel ----------
    async def commit(self, pid: str, user_id: int) -> CommitOutcome:
        if user_id != self.s.owner_telegram_id:
            return CommitOutcome(False, "Not authorized.")
        p = self.store.get_proposal(pid)
        if p is None:
            return CommitOutcome(False, "This proposal no longer exists.")
        if p.status in ("committing", "committed"):
            return CommitOutcome(p.status == "committed", "Already done.", p)
        if p.status != "pending":
            return CommitOutcome(False, f"This proposal is {p.status}; nothing was changed.", p)
        if datetime.now().timestamp() - p.created_at > self.s.proposal_ttl_min * 60:
            self.store.transition(pid, "pending", "expired")
            return CommitOutcome(False, "This proposal expired. Send the request again.", p)
        if not self.store.transition(pid, "pending", "committing"):
            return CommitOutcome(False, "Already being handled.", p)  # lost a double-tap race
        try:
            event = await self._write(p)
        except _Stale as e:
            self.store.transition(pid, "committing", "failed", {"error": str(e)})
            return CommitOutcome(False, f"Not changed: {e}", p)
        except AuthExpired as e:
            self.store.transition(pid, "committing", "pending")
            return CommitOutcome(False, f"Google auth problem: {e}", p, auth_expired=True)
        except BackendError as e:
            self.store.transition(pid, "committing", "pending")  # safe to retry: creates are idempotent
            return CommitOutcome(False, f"Calendar error, nothing changed. Tap again to retry. ({e})", p)
        result = {"event_id": event.id if event else p.payload.get("event_id"),
                  "html_link": event.html_link if event else ""}
        self.store.transition(pid, "committing", "committed", result)
        p = self.store.get_proposal(pid)
        return CommitOutcome(True, DONE_LABEL[p.kind], p, event)

    async def _write(self, p: Proposal) -> Event | None:
        if p.kind == "create":
            e = p.payload["event"]
            new = NewEvent(title=e["title"], start=datetime.fromisoformat(e["start"]),
                           end=datetime.fromisoformat(e["end"]), timezone=e["timezone"], location=e["location"],
                           description=e["description"], recurrence=e["recurrence"])
            return await self.backend.create_event(new, event_id=p.id)
        # update / delete: stale-proposal check (spec §5.8)
        try:
            current = await self.backend.get_event(p.payload["event_id"])
        except EventNotFound:
            raise _Stale("the event no longer exists.") from None
        if current.updated != p.payload["updated"]:
            raise _Stale("the event was changed after this proposal was made. Ask again.")
        if p.kind == "update":
            patch = p.payload["patch"]
            return await self.backend.update_event(current.id, EventPatch(
                title=patch.get("title"), location=patch.get("location"), timezone=self.s.timezone,
                start=datetime.fromisoformat(patch["start"]) if "start" in patch else None,
                end=datetime.fromisoformat(patch["end"]) if "end" in patch else None))
        await self.backend.delete_event(current.id)
        return None

    def cancel(self, pid: str, user_id: int) -> CommitOutcome:
        if user_id != self.s.owner_telegram_id:
            return CommitOutcome(False, "Not authorized.")
        ok = self.store.transition(pid, "pending", "cancelled")
        return CommitOutcome(ok, "❌ Cancelled" if ok else "Nothing to cancel.", self.store.get_proposal(pid))

    def session_note(self, outcome: CommitOutcome) -> str | None:
        """Trusted note appended to the agent session so 'move it to 11' can refer to the event."""
        p = outcome.proposal
        if not (outcome.ok and p):
            return None
        tz_label = self.s.tz_label()
        if p.kind == "create":
            e = p.payload["event"]
            when = fmt_span(datetime.fromisoformat(e["start"]), datetime.fromisoformat(e["end"]), tz_label)
            return f"Committed: event created (event_id={p.result['event_id']}): {e['title']}, {when}."
        if p.kind == "update":
            return f"Committed: event {p.payload['event_id']} updated as proposed."
        return f"Committed: event {p.payload['event_id']} deleted."


class _Stale(Exception):
    pass
