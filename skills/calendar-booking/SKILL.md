---
name: calendar-booking
description: The user wants to create, move, cancel or look up calendar events from natural language.
---
# Calendar booking playbook
- Extract: title, date phrase, start time, duration, location, people, recurrence.
- Resolve dates with date_candidates. Ask (ask_user) whenever more than one plausible date remains.
- If the message gives an explicit date ("Sat 17 Oct", "17/10"), don't ask about weekday ambiguity.
- propose_create_event runs the conflict check itself. If it reports conflicts, mention them in one line.
- Lookups ("what's on saturday?"): list_events for the window, answer in a short list (weekday, time, title).
- Move/cancel: find the event with list_events first (use a name as query when given).
  0 matches: say so and ask. 1 match: propose. Several: ask_user with one option per event ("Sat 10:00 Tennis lesson – Adam").
- After a "Committed:" note, "it"/"that" refers to the event in the note; use its event_id directly.
- Never include anything the user did not say in title, location or notes.
