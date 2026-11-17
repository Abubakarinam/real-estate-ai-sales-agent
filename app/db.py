"""SQLite storage: conversations, messages, leads, viewings, knowledge chunks, events."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    channel TEXT NOT NULL DEFAULT 'web',
    language TEXT,
    status TEXT NOT NULL DEFAULT 'ai',          -- ai | human | closed
    handoff_reason TEXT,
    handoff_at TEXT,
    pending_note TEXT,                           -- what happened while a human had the chat
    api_messages TEXT NOT NULL DEFAULT '[]',     -- full Claude message history (append-only)
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,                          -- customer | ai | staff | system
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT UNIQUE NOT NULL,
    name TEXT, phone TEXT, email TEXT,
    language TEXT,
    intent TEXT,                                 -- buy | rent | invest
    property_type TEXT,
    areas TEXT,
    bedrooms TEXT,
    budget_min INTEGER, budget_max INTEGER,
    timeline TEXT,                               -- asap | 1-3 months | 3-6 months | 6+ months | just browsing
    financing TEXT,                              -- cash | mortgage pre-approved | mortgage not yet | not needed (renting)
    interested_listings TEXT,
    notes TEXT,
    summary TEXT,
    next_step TEXT,
    score INTEGER NOT NULL DEFAULT 0,
    temperature TEXT NOT NULL DEFAULT 'cold',    -- hot | warm | cold
    score_reasons TEXT NOT NULL DEFAULT '[]',
    stage TEXT NOT NULL DEFAULT 'new',           -- new | qualified | viewing booked | handed off | won | lost
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS viewings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL,
    conversation_id TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    start_at TEXT NOT NULL,                      -- local time, ISO 'YYYY-MM-DDTHH:MM'
    agent TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'booked',       -- booked | cancelled | done
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    heading TEXT,
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    conversation_id TEXT,
    detail TEXT,
    created_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


_conn: sqlite3.Connection | None = None


def get() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = connect()
        _conn.executescript(SCHEMA)
    return _conn


def reset_connection() -> None:
    """Used by tests that point DB_PATH somewhere else."""
    global _conn
    if _conn is not None:
        _conn.close()
    _conn = None


@contextmanager
def tx():
    with _lock:
        conn = get()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def query(sql: str, args: tuple = ()) -> list[dict]:
    with _lock:
        return [dict(r) for r in get().execute(sql, args).fetchall()]


def one(sql: str, args: tuple = ()) -> dict | None:
    rows = query(sql, args)
    return rows[0] if rows else None


def log_event(kind: str, conversation_id: str | None, detail: dict | str | None = None) -> None:
    if isinstance(detail, dict):
        detail = json.dumps(detail, ensure_ascii=False)
    with tx() as c:
        c.execute(
            "INSERT INTO events(kind, conversation_id, detail, created_at) VALUES (?,?,?,?)",
            (kind, conversation_id, detail, now()),
        )
