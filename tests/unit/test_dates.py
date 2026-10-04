from datetime import date, datetime, timedelta

import pytest

from calbot.dates import DAYS, date_candidates, now_info

SUN = date(2026, 10, 4)


def dates(res):
    return [c["date"] for c in res["candidates"]]


@pytest.mark.parametrize("phrase,expected,ambiguous", [
    ("this coming saturday", ["2026-10-10"], False),
    ("coming saturday", ["2026-10-10"], False),
    ("upcoming saturday", ["2026-10-10"], False),
    ("this saturday", ["2026-10-10"], False),
    ("next saturday", ["2026-10-10", "2026-10-17"], True),
    ("saturday", ["2026-10-10"], False),
    ("tomorrow", ["2026-10-05"], False),
    ("today", ["2026-10-04"], False),
    ("2026-10-17", ["2026-10-17"], False),
    ("  Next   SATURDAY ", ["2026-10-10", "2026-10-17"], True),
    ("tmrw", ["2026-10-05"], False),
    ("coming sat", ["2026-10-10"], False),
    ("next thurs", ["2026-10-08", "2026-10-15"], True),
    ("this sunday", ["2026-10-04"], False),
    ("sunday", ["2026-10-04", "2026-10-11"], True),
])
def test_spec_table_on_sunday(phrase, expected, ambiguous):
    res = date_candidates(phrase, SUN)
    assert dates(res) == expected
    assert res["ambiguous"] is ambiguous


def test_this_passed_has_note():
    assert "already passed" in date_candidates("this saturday", SUN)["note"]


def test_on_saturday():
    sat = date(2026, 10, 10)
    assert dates(date_candidates("this coming saturday", sat)) == ["2026-10-17"]
    res = date_candidates("saturday", sat)
    assert dates(res) == ["2026-10-10", "2026-10-17"] and res["ambiguous"]


@pytest.mark.parametrize("policy,expected", [
    ("ask", ["2026-10-09", "2026-10-16"]),
    ("upcoming", ["2026-10-09"]),
    ("following_week", ["2026-10-16"]),
])
def test_next_policy_on_monday(policy, expected):
    assert dates(date_candidates("next friday", date(2026, 10, 5), policy)) == expected


def test_unparseable_raises():
    for p in ["in 2 weeks", "the 15th", "end of month", ""]:
        with pytest.raises(ValueError):
            date_candidates(p, SUN)


@pytest.mark.parametrize("today", [date(2026, 12, 25) + timedelta(days=i) for i in range(14)]
                         + [date(2028, 2, 25) + timedelta(days=i) for i in range(7)])
@pytest.mark.parametrize("wd", DAYS)
def test_invariants_all_weekdays(today, wd):
    """Across month/year/leap boundaries: weekday always matches, A is 1..7 days ahead."""
    coming = date.fromisoformat(dates(date_candidates(f"coming {wd}", today))[0])
    assert coming.weekday() == DAYS.index(wd) and 1 <= (coming - today).days <= 7
    nxt = [date.fromisoformat(d) for d in dates(date_candidates(f"next {wd}", today))]
    assert nxt == [coming, coming + timedelta(days=7)]
    this = date.fromisoformat(dates(date_candidates(f"this {wd}", today))[0])
    assert this.weekday() == DAYS.index(wd) and this >= today
    for c in date_candidates(wd, today)["candidates"]:
        assert date.fromisoformat(c["date"]).strftime("%A") == c["weekday"]


def test_now_info_table():
    info = now_info(datetime(2026, 10, 4, 9, 0))
    assert info["weekday"] == "Sunday"
    assert info["week"] == {"start": "2026-09-28", "end": "2026-10-04"}
    assert info["next_days"][5] == {"date": "2026-10-10", "weekday": "Saturday"}
    assert len(info["next_days"]) == 21
