"""SQLite state: proposals, sessions, daily counters, health info (spec D9)."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    kind TEXT NOT NULL,              -- create | update | delete
    status TEXT NOT NULL,            -- pending | committing | committed | cancelled | expired | superseded | failed
    action_json TEXT NOT NULL,       -- the validated Action (schemas.py)
    payload_json TEXT NOT NULL,      -- what commit() will send to the backend, plus card data
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    message_id INTEGER,              -- Telegram message holding the confirm card
    result_json TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    chat_id INTEGER PRIMARY KEY,
    data_json TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS counters (
    day TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (day, chat_id)
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass
class Proposal:
    id: str
    chat_id: int
    kind: str
    status: str
    action: dict
    payload: dict
    created_at: float
    updated_at: float
    message_id: int | None = None
    result: dict | None = None


@dataclass
class Session:
    chat_id: int
    messages: list = field(default_factory=list)          # Anthropic message params, replayed verbatim
    pending_ask: dict | None = None                       # {"tool_use_id", "other_results"} while awaiting an answer
    notes: list[str] = field(default_factory=list)        # trusted notes for the next turn (commits, revisions)
    known_event_ids: list[str] = field(default_factory=list)  # ID-provenance set (spec §5.8)
    revising_proposal_id: str | None = None
    updated_at: float = 0.0

    def to_json(self) -> str:
        return json.dumps({
            "messages": self.messages, "pending_ask": self.pending_ask, "notes": self.notes,
            "known_event_ids": self.known_event_ids, "revising_proposal_id": self.revising_proposal_id,
        })

    def remember_event(self, event_id: str) -> None:
        if event_id not in self.known_event_ids:
            self.known_event_ids.append(event_id)


class Store:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    # ---------- proposals ----------
    def add_proposal(self, chat_id: int, kind: str, action: dict, payload: dict) -> Proposal:
        now = time.time()
        p = Proposal(id=uuid.uuid4().hex, chat_id=chat_id, kind=kind, status="pending",
                     action=action, payload=payload, created_at=now, updated_at=now)
        self.db.execute(
            "INSERT INTO proposals (id, chat_id, kind, status, action_json, payload_json, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (p.id, chat_id, kind, p.status, json.dumps(action), json.dumps(payload), now, now))
        return p

    def get_proposal(self, pid: str) -> Proposal | None:
        r = self.db.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
        if not r:
            return None
        return Proposal(id=r["id"], chat_id=r["chat_id"], kind=r["kind"], status=r["status"],
                        action=json.loads(r["action_json"]), payload=json.loads(r["payload_json"]),
                        created_at=r["created_at"], updated_at=r["updated_at"], message_id=r["message_id"],
                        result=json.loads(r["result_json"]) if r["result_json"] else None)

    def transition(self, pid: str, from_status: str | tuple[str, ...], to_status: str,
                   result: dict | None = None) -> bool:
        """Atomic compare-and-set. Returns True if this caller won the transition."""
        froms = (from_status,) if isinstance(from_status, str) else from_status
        q = (f"UPDATE proposals SET status=?, updated_at=?, result_json=COALESCE(?, result_json)"
             f" WHERE id=? AND status IN ({','.join('?' * len(froms))})")
        cur = self.db.execute(q, (to_status, time.time(), json.dumps(result) if result else None, pid, *froms))
        return cur.rowcount == 1

    def set_message_id(self, pid: str, message_id: int) -> None:
        self.db.execute("UPDATE proposals SET message_id=? WHERE id=?", (message_id, pid))

    def expire_old(self, ttl_s: float) -> list[str]:
        cutoff = time.time() - ttl_s
        rows = self.db.execute("SELECT id FROM proposals WHERE status='pending' AND created_at < ?", (cutoff,)).fetchall()
        return [r["id"] for r in rows if self.transition(r["id"], "pending", "expired")]

    # ---------- sessions ----------
    def load_session(self, chat_id: int, idle_ttl_s: float) -> Session:
        r = self.db.execute("SELECT data_json, updated_at FROM sessions WHERE chat_id=?", (chat_id,)).fetchone()
        if not r or time.time() - r["updated_at"] > idle_ttl_s:
            return Session(chat_id=chat_id)
        d = json.loads(r["data_json"])
        return Session(chat_id=chat_id, updated_at=r["updated_at"], **d)

    def save_session(self, s: Session) -> None:
        s.updated_at = time.time()
        self.db.execute(
            "INSERT INTO sessions (chat_id, data_json, updated_at) VALUES (?,?,?)"
            " ON CONFLICT(chat_id) DO UPDATE SET data_json=excluded.data_json, updated_at=excluded.updated_at",
            (s.chat_id, s.to_json(), s.updated_at))

    def reset_session(self, chat_id: int) -> None:
        self.db.execute("DELETE FROM sessions WHERE chat_id=?", (chat_id,))

    # ---------- counters / kv ----------
    def bump_daily(self, day: str, chat_id: int) -> int:
        self.db.execute(
            "INSERT INTO counters (day, chat_id, count) VALUES (?,?,1)"
            " ON CONFLICT(day, chat_id) DO UPDATE SET count=count+1", (day, chat_id))
        return self.db.execute("SELECT count FROM counters WHERE day=? AND chat_id=?", (day, chat_id)).fetchone()[0]

    def set_kv(self, key: str, value: str) -> None:
        self.db.execute("INSERT INTO kv (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, value))

    def get_kv(self, key: str) -> str | None:
        r = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None
