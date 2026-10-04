import pytest
from pydantic import ValidationError

from calbot.roster import Student, people_title, resolve_people
from calbot.schemas import BookEvent, DeleteEvent, EditEvent, missing_required, parse_action, tool_input_schema
from tests.conftest import ROSTER


# ---------- schemas ----------
def test_booking_without_names_is_valid():
    a = parse_action("book", {"on_date": "2026-10-17", "start_time": "10:00", "location": "Clearwater"})
    assert isinstance(a, BookEvent) and a.people == [] and missing_required(a) == []


@pytest.mark.parametrize("payload,missing", [
    ({"on_date": "2026-10-17", "start_time": "10:00"}, ["location"]),
    ({"on_date": "2026-10-17", "start_time": "10:00", "location": "TBD"}, ["location"]),
    ({"on_date": "2026-10-17"}, ["time", "location"]),
    ({}, ["date", "time", "location"]),
])
def test_missing_required(payload, missing):
    assert missing_required(parse_action("book", payload)) == missing


def test_default_location_satisfies_rule():
    a = parse_action("book", {"on_date": "2026-10-17", "start_time": "10:00"})
    assert missing_required(a, {"location": "Kallang"}) == []


@pytest.mark.parametrize("intent,payload", [
    ("edit", {"new_start_time": "11:00"}),
    ("delete", {}),
    ("edit", {"event_id": "abc"}),  # no change
    ("frobnicate", {}),
    ("book", {"on_date": "not a date"}),
])
def test_invalid_actions(intent, payload):
    with pytest.raises(ValidationError):
        parse_action(intent, payload)


def test_edit_and_delete_ok():
    assert isinstance(parse_action("edit", {"event_id": "e1", "new_start_time": "11:00"}), EditEvent)
    assert isinstance(parse_action("delete", {"event_id": "e1"}), DeleteEvent)


def test_tool_schemas_hide_intent_and_are_closed():
    for m in (BookEvent, EditEvent, DeleteEvent):
        s = tool_input_schema(m)
        assert "intent" not in s["properties"] and s["additionalProperties"] is False
    assert tool_input_schema(EditEvent)["required"] == ["event_id"]


def _count(schema):
    props = schema["properties"]
    optional = [k for k in props if k not in schema.get("required", [])]
    unions = [k for k, v in props.items() if "anyOf" in v]
    return len(optional), len(unions)


def test_strict_budget_within_documented_limits():
    totals = [_count(tool_input_schema(m)) for m in (BookEvent, EditEvent, DeleteEvent)]
    assert sum(t[0] for t in totals) <= 24 and sum(t[1] for t in totals) <= 16


# ---------- roster ----------
def names(students):
    return [s.name for s in students]


def test_ambiguous_first_name():
    r = resolve_people(["wei"], ROSTER)
    assert r.ambiguous == {"wei": ["Wei Ling Tan", "Wei Ming Lee"]} and not r.resolved


@pytest.mark.parametrize("typed,expected", [
    ("ahmad", "Ahmad Rahman"), ("rahman", "Ahmad Rahman"), ("Priyaa", "Priya Nair"),
    ("Wei Ling", "Wei Ling Tan"),  # multi-word: was 'unknown' with the spec's reference matcher
    ("wei ling tan", "Wei Ling Tan"), ("WL", "Wei Ling Tan"), ("  ADAM ", "Adam Lee"),
])
def test_resolved(typed, expected):
    r = resolve_people([typed], ROSTER)
    assert names(r.resolved) == [expected] and not r.ambiguous and not r.unknown


def test_unknown_never_blocks_and_order_kept():
    r = resolve_people(["eve", "zoe", "adam"], ROSTER)
    assert r.unknown == ["Zoe"] and r.ordered == ["Eve Goh", "Zoe", "Adam Lee"]


def test_single_letter_not_matched():
    assert resolve_people(["a"], ROSTER).unknown == ["A"]


def test_empty_roster_and_empty_names():
    assert resolve_people(["Zoe"], []).unknown == ["Zoe"]
    r = resolve_people([], ROSTER)
    assert not (r.resolved or r.ambiguous or r.unknown)


def test_two_adams_ambiguous_eve_unknown():
    roster = [Student("Adam Lee"), Student("Adam Tan")]
    r = resolve_people(["Adam", "Eve"], roster)
    assert r.ambiguous == {"Adam": ["Adam Lee", "Adam Tan"]} and r.unknown == ["Eve"]


def test_duplicate_alias_is_ambiguous():
    roster = [Student("Ann Lim", ("AL",)), Student("Alan Lee", ("AL",))]
    assert "AL" in resolve_people(["AL"], roster).ambiguous


@pytest.mark.parametrize("people,title", [
    ([], "Tennis lesson"),
    (["Adam"], "Tennis lesson – Adam"),
    (["Adam", "Eve"], "Tennis lesson – Adam & Eve"),
    (["Adam", "Eve", "Ben"], "Tennis lesson – Adam, Eve & Ben"),
    (["A", "B", "C", "D"], "Tennis lesson (4 students)"),
])
def test_people_title(people, title):
    assert people_title("Tennis lesson", people) == title
