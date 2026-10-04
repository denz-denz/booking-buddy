"""get_now and date_candidates: the model reads weekdays instead of computing them (spec §7)."""
from __future__ import annotations

from ..dates import date_candidates, now_info
from .base import Tool, ToolContext, ToolResult, obj_schema


async def get_now(ctx: ToolContext, _: dict) -> ToolResult:
    data = now_info(ctx.settings.now())
    data["timezone"] = ctx.settings.timezone
    return ToolResult.ok(data)


async def resolve_date(ctx: ToolContext, args: dict) -> ToolResult:
    phrase = str(args.get("phrase", ""))
    try:
        res = date_candidates(phrase, ctx.settings.now().date(), ctx.settings.next_weekday_policy)
    except ValueError:
        return ToolResult.error(
            f"Can't resolve {phrase!r} deterministically. Use the get_now date table to work it out, or ask the "
            "user for an explicit date. The confirm card will show the weekday and date.")
    return ToolResult.ok(res)


TOOLS = [
    Tool(
        name="get_now",
        description="Current date/time in the owner's timezone, today's weekday, this Mon-Sun week, and a table of "
                    "the next 21 dates with weekday names. Call before resolving any date.",
        input_schema=obj_schema({}),
        handler=get_now,
    ),
    Tool(
        name="date_candidates",
        description="Resolve a relative date phrase ('next saturday', 'this friday', 'tomorrow', 'coming sat', "
                    "ISO date) into candidate dates. If ambiguous=true and the user hasn't picked one, ask with "
                    "ask_user. Pass only the date words, not the time.",
        input_schema=obj_schema({"phrase": {"type": "string", "description": "The user's date words, e.g. 'next saturday'"}},
                                ["phrase"]),
        handler=resolve_date,
    ),
]
