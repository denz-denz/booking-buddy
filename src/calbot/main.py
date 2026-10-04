"""Wiring and startup checks."""
from __future__ import annotations

import logging
import sys

import anthropic

from .agent import Agent
from .backends.base import CalendarBackend
from .backends.fake import FakeBackend
from .config import Settings
from .confirm import Proposals
from .prompts import build_system_prompt
from .roster import load_roster
from .store import Store
from .tools import collect_tools

log = logging.getLogger("calbot")


class RedactSecrets(logging.Filter):
    def __init__(self, secrets: list[str]):
        super().__init__()
        self.secrets = [s for s in secrets if s and len(s) > 8]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if any(s in msg for s in self.secrets):
            for s in self.secrets:
                msg = msg.replace(s, "[REDACTED]")
            record.msg, record.args = msg, None
        return True


def setup_logging(settings: Settings) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactSecrets([settings.telegram_bot_token.get_secret_value(),
                                     settings.anthropic_api_key.get_secret_value()]))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    logging.getLogger("httpx").setLevel(logging.WARNING)  # PTB logs full URLs incl. the bot token at INFO
    logging.getLogger("httpx2").setLevel(logging.WARNING)


def make_backend(settings: Settings) -> CalendarBackend:
    if settings.backend == "fake":
        log.warning("BACKEND=fake: events live in memory only")
        return FakeBackend()
    from .backends.google_api import GoogleApiBackend
    return GoogleApiBackend(settings.google_token_file, settings.calendar_id, settings.tz)


async def check_backend(backend: CalendarBackend) -> None:
    if hasattr(backend, "check_write_access"):
        role = await backend.check_write_access()
        if role not in ("owner", "writer"):
            raise SystemExit(f"No write access to the configured calendar (accessRole={role!r}).")
        log.info("Calendar access OK (accessRole=%s)", role)


def build(settings: Settings, backend: CalendarBackend | None = None, client=None):
    store = Store(settings.db_path)
    backend = backend or make_backend(settings)
    roster = load_roster(settings.roster_file)
    proposals = Proposals(settings, store, backend, roster)
    client = client or anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value() or None,
                                                max_retries=2, timeout=60.0)
    agent = Agent(settings, store, backend, proposals, client, collect_tools(), build_system_prompt(settings))
    return store, backend, proposals, agent


def main() -> None:
    settings = Settings()
    setup_logging(settings)
    missing = [n for n, v in [("TELEGRAM_BOT_TOKEN", settings.telegram_bot_token.get_secret_value()),
                              ("OWNER_TELEGRAM_ID", settings.owner_telegram_id),
                              ("ANTHROPIC_API_KEY", settings.anthropic_api_key.get_secret_value())] if not v]
    if missing:
        raise SystemExit(f"Missing required settings: {', '.join(missing)} (see .env.example)")

    store, backend, proposals, agent = build(settings)  # tool-surface assertion runs in Agent.__init__
    log.info("Tools exposed to the model: %s", ", ".join(t["name"] for t in agent.api_tools))
    expired = store.expire_old(settings.proposal_ttl_min * 60)
    if expired:
        log.info("Expired %d stale proposals", len(expired))

    from .telegram_app import Bot
    async def post_init(_app) -> None:
        await check_backend(backend)

    Bot(settings, store, agent, proposals, post_init=post_init).run()


if __name__ == "__main__":
    main()
