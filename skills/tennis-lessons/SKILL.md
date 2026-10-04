---
name: tennis-lessons
description: The user mentions a tennis lesson, coaching session, court booking, or a student by name.
---
# Tennis lesson conventions (the user is the coach; people are students)
- Title: "Tennis lesson" for lessons. Don't put names in the title; pass them in people and code
  builds "Tennis lesson – Adam & Eve".
- Names are optional. If the user gives none, book without names.
- If the tool reports an ambiguous name, ask which student for that name only. Unknown names are fine:
  the booking proceeds and the card flags them.
- Venue goes in location as the user wrote it, title-cased ("clearwater" → "Clearwater"). Don't expand,
  geocode or guess addresses. One named student with a saved default venue may omit the location.
- Duration: omit unless the user said it ("1.5h" → 90). Code applies the default and shows it on the card.
- Group lessons ("for adam and eve"): one event, each name its own item in people.
- Never add attendees or send invitations.
- Recurring weekly lessons: confirm the start date and how many weeks / until when before proposing an
  RRULE like "RRULE:FREQ=WEEKLY;COUNT=8".
