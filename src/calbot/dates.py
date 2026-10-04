"""Deterministic relative-date resolution (spec §7). Pure functions; no clock access."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

# Abbreviations normalised before parsing ("tmrw 6pm" style shorthand, spec E21).
_ALIASES = {
    "tmrw": "tomorrow", "tmr": "tomorrow", "tmw": "tomorrow", "tomoro": "tomorrow", "tomorow": "tomorrow",
    "tdy": "today", "tonight": "today",
    "mon": "monday", "tue": "tuesday", "tues": "tuesday", "wed": "wednesday", "weds": "wednesday",
    "thu": "thursday", "thur": "thursday", "thurs": "thursday", "fri": "friday",
    "sat": "saturday", "sun": "sunday",
}
_WD = "|".join(DAYS)
_PAT = re.compile(rf"(this coming|coming|upcoming|this|next)?\s*({_WD})")


def _normalise(phrase: str) -> str:
    words = phrase.strip().lower().replace(",", " ").split()
    return " ".join(_ALIASES.get(w, w) for w in words)


def date_candidates(phrase: str, today: date, next_policy: str = "ask") -> dict:
    """Weeks are Monday-Sunday. A = first occurrence of the weekday strictly after today.
    next_policy: 'ask' | 'upcoming' | 'following_week' (only affects 'next <weekday>').
    Raises ValueError for phrases outside the supported table."""
    p = _normalise(phrase)
    note = ""
    if p == "today":
        cands = [today]
    elif p == "tomorrow":
        cands = [today + timedelta(days=1)]
    elif p == "day after tomorrow":
        cands = [today + timedelta(days=2)]
    else:
        try:
            cands = [date.fromisoformat(p)]
        except ValueError:
            m = _PAT.fullmatch(p)
            if not m:
                raise ValueError(f"Cannot parse date phrase: {phrase!r}")
            qual, name = m.groups()
            target = DAYS.index(name)
            week_start = today - timedelta(days=today.weekday())
            A = today + timedelta(days=(target - today.weekday() - 1) % 7 + 1)
            if qual == "this":
                d = week_start + timedelta(days=target)
                if d >= today:
                    cands = [d]
                else:
                    cands, note = [A], f"'this {name}' already passed this week; using the upcoming one"
            elif qual in ("this coming", "coming", "upcoming"):
                cands = [A]
            elif qual == "next":
                cands = [A, A + timedelta(days=7)]
                if next_policy == "upcoming":
                    cands = [A]
                elif next_policy == "following_week":
                    cands = [A + timedelta(days=7)]
            else:  # bare weekday
                cands = [today, A] if target == today.weekday() else [A]
    return {
        "candidates": [
            {"date": c.isoformat(), "weekday": DAYS[c.weekday()].title(), "label": fmt_day(c)} for c in cands
        ],
        "ambiguous": len(cands) > 1,
        "note": note,
    }


def fmt_day(d: date) -> str:
    """'Sat 17 Oct 2026'."""
    return f"{d:%a} {d.day} {d:%b %Y}"


def now_info(now: datetime, days: int = 21) -> dict:
    """Payload of the get_now tool: lets the model read weekdays instead of computing them."""
    today = now.date()
    week_start = today - timedelta(days=today.weekday())
    return {
        "now": now.isoformat(timespec="minutes"),
        "today": today.isoformat(),
        "weekday": DAYS[today.weekday()].title(),
        "week": {"start": week_start.isoformat(), "end": (week_start + timedelta(days=6)).isoformat()},
        "next_days": [
            {"date": (today + timedelta(days=i)).isoformat(), "weekday": DAYS[(today + timedelta(days=i)).weekday()].title()}
            for i in range(1, days + 1)
        ],
    }
