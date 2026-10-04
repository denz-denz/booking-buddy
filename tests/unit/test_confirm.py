import asyncio
from datetime import datetime, timedelta

import pytest

from calbot.backends.base import Event
from calbot.cards import render_card
from calbot.confirm import ProposalError
from calbot.schemas import parse_action
from tests.conftest import OWNER, SGT


def book(**kw):
    return parse_action("book", kw)


async def test_canonical_create_and_commit(proposals, backend, session):
    p = await proposals.propose_create(session, book(on_date="2026-10-17", start_time="10:00",
                                                     location="Clearwater Condo", title="Tennis lesson"))
    assert backend.writes == []  # nothing written before the tap
    card = render_card(p.kind, p.payload, "SGT")
    assert card.splitlines()[:3] == ["📅 Tennis lesson", "Sat 17 Oct 2026 · 10:00–11:00 SGT  (1 h by default)",
                                     "📍 Clearwater Condo"]
    out = await proposals.commit(p.id, OWNER)
    assert out.ok and backend.writes == [("create", p.id)]
    assert "event_id=" in proposals.session_note(out)


async def test_double_tap_creates_one_event(proposals, backend, session):  # E8
    p = await proposals.propose_create(session, book(on_date="2026-10-17", start_time="10:00", location="X"))
    results = await asyncio.gather(*[proposals.commit(p.id, OWNER) for _ in range(3)])
    assert len(backend.events) == 1 and backend.writes == [("create", p.id)]
    assert sum(r.ok and r.message.startswith("✅") for r in results) == 1


async def test_non_owner_cannot_commit(proposals, backend, session):
    p = await proposals.propose_create(session, book(on_date="2026-10-17", start_time="10:00", location="X"))
    assert not (await proposals.commit(p.id, 999)).ok and backend.writes == []


async def test_expiry(proposals, store, session):
    p = await proposals.propose_create(session, book(on_date="2026-10-17", start_time="10:00", location="X"))
    store.db.execute("UPDATE proposals SET created_at = created_at - 3600 WHERE id=?", (p.id,))
    out = await proposals.commit(p.id, OWNER)
    assert not out.ok and store.get_proposal(p.id).status == "expired"


async def test_cancel(proposals, store, session):
    p = await proposals.propose_create(session, book(on_date="2026-10-17", start_time="10:00", location="X"))
    assert proposals.cancel(p.id, OWNER).ok
    assert not (await proposals.commit(p.id, OWNER)).ok


async def test_edit_supersedes_old(proposals, store, session):
    p1 = await proposals.propose_create(session, book(on_date="2026-10-17", start_time="10:00", location="X"))
    session.revising_proposal_id = p1.id
    p2 = await proposals.propose_create(session, book(on_date="2026-10-17", start_time="11:00", location="X"))
    assert store.get_proposal(p1.id).status == "superseded" and store.get_proposal(p2.id).status == "pending"
    assert session.revising_proposal_id is None


async def test_missing_fields_rejected(proposals, session):  # E24/E27
    with pytest.raises(ProposalError, match="Missing: location"):
        await proposals.propose_create(session, book(on_date="2026-10-10", start_time="10:00"))
    with pytest.raises(ProposalError, match="Missing: date, time, location"):
        await proposals.propose_create(session, book())


async def test_single_student_defaults(proposals, session):  # E26
    p = await proposals.propose_create(session, book(on_date="2026-10-05", start_time="17:00", people=["ahmad"]))
    ev = p.payload["event"]
    assert ev["title"] == "Tennis lesson – Ahmad Rahman" and ev["location"] == "Kallang Court 3"
    assert p.payload["flags"] == {"location_defaulted": True, "duration_defaulted": True, "title_defaulted": True}
    assert datetime.fromisoformat(ev["end"]) - datetime.fromisoformat(ev["start"]) == timedelta(minutes=90)


async def test_group_booking(proposals, session):  # E22
    p = await proposals.propose_create(session, book(on_date="2026-10-10", start_time="10:00", location="Clearwater",
                                                     title="Tennis lesson", people=["Adam", "Eve"]))
    ev = p.payload["event"]
    assert ev["title"] == "Tennis lesson – Adam Lee & Eve Goh"
    assert p.payload["flags"] == {"duration_defaulted": True}  # group: global default, no roster defaults
    assert ev["end"].startswith("2026-10-10T11:00")


async def test_unknown_person_flagged(proposals, session):  # E18
    p = await proposals.propose_create(session, book(on_date="2026-10-05", start_time="17:00", location="Clearwater",
                                                     people=["Zoe"]))
    assert p.payload["unknown_people"] == ["Zoe"]
    assert "⚠️ Zoe: not on roster" in render_card("create", p.payload, "SGT")


