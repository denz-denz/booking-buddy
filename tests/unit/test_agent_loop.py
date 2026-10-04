"""Agent loop with a scripted fake Claude client (no network)."""
import copy
import itertools

import anthropic
import pytest
from anthropic.types.beta import BetaMessage

from calbot.agent import Agent
from calbot.prompts import build_system_prompt
from calbot.tools import ToolSurfaceError, assert_tool_surface, collect_tools
from tests.conftest import OWNER

_ids = itertools.count()


def msg(*blocks, stop="end_turn"):
    content = []
    for b in blocks:
        if isinstance(b, str):
            content.append({"type": "text", "text": b})
        else:
            name, inp = b
            content.append({"type": "tool_use", "id": f"toolu_{next(_ids)}", "name": name, "input": inp})
    if any(c["type"] == "tool_use" for c in content):
        stop = "tool_use"
    return BetaMessage.model_validate({
        "id": f"msg_{next(_ids)}", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
        "content": content, "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1}})


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        outer = self

        class _Messages:
            async def create(self, **kw):
                outer.requests.append(copy.deepcopy(kw))
                return outer.script.pop(0)

        class _Beta:
            messages = _Messages()

        self.beta = _Beta()
        self.messages = _Messages()


class FakeUI:
    def __init__(self):
        self.questions, self.cards = [], []

    async def send_question(self, chat_id, question, options):
        self.questions.append((question, options))

    async def send_card(self, chat_id, proposal):
        self.cards.append(proposal)


def make_agent(settings, store, backend, proposals, script, **kw):
    client = FakeClient(script)
    agent = Agent(settings, store, backend, proposals, client, collect_tools(), build_system_prompt(settings), **kw)
    return agent, client


CANONICAL = "I have an upcoming tennis lesson next saturday 10am at clearwater condo"


async def test_canonical_flow_with_restart(settings, store, backend, proposals):  # E1 + E12
    ui = FakeUI()
    agent, client = make_agent(settings, store, backend, proposals, [
        msg(("get_now", {}), ("date_candidates", {"phrase": "next saturday"})),
        msg(("ask_user", {"question": "Which Saturday?", "options": ["Sat 10 Oct", "Sat 17 Oct"]})),
    ])
    s = store.load_session(OWNER, 1800)
    r = await agent.handle(s, CANONICAL, ui)
    assert r.asked and ui.questions == [("Which Saturday?", ["Sat 10 Oct", "Sat 17 Oct"])]
    # the date_candidates result the model saw
    tool_results = client.requests[1]["messages"][-1]["content"]
    assert '"ambiguous": true' in next(t["content"] for t in tool_results if '"candidates"' in t["content"])

    # --- restart: a brand-new agent and session loaded from SQLite ---
    agent2, client2 = make_agent(settings, store, backend, proposals, [
        msg(("propose_create_event", {"title": "Tennis lesson", "date_phrase": "next saturday",
                                      "on_date": "2026-10-17", "start_time": "10:00",
                                      "location": "Clearwater Condo"})),
        msg("Tap ✅ to confirm."),
    ])
    s2 = store.load_session(OWNER, 1800)
    assert s2.pending_ask is not None
    r2 = await agent2.handle(s2, "[choice] Sat 17 Oct", ui)
    first = client2.requests[0]["messages"]
    answer = first[-1]["content"]
    # earlier tool results are delivered together with the ask_user answer, in one user message
    assert answer[-1]["type"] == "tool_result" and answer[-1]["content"] == "User answered: [choice] Sat 17 Oct"
    assert len(ui.cards) == 1 and backend.writes == []
    p = ui.cards[0]
    assert p.payload["event"]["start"] == "2026-10-17T10:00:00+08:00"
    assert r2.replies == ["Tap ✅ to confirm."]
    assert store.load_session(OWNER, 1800).pending_ask is None


async def test_request_shape(settings, store, backend, proposals):
    agent, client = make_agent(settings, store, backend, proposals, [msg("hi")])
    await agent.handle(store.load_session(OWNER, 1800), "hello", FakeUI())
    req = client.requests[0]
    assert req["model"] == "claude-sonnet-5-5" and req["fallbacks"] == "default"
    assert req["betas"] == ["server-side-fallback-2026-07-01"]
    assert "tool_choice" not in req and "thinking" not in req
    strict = {t["name"] for t in req["tools"] if t.get("strict")}
    assert strict == {"propose_create_event", "propose_update_event", "propose_delete_event"}


async def test_validation_error_goes_back_to_model(settings, store, backend, proposals):  # E19/E24
    agent, client = make_agent(settings, store, backend, proposals, [
        msg(("propose_create_event", {"on_date": "2026-10-10", "start_time": "10:00"})),
        msg("Where is the lesson?"),
    ])
    r = await agent.handle(store.load_session(OWNER, 1800), "tennis lesson saturday 10am", FakeUI())
    err = client.requests[1]["messages"][-1]["content"][0]
    assert err["is_error"] and err["content"].startswith("Missing: location")
    assert r.replies == ["Where is the lesson?"]


async def test_injected_event_id_rejected(settings, store, backend, proposals):  # E16
    ui = FakeUI()
    agent, client = make_agent(settings, store, backend, proposals, [
        msg(("propose_delete_event", {"event_id": "made_up_123"})),
        msg("I couldn't find that event."),
    ])
    await agent.handle(store.load_session(OWNER, 1800), "delete it", ui)
    assert ui.cards == [] and store.db.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 0


async def test_commit_note_reaches_next_turn(settings, store, backend, proposals):
    s = store.load_session(OWNER, 1800)
    s.notes.append("Committed: event created (event_id=abc).")
    store.save_session(s)
    agent, client = make_agent(settings, store, backend, proposals, [msg("ok")])
    await agent.handle(store.load_session(OWNER, 1800), "move it to 11", FakeUI())
    msgs = client.requests[0]["messages"]
    assert msgs[-2]["role"] == "user" and msgs[-1] == {"role": "system", "content": "Committed: event created (event_id=abc)."}
    assert store.load_session(OWNER, 1800).notes == []


async def test_history_is_append_only(settings, store, backend, proposals):
    agent, client = make_agent(settings, store, backend, proposals, [msg("one"), msg("two")])
    s = store.load_session(OWNER, 1800)
    await agent.handle(s, "a", FakeUI())
    await agent.handle(store.load_session(OWNER, 1800), "b", FakeUI())
    first, second = client.requests[0]["messages"], client.requests[1]["messages"]
    assert second[:len(first)] == first


async def test_auth_expired_alerts_owner(settings, store, backend, proposals):  # E10
    alerts = []

    async def on_auth(detail):
        alerts.append(detail)

    backend.auth_broken = True
    agent, client = make_agent(settings, store, backend, proposals, [
        msg(("list_events", {"time_min": "2026-10-10", "time_max": "2026-10-10"})),
        msg("Calendar access expired."),
    ], on_auth_error=on_auth)
    r = await agent.handle(store.load_session(OWNER, 1800), "what's on saturday", FakeUI())
    assert alerts and r.replies == ["Calendar access expired."]


async def test_api_error_keeps_session_unchanged(settings, store, backend, proposals):
    class Boom:
        async def create(self, **kw):
            raise anthropic.APIConnectionError(request=None)

    agent, client = make_agent(settings, store, backend, proposals, [])
    client.beta.messages = Boom()
    r = await agent.handle(store.load_session(OWNER, 1800), "hi", FakeUI())
    assert r.error == "api" and store.load_session(OWNER, 1800).messages == []


def test_tool_surface_rejects_extras():  # E11
    registry = collect_tools()
    api = [t.api_param(True) for t in registry]
    assert_tool_surface(api, registry)
    for extra in ({"name": "bash", "description": "", "input_schema": {}},
                  {"type": "web_search_20260209", "name": "web_search"}):
        with pytest.raises(ToolSurfaceError):
            assert_tool_surface(api + [extra], registry)
    with pytest.raises(ToolSurfaceError):
        assert_tool_surface(api[1:], registry)


def test_skills_in_system_prompt(settings):
    (settings.skills_dir / "demo").mkdir()
    (settings.skills_dir / "demo" / "SKILL.md").write_text("---\nname: demo\ndescription: when testing\n---\nBody here")
    prompt = build_system_prompt(settings)
    assert '<skill name="demo">' in prompt and "Body here" in prompt and "when testing" in prompt


async def test_bad_request_resets_session(settings, store, backend, proposals):
    import httpx2
    s = store.load_session(OWNER, 1800)
    s.messages = [{"role": "user", "content": "old"}]
    store.save_session(s)

    class Rejects:
        async def create(self, **kw):
            req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
            raise anthropic.BadRequestError("bad", response=httpx2.Response(400, request=req), body=None)

    agent, client = make_agent(settings, store, backend, proposals, [])
    client.beta.messages = Rejects()
    r = await agent.handle(store.load_session(OWNER, 1800), "hi", FakeUI())
    assert r.error == "bad_request" and store.load_session(OWNER, 1800).messages == []
