"""Talk to the agent in the terminal: real Claude, in-memory calendar, no Telegram or Google needed.

    .venv/bin/python scripts/chat.py            # uses ANTHROPIC_API_KEY from .env
    NOW_OVERRIDE=2026-10-04T09:00:00+08:00 .venv/bin/python scripts/chat.py

Type a message; answer questions by typing the option number; type 'ok <n>' / 'no <n>' / 'edit <n>' to act on
proposal card n; 'cal' shows the fake calendar; 'quit' exits."""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from calbot.backends.fake import FakeBackend  # noqa: E402
from calbot.cards import render_card  # noqa: E402
from calbot.config import Settings  # noqa: E402
from calbot.main import build  # noqa: E402


class ConsoleUI:
    def __init__(self, settings):
        self.s = settings
        self.options: list[str] = []
        self.cards: list = []

    async def send_question(self, chat_id, question, options):
        self.options = options
        print(f"\n🤖 {question}")
        for i, o in enumerate(options, 1):
            print(f"   [{i}] {o}")

    async def send_card(self, chat_id, proposal):
        self.cards.append(proposal)
        print(f"\n--- card {len(self.cards)} ({proposal.status}) ---")
        print(render_card(proposal.kind, proposal.payload, self.s.tz_label()))
        print("--- ok / no / edit", len(self.cards), "---")


async def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    settings = Settings(backend="fake", data_dir=tmp)
    settings.owner_telegram_id = settings.owner_telegram_id or 1
    store, backend, proposals, agent = build(settings, backend=FakeBackend())
    ui = ConsoleUI(settings)
    chat = settings.owner_telegram_id
    print(f"Model {settings.anthropic_model}, now = {settings.now():%a %d %b %Y %H:%M}. Ctrl-D to exit.")
    while True:
        try:
            line = input("\nyou> ").strip()
        except EOFError:
            break
        if not line:
            continue
        if line == "quit":
            break
        if line == "cal":
            for e in backend.events.values():
                print(f"  {e.start:%a %d %b %H:%M}-{e.end:%H:%M} {e.title} @ {e.location} [{e.id}]")
            continue
        parts = line.split()
        if len(parts) == 2 and parts[0] in ("ok", "no", "edit") and parts[1].isdigit():
            p = ui.cards[int(parts[1]) - 1]
            if parts[0] == "ok":
                out = await proposals.commit(p.id, chat)
                print(out.message)
                note = proposals.session_note(out)
                if note:
                    s = store.load_session(chat, 1800)
                    s.notes.append(note)
                    if out.event:
                        s.remember_event(out.event.id)
                    store.save_session(s)
            elif parts[0] == "no":
                print(proposals.cancel(p.id, chat).message)
            else:
                s = store.load_session(chat, 1800)
                s.revising_proposal_id = p.id
                store.save_session(s)
                print("🤖 What should change?")
            continue
        if line.isdigit() and ui.options and 0 < int(line) <= len(ui.options):
            line = f"[choice] {ui.options[int(line) - 1]}"
        ui.options = []
        result = await agent.handle(store.load_session(chat, 1800), line, ui)
        for r in result.replies:
            print(f"🤖 {r}")


if __name__ == "__main__":
    asyncio.run(main())