async def test_ambiguous_person_rejected(proposals, session):  # E17
    with pytest.raises(ProposalError, match="'wei' could be: Wei Ling Tan, Wei Ming Lee"):
        await proposals.propose_create(session, book(on_date="2026-10-05", start_time="17:00", location="X",
                                                     people=["wei"]))


async def test_guards(proposals, session):
    with pytest.raises(ProposalError, match="in the past"):
        await proposals.propose_create(session, book(on_date="2026-10-03", start_time="10:00", location="X"))
    with pytest.raises(ProposalError, match="2 years"):
        await proposals.propose_create(session, book(on_date="2029-10-03", start_time="10:00", location="X"))
    with pytest.raises(ProposalError, match="not plausible"):
        await proposals.propose_create(session, book(on_date="2026-10-05", start_time="10:00", location="X",
                                                     duration_min=2000))
    with pytest.raises(ProposalError, match="need an end"):
        await proposals.propose_create(session, book(on_date="2026-10-05", start_time="10:00", location="X",
                                                     recurrence="RRULE:FREQ=WEEKLY"))


async def test_conflicts_reported(proposals, backend, session):  # E5
    backend.events["x1"] = Event("x1", "Dentist", datetime(2026, 10, 17, 10, 30, tzinfo=SGT),
                                 datetime(2026, 10, 17, 11, 30, tzinfo=SGT))
    p = await proposals.propose_create(session, book(on_date="2026-10-17", start_time="10:00", location="X"))
    assert [c["title"] for c in p.payload["conflicts"]] == ["Dentist"]
    assert "Overlaps" in render_card("create", p.payload, "SGT")


# ---------- edit / delete ----------
@pytest.fixture
def lesson(backend):
    e = Event("evt1", "Tennis lesson – Adam Lee", datetime(2026, 10, 10, 10, tzinfo=SGT),
              datetime(2026, 10, 10, 11, 30, tzinfo=SGT), location="Clearwater", updated="u1")
    backend.events[e.id] = e
    return e


async def test_provenance_guard(proposals, session, lesson):  # E16
    with pytest.raises(ProposalError, match="Unknown event_id"):
        await proposals.propose_update(session, parse_action("edit", {"event_id": "evt1", "new_start_time": "11:00"}))
    with pytest.raises(ProposalError, match="Unknown event_id"):
        await proposals.propose_delete(session, parse_action("delete", {"event_id": "evt1"}))


async def test_move_preserves_date_and_duration(proposals, backend, session, lesson):  # E13
    session.remember_event("evt1")
    p = await proposals.propose_update(session, parse_action("edit", {"event_id": "evt1", "new_start_time": "11:00"}))
    assert p.payload["after"]["start"] == "2026-10-10T11:00:00+08:00"
    assert p.payload["after"]["end"] == "2026-10-10T12:30:00+08:00"
    assert backend.writes == []
    assert (await proposals.commit(p.id, OWNER)).ok
    assert backend.events["evt1"].start.hour == 11 and backend.writes == [("update", "evt1")]


async def test_stale_proposal_aborts(proposals, backend, session, lesson):  # E20
    session.remember_event("evt1")
    p = await proposals.propose_update(session, parse_action("edit", {"event_id": "evt1", "new_location": "Kallang"}))
    backend.events["evt1"].updated = "u2"  # changed elsewhere after the proposal
    out = await proposals.commit(p.id, OWNER)
    assert not out.ok and "changed" in out.message and backend.writes == []
    p2 = await proposals.propose_delete(session, parse_action("delete", {"event_id": "evt1"}))
    del backend.events["evt1"]
    out = await proposals.commit(p2.id, OWNER)
    assert not out.ok and "no longer exists" in out.message


async def test_delete(proposals, backend, session, lesson):  # E15
    session.remember_event("evt1")
    p = await proposals.propose_delete(session, parse_action("delete", {"event_id": "evt1"}))
    assert "removes it" in render_card("delete", p.payload, "SGT") and backend.writes == []
    assert (await proposals.commit(p.id, OWNER)).ok and "evt1" not in backend.events


async def test_recurring_refused(proposals, backend, session, lesson):
    lesson.recurring = True
    session.remember_event("evt1")
    with pytest.raises(ProposalError, match="recurring"):
        await proposals.propose_delete(session, parse_action("delete", {"event_id": "evt1"}))


async def test_auth_expired_keeps_proposal_pending(proposals, backend, store, session):  # E10 (commit side)
    p = await proposals.propose_create(session, book(on_date="2026-10-17", start_time="10:00", location="X"))
    backend.auth_broken = True
    out = await proposals.commit(p.id, OWNER)
    assert out.auth_expired and store.get_proposal(p.id).status == "pending"
    backend.auth_broken = False
    assert (await proposals.commit(p.id, OWNER)).ok
