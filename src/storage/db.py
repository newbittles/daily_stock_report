from __future__ import annotations

import sqlite3
from pathlib import Path

_DDL = """
CREATE TABLE IF NOT EXISTS watchlist (
    ticker      TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    conditions  TEXT NOT NULL DEFAULT '{}',
    added_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS signal_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    pattern     TEXT,
    score       REAL,
    reasons     TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS alert_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     TEXT NOT NULL,
    alert_type  TEXT NOT NULL,
    ticker      TEXT,
    message     TEXT NOT NULL,
    sent_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS analysis_cache (
    cache_key   TEXT PRIMARY KEY,
    payload     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    ttl_sec     INTEGER NOT NULL DEFAULT 300
);

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trade_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    trade_type  TEXT NOT NULL CHECK(trade_type IN ('BUY', 'SELL')),
    price       REAL NOT NULL,
    quantity    INTEGER NOT NULL,
    trade_date  TEXT NOT NULL,
    order_no    TEXT,
    synced_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(order_no) ON CONFLICT IGNORE
);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    conn.commit()
