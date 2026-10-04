"""Telegram layer without network: auth guard, button flows, message splitting."""
from types import SimpleNamespace

import pytest

from calbot.agent import TurnResult
from calbot.schemas import parse_action
from calbot.telegram_app import Bot, split_message
from tests.conftest import OWNER


class StubAgent:
    def __init__(self, backend):
        self.backend = backend
        self.calls = []
        self.on_auth_error = None

    async def handle(self, session, text, ui):
        self.calls.append(text)
        return TurnResult(replies=[f"echo {text}"])


@pytest.fixture
def bot(settings, store, backend, proposals):
    b = Bot(settings, store, StubAgent(backend), proposals)
    b.sent = []

    async def send(chat_id, text, **kw):
        b.sent.append((chat_id, text, kw.get("reply_markup")))
        return SimpleNamespace(message_id=len(b.sent))

    async def no_typing(chat_id):
        pass

    b.send = send
    b.ui.send = send
    b._typing = no_typing
    return b


def text_update(user_id, text):
    return SimpleNamespace(effective_user=SimpleNamespace(id=user_id), effective_chat=SimpleNamespace(id=user_id),
                           message=SimpleNamespace(text=text))


class FakeQuery:
    def __init__(self, data, chat_id=OWNER, text="card"):
        self.data = data
        self.message = SimpleNamespace(chat=SimpleNamespace(id=chat_id), text=text)
        self.answered = False
        self.edits = []

    async def answer(self):
        self.answered = True

    async def edit_message_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))

    async def edit_message_reply_markup(self, markup):
        self.edits.append((None, markup))


def cb_update(user_id, q):
    return SimpleNamespace(effective_user=SimpleNamespace(id=user_id), callback_query=q)


async def test_non_owner_gets_nothing(bot):  # E9
    await bot.on_text(text_update(999, "hi"), None)
    q = FakeQuery("p:ok:whatever", chat_id=999)
    await bot.on_callback(cb_update(999, q), None)
    assert bot.agent.calls == [] and bot.sent == [] and not q.answered


async def test_owner_message_runs_agent(bot):
    await bot.on_text(text_update(OWNER, "hi"), None)
    assert bot.agent.calls == ["hi"] and bot.sent == [(OWNER, "echo hi", None)]


async def test_daily_cap(bot, settings):
    settings.daily_message_cap = 1
    await bot.on_text(text_update(OWNER, "a"), None)
    await bot.on_text(text_update(OWNER, "b"), None)
    assert bot.agent.calls == ["a"] and "limit" in bot.sent[-1][1]


async def test_option_tap_becomes_choice(bot):
    await bot.ui.send_question(OWNER, "Which Saturday?", ["Sat 10 Oct", "Sat 17 Oct"])
    markup = bot.sent[-1][2]
    assert [b.callback_data for row in markup.inline_keyboard for b in row] == ["a:0", "a:1"]
    await bot.on_callback(cb_update(OWNER, FakeQuery("a:1")), None)
    assert bot.agent.calls == ["[choice] Sat 17 Oct"]


async def test_confirm_button_commits_and_notes_session(bot, proposals, store, backend, session):
    p = await proposals.propose_create(session, parse_action(
        "book", {"on_date": "2026-10-17", "start_time": "10:00", "location": "Clearwater"}))
    await bot.ui.send_card(OWNER, p)
    datas = [b.callback_data for row in bot.sent[-1][2].inline_keyboard for b in row]
    assert all(len(d.encode()) <= 64 for d in datas)  # Telegram callback_data limit
    q = FakeQuery(f"p:ok:{p.id}")
    await bot.on_callback(cb_update(OWNER, q), None)
    assert backend.writes == [("create", p.id)]
    text, markup = q.edits[-1]
    assert text.endswith("https://calendar.example/" + p.id) and markup is None
    s = store.load_session(OWNER, 1800)
    assert s.notes and p.id in s.known_event_ids
    # second tap: nothing new written
    await bot.on_callback(cb_update(OWNER, FakeQuery(f"p:ok:{p.id}")), None)
    assert backend.writes == [("create", p.id)]


async def test_edit_button_marks_revision(bot, proposals, store, session):
    p = await proposals.propose_create(session, parse_action(
        "book", {"on_date": "2026-10-17", "start_time": "10:00", "location": "Clearwater"}))
    await bot.on_callback(cb_update(OWNER, FakeQuery(f"p:ed:{p.id}")), None)
    assert store.load_session(OWNER, 1800).revising_proposal_id == p.id
    assert bot.sent[-1][1] == "What should change?"


async def test_cancel_button(bot, proposals, store, session, backend):
    p = await proposals.propose_create(session, parse_action(
        "book", {"on_date": "2026-10-17", "start_time": "10:00", "location": "Clearwater"}))
    q = FakeQuery(f"p:no:{p.id}")
    await bot.on_callback(cb_update(OWNER, q), None)
    assert store.get_proposal(p.id).status == "cancelled" and "Cancelled" in q.edits[-1][0]
    assert backend.writes == []


def test_split_message():
    text = "\n".join(["x" * 100] * 100)
    chunks = split_message(text)
    assert all(len(c) <= 4096 for c in chunks) and "".join(chunks).replace("\n", "") == text.replace("\n", "")
    assert split_message("short") == ["short"]
