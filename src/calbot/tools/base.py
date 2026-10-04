"""Tool definition types shared by every capability module (spec §5.5)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable, Literal, Protocol

if TYPE_CHECKING:
    from ..backends.base import CalendarBackend
    from ..config import Settings
    from ..confirm import Proposals
    from ..store import Proposal, Session, Store


class UI(Protocol):
    """What tools may do to the chat. Implemented by telegram_app and by the eval harness."""

    async def send_question(self, chat_id: int, question: str, options: list[str]) -> None: ...

    async def send_card(self, chat_id: int, proposal: "Proposal") -> None: ...


@dataclass
class ToolContext:
    settings: "Settings"
    store: "Store"
    backend: "CalendarBackend"
    proposals: "Proposals"
    session: "Session"
    ui: UI


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    ends_turn: bool = False  # ask_user: stop the loop; the user's reply becomes this tool's result

    @classmethod
    def ok(cls, data, **kw) -> "ToolResult":
        return cls(json.dumps(data, ensure_ascii=False, default=str), **kw)

    @classmethod
    def error(cls, msg: str) -> "ToolResult":
        return cls(msg, is_error=True)


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[ToolContext, dict], Awaitable[ToolResult]]
    kind: Literal["read", "propose", "ui"] = "read"
    strict: bool = False

    def api_param(self, strict_enabled: bool) -> dict:
        d = {"name": self.name, "description": self.description, "input_schema": self.input_schema}
        if self.strict and strict_enabled:
            d["strict"] = True
        return d


def obj_schema(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}
