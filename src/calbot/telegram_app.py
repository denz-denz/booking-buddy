"""Telegram layer (spec §5.1): owner allowlist, per-chat locks, cards and buttons."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import defaultdict
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from .agent import Agent
from .cards import BUTTONS, DONE_LABEL, render_card
from .config import Settings
from .confirm import Proposals
from .store import Proposal, Store

log = logging.getLogger(__name__)

TG_MAX = 4096  # Bot API message length limit
AUTH_ALERT_EVERY_S = 3600

HELP = (
    "Tell me about events in plain language, e.g.\n"
    "• tennis lesson next saturday 10am at clearwater condo\n"
    "• lesson for adam and eve this coming sat 10am at clearwater\n"
    "• move my saturday lesson to 11\n"
    "• cancel tomorrow's lesson with ahmad\n"
    "• what's on this week?\n\n"
    "Nothing is written to your calendar until you tap ✅.\n"
    "/reset clears the conversation, /status shows health."
)


def split_message(text: str, limit: int = TG_MAX) -> list[str]:
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        cut = cut if cut > 0 else limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks + [text] if text else chunks


class TelegramUI:
    """Implements tools.base.UI on top of the Bot API."""

    def __init__(self, send, store: Store, settings: Settings):
        self.send = send
        self.store = store
        self.s = settings

    async def send_question(self, chat_id: int, question: str, options: list[str]) -> None:
        self.store.set_kv(f"ask:{chat_id}", json.dumps(options))
        markup = None
        if options:
            markup = InlineKeyboardMarkup([[InlineKeyboardButton(o, callback_data=f"a:{i}")] for i, o in enumerate(options)])
        await self.send(chat_id, question, reply_markup=markup)

    async def send_card(self, chat_id: int, proposal: Proposal) -> None:
        text, markup = card_view(proposal, self.s)
        msg = await self.send(chat_id, text, reply_markup=markup)
        self.store.set_message_id(proposal.id, msg.message_id)


def card_view(p: Proposal, s: Settings) -> tuple[str, InlineKeyboardMarkup | None]:
    text = render_card(p.kind, p.payload, s.tz_label())
    if p.status == "pending":
        row = [InlineKeyboardButton(label, callback_data=f"p:{code}:{p.id}") for label, code in BUTTONS[p.kind]]
        return text, InlineKeyboardMarkup([row])
    footer = {"committed": DONE_LABEL[p.kind], "cancelled": "❌ Cancelled", "expired": "⌛ Expired",
              "superseded": "↪️ Replaced by a newer proposal", "failed": "⚠️ Not changed"}.get(p.status, p.status)
    if p.status == "committed" and p.result and p.result.get("html_link"):
        footer += f"\n{p.result['html_link']}"
    return f"{text}\n\n{footer}", None


class Bot:
    def __init__(self, settings: Settings, store: Store, agent: Agent, proposals: Proposals, post_init=None):
        self.s = settings
        self.store = store
        self.agent = agent
        self.proposals = proposals
        self.locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        builder = Application.builder().token(settings.telegram_bot_token.get_secret_value())
        if post_init:
            builder = builder.post_init(post_init)
        self.app = builder.build()
        self.ui = TelegramUI(self.send, store, settings)
        agent.on_auth_error = self.alert_auth

        private = filters.ChatType.PRIVATE
        self.app.add_handler(CommandHandler("start", self.cmd_help, filters=private))
        self.app.add_handler(CommandHandler("help", self.cmd_help, filters=private))
        self.app.add_handler(CommandHandler("reset", self.cmd_reset, filters=private))
        self.app.add_handler(CommandHandler("status", self.cmd_status, filters=private))
        self.app.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, self.on_text))
        self.app.add_handler(CallbackQueryHandler(self.on_callback))

    async def send(self, chat_id: int, text: str, **kw):
        return await self.app.bot.send_message(chat_id, text, **kw)

    # ---------- auth ----------
    def is_owner(self, update: Update) -> bool:
        user = update.effective_user
        ok = user is not None and user.id == self.s.owner_telegram_id
        if not ok:
            log.warning("Ignored update from non-owner user id=%s", user.id if user else None)
        return ok

    async def alert_auth(self, detail: str) -> None:
        last = float(self.store.get_kv("auth_alert_at") or 0)
        self.store.set_kv("token_health", f"FAILED at {datetime.now(self.s.tz):%Y-%m-%d %H:%M}: {detail}")
        if time.time() - last < AUTH_ALERT_EVERY_S:
            return
        self.store.set_kv("auth_alert_at", str(time.time()))
        await self.send(
            self.s.owner_telegram_id,
            "⚠️ Google Calendar auth expired or was revoked. Re-run scripts/google_auth.py and redeploy the token.")

    # ---------- commands ----------
    async def cmd_help(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if self.is_owner(update):
            await update.effective_chat.send_message(HELP)

    async def cmd_reset(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_owner(update):
            return
        async with self.locks[update.effective_chat.id]:
            self.store.reset_session(update.effective_chat.id)
        await update.effective_chat.send_message("Conversation cleared.")

    async def cmd_status(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_owner(update):
            return
        backend = self.agent.backend
        age = backend.token_age_days() if hasattr(backend, "token_age_days") else None
        lines = [
            f"Model: {self.s.anthropic_model} (effort {self.s.anthropic_effort})",
            f"Timezone: {self.s.timezone}",
            f"Backend: {backend.name} · calendar {self.s.calendar_id}",
            f"Confirm mode: {self.s.confirm_mode} · next-weekday policy: {self.s.next_weekday_policy}",
            f"Google token: {self.store.get_kv('token_health') or 'ok'}"
            + (f" · authorized {age:.1f} days ago" if age is not None else ""),
            f"Last successful calendar call: {self.store.get_kv('last_calendar_ok') or 'none yet'}",
        ]
        await update.effective_chat.send_message("\n".join(lines))

    # ---------- messages ----------
    async def on_text(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.is_owner(update):
            return
        await self.run_turn(update.effective_chat.id, update.message.text)

    async def run_turn(self, chat_id: int, text: str) -> None:
        day = datetime.now(self.s.tz).date().isoformat()
        if self.store.bump_daily(day, chat_id) > self.s.daily_message_cap:
            await self.send(chat_id, "Daily message limit reached. Try again tomorrow.")
            return
        async with self.locks[chat_id]:
            typing = asyncio.create_task(self._typing(chat_id))
            try:
                session = self.store.load_session(chat_id, self.s.session_idle_ttl_min * 60)
                result = await self.agent.handle(session, text, self.ui)
            finally:
                typing.cancel()
        for reply in result.replies:
            for chunk in split_message(reply):
                await self.send(chat_id, chunk)

    async def _typing(self, chat_id: int) -> None:
        with contextlib.suppress(Exception):
            while True:
                await self.app.bot.send_chat_action(chat_id, ChatAction.TYPING)
                await asyncio.sleep(4.5)

    # ---------- buttons ----------
    async def on_callback(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        if not self.is_owner(update):
            return  # no answer at all: don't confirm the bot exists
        await q.answer()
        chat_id = q.message.chat.id
        data = q.data or ""
        if data.startswith("a:"):
            options = json.loads(self.store.get_kv(f"ask:{chat_id}") or "[]")
            idx = int(data[2:]) if data[2:].isdigit() else -1
            if not 0 <= idx < len(options):
                await q.edit_message_reply_markup(None)
                return
            choice = options[idx]
            with contextlib.suppress(BadRequest):
                await q.edit_message_text(f"{q.message.text}\n→ {choice}")
            await self.run_turn(chat_id, f"[choice] {choice}")
            return
        if data.startswith("p:"):
            _, code, pid = data.split(":", 2)
            await self.on_proposal_button(chat_id, q, code, pid, update.effective_user.id)

    async def on_proposal_button(self, chat_id: int, q, code: str, pid: str, user_id: int) -> None:
        async with self.locks[chat_id]:
            if code == "ok":
                outcome = await self.proposals.commit(pid, user_id)
                if outcome.auth_expired:
                    await self.alert_auth(outcome.message)
                note = self.proposals.session_note(outcome)
                if note:
                    session = self.store.load_session(chat_id, self.s.session_idle_ttl_min * 60)
                    session.notes.append(note)
                    if outcome.event:
                        session.remember_event(outcome.event.id)
                    self.store.save_session(session)
                    self.store.set_kv("last_calendar_ok", datetime.now(self.s.tz).strftime("%Y-%m-%d %H:%M"))
            elif code == "no":
                outcome = self.proposals.cancel(pid, user_id)
            elif code == "ed":
                p = self.store.get_proposal(pid)
                if p and p.status == "pending":
                    session = self.store.load_session(chat_id, self.s.session_idle_ttl_min * 60)
                    session.revising_proposal_id = pid
                    self.store.save_session(session)
                    await self.send(chat_id, "What should change?")
                return
            else:
                return
        p = self.store.get_proposal(pid)
        if p:
            text, markup = card_view(p, self.s)
            if not outcome.ok and p.status != "committed":
                text += f"\n\n⚠️ {outcome.message}"
            with contextlib.suppress(BadRequest):
                await q.edit_message_text(text, reply_markup=markup)
        else:
            await self.send(chat_id, outcome.message)

    def run(self) -> None:
        self.app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)
