"""Pydantic action models: the single source of truth for the propose_* tools (spec §5.6)."""
from __future__ import annotations

from datetime import date, time
from typing import Annotated, Literal, Union

import anthropic
from pydantic import BaseModel, Field, TypeAdapter, model_validator


class BookEvent(BaseModel):
    """Lenient on purpose: every field is optional at the schema level.
    What is *required* (date, time, location) is enforced by missing_required() below."""
    intent: Literal["book"]
    on_date: date | None = Field(None, description="Resolved date (YYYY-MM-DD), taken from date_candidates or an explicit date. Omit if the user gave none.")
    start_time: time | None = Field(None, description="Start time, 24h HH:MM. Omit if the user gave none.")
    location: str | None = Field(None, description="Venue as the user wrote it, title-cased. Omit if not given.")
    title: str | None = Field(None, description="Short title without people's names, e.g. 'Tennis lesson'. Omit to use the default.")
    people: list[str] = Field(default_factory=list, description="Names as written, one per item. Empty is fine.")
    duration_min: int | None = Field(None, description="Omit if the user gave none; code applies the default")
    date_phrase: str | None = Field(None, description="The user's own words for the date, e.g. 'next saturday' (for logging)")
    notes: str | None = Field(None, description="Extra details the user explicitly gave. Omit otherwise.")
    recurrence: str | None = Field(None, description="RRULE only if the user asked for a repeating event, e.g. 'RRULE:FREQ=WEEKLY;COUNT=8'. Must include COUNT or UNTIL.")


class EditEvent(BaseModel):
    intent: Literal["edit"]
    event_id: str = Field(description="Must come from list_events / get_event / a committed proposal")
    new_title: str | None = None
    new_date: date | None = None
    new_start_time: time | None = None
    new_duration_min: int | None = None
    new_location: str | None = None

    @model_validator(mode="after")
    def needs_a_change(self):
        if not any([self.new_title, self.new_date, self.new_start_time,
                    self.new_duration_min, self.new_location]):
            raise ValueError("edit must change at least one field")
        return self


class DeleteEvent(BaseModel):
    intent: Literal["delete"]
    event_id: str = Field(description="Must come from list_events / get_event / a committed proposal")


Action = Annotated[Union[BookEvent, EditEvent, DeleteEvent], Field(discriminator="intent")]
ActionAdapter: TypeAdapter = TypeAdapter(Action)

_PLACEHOLDERS = {"", "unknown", "tbd", "tba", "n/a", "na", "none", "null", "?", "somewhere"}


def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip().casefold() in _PLACEHOLDERS)


def missing_required(a: BookEvent, defaults: dict | None = None) -> list[str]:
    """The business rule: a booking needs a date, a time and a location.
    `defaults` may supply a location (e.g. the single resolved student's default venue)."""
    defaults = defaults or {}
    missing = []
    if a.on_date is None:
        missing.append("date")
    if a.start_time is None:
        missing.append("time")
    if _blank(a.location) and _blank(defaults.get("location")):
        missing.append("location")
    return missing


def tool_input_schema(model: type[BaseModel]) -> dict:
    """JSON schema for a propose_* tool: generated from the model, minus `intent` (code fills it)."""
    schema = anthropic.transform_schema(model)
    schema["properties"].pop("intent", None)
    if "required" in schema:
        schema["required"] = [r for r in schema["required"] if r != "intent"]
    schema.pop("title", None)
    return schema


def parse_action(intent: str, tool_input: dict):
    """Validate a tool call server-side. Raises pydantic.ValidationError."""
    return ActionAdapter.validate_python({**tool_input, "intent": intent})
