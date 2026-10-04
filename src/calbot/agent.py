"""Agent core: a hand-written tool loop over the Anthropic Messages API (spec D2b).

Only tools from the registry exist; there are no built-ins to strip. Conversation history is
stored verbatim in SQLite (append-only), so a restart mid-conversation resumes cleanly (E12)."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import anthropic

from .backends.base import AuthExpired, BackendError, CalendarBackend
from .config import Settings
from .confirm import Proposals
from .store import Session, Store
from .tools import assert_tool_surface
from .tools.base import Tool, ToolContext, ToolResult, UI

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16000


@dataclass
class TurnResult:
    replies: list[str] = field(default_factory=list)
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)  # (name, input), for evals/logs
    asked: bool = False
    error: str | None = None


class Agent:
    def __init__(self, settings: Settings, store: Store, backend: CalendarBackend, proposals: Proposals,
                 client, tools: list[Tool], system_prompt: str,
                 on_auth_error: Callable[[str], Awaitable[None]] | None = None):
        self.s = settings
        self.store = store
        self.backend = backend
        self.proposals = proposals
        self.client = client
        self.tools = {t.name: t for t in tools}
        self.registry = tools
        self.system_prompt = system_prompt
        self.on_auth_error = on_auth_error
        self.strict = settings.strict_tools
        self.api_tools = self._build_api_tools()

    def _build_api_tools(self) -> list[dict]:
        api_tools = [t.api_param(self.strict) for t in self.registry]
        assert_tool_surface(api_tools, self.registry)
        return api_tools

    # ---------- API ----------
    async def _create(self, messages: list[dict]):
        kw = dict(
            model=self.s.anthropic_model,
            max_tokens=MAX_TOKENS,
            system=self.system_prompt,
            tools=self.api_tools,
            messages=messages,
            output_config={"effort": self.s.anthropic_effort},
            cache_control={"type": "ephemeral"},
        )
        if self.s.refusal_fallback:
            return await self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kw)
        return await self.client.messages.create(**kw)

    async def _call(self, messages: list[dict]):
        try:
            return await self._create(messages)
        except anthropic.BadRequestError as e:
            msg = str(e).lower()
            if self.strict and ("strict" in msg or "schema" in msg):
                log.warning("API rejected strict tool schemas; continuing with code-side validation only: %s", e)
                self.strict = False
                self.api_tools = self._build_api_tools()
                return await self._create(messages)
            raise

    # ---------- tools ----------
    async def _run_tool(self, name: str, args: dict, ctx: ToolContext) -> ToolResult:
        tool = self.tools.get(name)
        if tool is None:  # can't happen with the surface check, but never execute unknown names
            return ToolResult.error(f"Unknown tool {name!r}")
        try:
            result = await tool.handler(ctx, args)
            if not result.is_error and name.startswith(("list_", "get_event", "check_", "propose_")):
                self.store.set_kv("last_calendar_ok", self.s.now().strftime("%Y-%m-%d %H:%M"))
                self.store.set_kv("token_health", "ok")
            return result
        except AuthExpired as e:
            log.error("Google auth failure in %s", name)
            if self.on_auth_error:
                await self.on_auth_error(str(e))
            return ToolResult.error("Google Calendar access has expired. The owner has been alerted. "
                                    "Tell the user briefly; don't retry.")
        except BackendError as e:
            return ToolResult.error(f"Calendar error: {e}")
        except Exception:
            log.exception("Tool %s crashed", name)
            return ToolResult.error("Internal error in tool. Tell the user something went wrong.")

    # ---------- turn ----------
    async def handle(self, session: Session, text: str, ui: UI) -> TurnResult:
        """Run one user turn. Mutates and saves the session only if the turn completes."""
        try:
            return await asyncio.wait_for(self._handle(session, text, ui), timeout=self.s.turn_timeout_s)
        except asyncio.TimeoutError:
            log.warning("Turn timed out for chat %s", session.chat_id)
            return TurnResult(replies=["Sorry, that took too long. Please try again."], error="timeout")

    async def _handle(self, session: Session, text: str, ui: UI) -> TurnResult:
        out = TurnResult()
        messages = list(session.messages)  # work on a copy; commit at the end
        if session.pending_ask:
            content = list(session.pending_ask["other_results"]) + [{
                "type": "tool_result", "tool_use_id": session.pending_ask["tool_use_id"],
                "content": f"User answered: {text}"}]
        else:
            content = [{"type": "text", "text": text}]
        messages.append({"role": "user", "content": content})
        notes = list(session.notes)
        if session.revising_proposal_id:
            p = self.store.get_proposal(session.revising_proposal_id)
            if p and p.status == "pending":
                notes.append(f"The user tapped Edit on proposal {p.id} ({p.kind}). Their message above says "
                             "what to change. Call the same propose tool again with the full corrected details; "
                             "the old proposal will be replaced.")
        if notes:
            messages.append({"role": "system", "content": "\n".join(notes)})

        ctx = ToolContext(settings=self.s, store=self.store, backend=self.backend, proposals=self.proposals,
                          session=session, ui=ui)
        pending_ask = None
        try:
            for _ in range(self.s.max_turns):
                resp = await self._call(messages)
                messages.append({"role": "assistant", "content": [b.to_dict(mode="json") for b in resp.content]})
                if resp.stop_reason == "refusal":
                    out.replies.append("Sorry, I can't help with that request.")
                    break
                tool_uses = [b for b in resp.content if b.type == "tool_use"]
                asks = [b for b in tool_uses if b.name == "ask_user"]
                if not asks:
                    out.replies += [b.text.strip() for b in resp.content if b.type == "text" and b.text.strip()]
                if not tool_uses:
                    if resp.stop_reason == "max_tokens":
                        out.replies.append("(Reply cut off. Please try a shorter request.)")
                    break
                results = []
                for tu in tool_uses:
                    args = tu.input if isinstance(tu.input, dict) else {}
                    out.tool_calls.append((tu.name, args))
                    if tu.name == "ask_user" and tu is not asks[0]:
                        r = ToolResult.error("Only one question at a time; this one was not sent.")
                    else:
                        r = await self._run_tool(tu.name, args, ctx)
                    log.info("tool %s -> %s%s", tu.name, "error" if r.is_error else "ok",
                             f": {r.content[:200]}" if r.is_error else "")
                    if r.ends_turn:
                        pending_ask = tu.id
                        continue
                    block = {"type": "tool_result", "tool_use_id": tu.id, "content": r.content}
                    if r.is_error:
                        block["is_error"] = True
                    results.append(block)
                if pending_ask:
                    out.asked = True
                    break
                messages.append({"role": "user", "content": results})
            else:
                out.replies.append("I couldn't finish that in one go. Could you rephrase or split it up?")
        except anthropic.BadRequestError as e:
            # A 400 on a stored conversation would repeat on every message; start over instead.
            log.error("Anthropic rejected the request (%s); resetting session %s", e, session.chat_id)
            self.store.reset_session(session.chat_id)
            out.error = "bad_request"
            out.replies = ["Something went wrong with this conversation, so I've reset it. "
                           "Please send your request again."]
            return out
        except anthropic.APIError as e:
            log.error("Anthropic API error: %s", e)
            out.error = "api"
            out.replies = ["Sorry, the AI service is unavailable right now. Please try again in a minute."]
            return out

        session.messages = messages
        session.notes = []
        session.pending_ask = {"tool_use_id": pending_ask, "other_results": results} if pending_ask else None
        if session.revising_proposal_id:
            p = self.store.get_proposal(session.revising_proposal_id)
            if not p or p.status != "pending":
                session.revising_proposal_id = None
        self.store.save_session(session)
        return out


def summarize_calls(calls: list[tuple[str, dict]]) -> str:
    return "; ".join(f"{n}({json.dumps(a, ensure_ascii=False)})" for n, a in calls)
