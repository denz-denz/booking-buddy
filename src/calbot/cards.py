"""Plain-text rendering of confirm cards. Plain text (no parse mode) so event titles never break Markdown."""
from __future__ import annotations

from datetime import datetime

from .dates import fmt_day


def fmt_duration(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    if h and m:
        return f"{h} h {m} min"
    return f"{h} h" if h else f"{m} min"


def fmt_span(start: datetime, end: datetime, tz_label: str) -> str:
    """'Sat 17 Oct 2026 · 10:00–11:00 SGT' (end date shown if it differs)."""
    if start.date() == end.date():
        return f"{fmt_day(start.date())} · {start:%H:%M}–{end:%H:%M} {tz_label}"
    return f"{fmt_day(start.date())} {start:%H:%M} – {fmt_day(end.date())} {end:%H:%M} {tz_label}"


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def render_event_lines(ev: dict, tz_label: str, flags: dict | None = None) -> list[str]:
    flags = flags or {}
    start, end = _dt(ev["start"]), _dt(ev["end"])
    when = fmt_span(start, end, tz_label)
    dur_min = int((end - start).total_seconds() // 60)
    if flags.get("duration_defaulted"):
        when += f"  ({fmt_duration(dur_min)} by default)"
    lines = [f"📅 {ev['title']}" + ("  (default title)" if flags.get("title_defaulted") else ""), when]
    if ev.get("location"):
        loc = f"📍 {ev['location']}"
        if flags.get("location_defaulted"):
            loc += "  (student's default venue)"
        lines.append(loc)
    if ev.get("recurrence"):
        lines.append("🔁 " + "; ".join(ev["recurrence"]))
    return lines


def render_card(kind: str, payload: dict, tz_label: str) -> str:
    if kind == "create":
        lines = render_event_lines(payload["event"], tz_label, payload.get("flags"))
        if payload.get("description_names"):
            lines.append("👥 " + ", ".join(payload["description_names"]))
    elif kind == "update":
        lines = ["✏️ Change event", "Before:"]
        lines += ["  " + x for x in render_event_lines(payload["before"], tz_label)]
        lines.append("After:")
        lines += ["  " + x for x in render_event_lines(payload["after"], tz_label)]
    elif kind == "delete":
        lines = ["🗑 Delete this event? This removes it from your calendar."]
        lines += render_event_lines(payload["event"], tz_label)
    else:
        raise ValueError(kind)
    for name in payload.get("unknown_people", []):
        lines.append(f"⚠️ {name}: not on roster")
    for c in payload.get("conflicts", []):
        lines.append(f"⚠️ Overlaps: {fmt_span(_dt(c['start']), _dt(c['end']), tz_label)} {c['title'][:60]}")
    return "\n".join(lines)


BUTTONS = {
    "create": [("✅ Create", "ok"), ("✏️ Edit", "ed"), ("❌ Cancel", "no")],
    "update": [("✅ Update", "ok"), ("✏️ Edit", "ed"), ("❌ Cancel", "no")],
    "delete": [("🗑 Delete", "ok"), ("❌ Keep it", "no")],
}

DONE_LABEL = {"create": "✅ Created", "update": "✅ Updated", "delete": "🗑 Deleted"}
