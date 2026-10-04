from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from calbot.backends.fake import FakeBackend
from calbot.config import Settings
from calbot.confirm import Proposals
from calbot.roster import Student
from calbot.store import Session, Store

SGT = ZoneInfo("Asia/Singapore")
OWNER = 42
FROZEN = datetime(2026, 10, 4, 9, 0, tzinfo=SGT)  # Sunday


@pytest.fixture
def settings(tmp_path):
    (tmp_path / "skills").mkdir()
    return Settings(_env_file=None, owner_telegram_id=OWNER, telegram_bot_token="123:abc", anthropic_api_key="x",
                    data_dir=tmp_path, skills_dir=tmp_path / "skills", roster_file=tmp_path / "roster.json",
                    now_override=FROZEN, backend="fake", timezone_label="SGT")


@pytest.fixture
def store(settings):
    return Store(settings.db_path)


@pytest.fixture
def backend():
    return FakeBackend()


ROSTER = [
    Student("Wei Ling Tan", ("WL",), "Clearwater Condo", 60),
    Student("Wei Ming Lee"),
    Student("Ahmad Rahman", (), "Kallang Court 3", 90),
    Student("Priya Nair"),
    Student("Adam Lee"),
    Student("Eve Goh"),
]


@pytest.fixture
def roster():
    return list(ROSTER)


@pytest.fixture
def proposals(settings, store, backend, roster):
    return Proposals(settings, store, backend, roster)


@pytest.fixture
def session():
    return Session(chat_id=OWNER)


def write_roster(path, students):
    path.write_text(json.dumps([{"name": s.name, "aliases": list(s.aliases), "default_location": s.default_location,
                                 "default_duration_min": s.default_duration_min} for s in students]))
