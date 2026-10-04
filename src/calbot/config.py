"""Typed settings from the environment (and an optional .env file)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- secrets / identity ---
    telegram_bot_token: SecretStr = SecretStr("")
    owner_telegram_id: int = 0
    anthropic_api_key: SecretStr = SecretStr("")

    # --- model ---
    anthropic_model: str = "claude-sonnet-5-5"
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    strict_tools: bool = True
    refusal_fallback: bool = True

    # --- behaviour ---
    timezone: str = "Asia/Singapore"
    timezone_label: str = ""  # e.g. "SGT"; empty → derived from the zone
    default_duration_min: int = 60
    default_event_title: str = "Tennis lesson"
    calendar_id: str = "primary"
    next_weekday_policy: Literal["ask", "upcoming", "following_week"] = "ask"
    confirm_mode: Literal["always", "never"] = "always"
    backend: Literal["google", "fake"] = "google"

    # --- limits ---
    max_turns: int = 8
    turn_timeout_s: float = 90.0
    daily_message_cap: int = 200
    session_idle_ttl_min: int = 30
    proposal_ttl_min: int = 30

    # --- paths ---
    data_dir: Path = REPO_ROOT / "data"
    skills_dir: Path = REPO_ROOT / "skills"
    # default to files inside data_dir when not set explicitly
    google_client_secret_file: Path | None = None
    google_token_file: Path | None = None
    roster_file: Path | None = None

    # --- testing ---
    now_override: datetime | None = None  # frozen clock for evals, e.g. 2026-10-04T09:00:00+08:00

    @model_validator(mode="after")
    def _data_paths(self):
        self.google_client_secret_file = self.google_client_secret_file or self.data_dir / "client_secret.json"
        self.google_token_file = self.google_token_file or self.data_dir / "google_token.json"
        self.roster_file = self.roster_file or self.data_dir / "roster.json"
        return self

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str) -> str:
        ZoneInfo(v)
        return v

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "calbot.sqlite3"

    def now(self) -> datetime:
        if self.now_override is not None:
            n = self.now_override
            return n.astimezone(self.tz) if n.tzinfo else n.replace(tzinfo=self.tz)
        return datetime.now(self.tz)

    def tz_label(self, dt: datetime | None = None) -> str:
        return self.timezone_label or (dt or self.now()).tzname() or self.timezone
