"""ask_user: send a question with option buttons and end the turn (spec §5.4)."""
from __future__ import annotations

from .base import Tool, ToolContext, ToolResult, obj_schema


async def ask_user(ctx: ToolContext, args: dict) -> ToolResult:
    question = str(args.get("question", "")).strip()
    options = [str(o).strip()[:60] for o in args.get("options", []) if str(o).strip()][:4]
    if not question:
        return ToolResult.error("question is required")
    await ctx.ui.send_question(ctx.session.chat_id, question, options)
    # The loop stops here; the user's tap or typed reply becomes this tool's result next turn.
    return ToolResult("", ends_turn=True)


TOOLS = [
    Tool(
        name="ask_user",
        description="Ask the user ONE question, optionally with 2-4 short option buttons (e.g. 'Sat 10 Oct', "
                    "'Sat 17 Oct'). The question is sent immediately and your turn ends; the user's answer comes "
                    "back as this tool's result. Free-text answers are possible too. Don't call other tools in the "
                    "same step.",
        input_schema=obj_schema({
            "question": {"type": "string"},
            "options": {"type": "array", "items": {"type": "string"}, "description": "0-4 short choices"},
        }, ["question"]),
        handler=ask_user,
        kind="ui",
    ),
]
