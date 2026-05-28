from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass


@dataclass
class WatchItem:
    ticker: str
    name: str
    added_at: str
    conditions: dict


@dataclass
class TradeRecord:
    ticker: str
    name: str
    trade_type: str  # BUY | SELL
    price: float
    quantity: int
    trade_date: str
    order_no: str | None = None
    id: int | None = None
    synced_at: str | None = None


@dataclass
class SignalRecord:
    ticker: str
    signal_type: str  # "buy" | "watch" | "none"
    pattern: str
    score: float
    reasons: list[str]
    id: int | None = None
    created_at: str | None = None


class WatchlistRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, ticker: str, name: str, conditions: dict | None = None) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO watchlist (ticker, name, conditions) VALUES (?, ?, ?)",
            (ticker, name, json.dumps(conditions or {})),
        )
        self._conn.commit()

    def remove(self, ticker: str) -> bool:
        cur = self._conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker,))
        self._conn.commit()
        return cur.rowcount > 0

    def get_all(self) -> list[WatchItem]:
        rows = self._conn.execute(
            "SELECT ticker, name, added_at, conditions FROM watchlist ORDER BY added_at"
        ).fetchall()
        return [
            WatchItem(
                ticker=r["ticker"],
                name=r["name"],
                added_at=r["added_at"],
                conditions=json.loads(r["conditions"]),
            )
            for r in rows
        ]

    def exists(self, ticker: str) -> bool:
        return bool(
            self._conn.execute(
                "SELECT 1 FROM watchlist WHERE ticker = ?", (ticker,)
            ).fetchone()
        )


class SignalLogRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def insert(self, record: SignalRecord) -> int:
        cur = self._conn.execute(
            """INSERT INTO signal_log (ticker, signal_type, pattern, score, reasons)
               VALUES (?, ?, ?, ?, ?)""",
            (
                record.ticker,
                record.signal_type,
                record.pattern,
                record.score,
                json.dumps(record.reasons),
            ),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]


class AlertHistoryRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def insert(
        self,
        chat_id: str,
        alert_type: str,
        message: str,
        ticker: str | None = None,
    ) -> None:
        self._conn.execute(
            """INSERT INTO alert_history (chat_id, alert_type, ticker, message)
               VALUES (?, ?, ?, ?)""",
            (chat_id, alert_type, ticker, message),
        )
        self._conn.commit()


class TradeHistoryRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert(self, record: TradeRecord) -> None:
        """order_no 기준 중복 무시 (UNIQUE ON CONFLICT IGNORE)."""
        self._conn.execute(
            """INSERT OR IGNORE INTO trade_history
               (ticker, name, trade_type, price, quantity, trade_date, order_no)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (record.ticker, record.name, record.trade_type,
             record.price, record.quantity, record.trade_date, record.order_no),
        )
        self._conn.commit()

    def insert(self, record: TradeRecord) -> int:
        """order_no 없는 수동 입력용."""
        cur = self._conn.execute(
            """INSERT INTO trade_history
               (ticker, name, trade_type, price, quantity, trade_date, order_no)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (record.ticker, record.name, record.trade_type,
             record.price, record.quantity, record.trade_date, record.order_no),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_recent(self, limit: int = 20) -> list[TradeRecord]:
        rows = self._conn.execute(
            """SELECT id, ticker, name, trade_type, price, quantity, trade_date, order_no, synced_at
               FROM trade_history ORDER BY trade_date DESC, id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [_row_to_trade(r) for r in rows]

    def get_sells(self) -> list[TradeRecord]:
        """whatif 분석용: 전체 매도 내역."""
        rows = self._conn.execute(
            """SELECT id, ticker, name, trade_type, price, quantity, trade_date, order_no, synced_at
               FROM trade_history WHERE trade_type = 'SELL'
               ORDER BY trade_date DESC""",
        ).fetchall()
        return [_row_to_trade(r) for r in rows]

    def last_sync_date(self) -> str | None:
        row = self._conn.execute(
            "SELECT MAX(synced_at) as last FROM trade_history"
        ).fetchone()
        return row["last"] if row else None


def _row_to_trade(r: sqlite3.Row) -> TradeRecord:
    return TradeRecord(
        id=r["id"],
        ticker=r["ticker"],
        name=r["name"],
        trade_type=r["trade_type"],
        price=r["price"],
        quantity=r["quantity"],
        trade_date=r["trade_date"],
        order_no=r["order_no"],
        synced_at=r["synced_at"],
    )


class AnalysisCacheRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, key: str) -> dict | None:
        row = self._conn.execute(
            """SELECT payload FROM analysis_cache
               WHERE cache_key = ?
               AND datetime(created_at, '+' || ttl_sec || ' seconds') > datetime('now')""",
            (key,),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["payload"])  # type: ignore[index]

    def set(self, key: str, payload: dict, ttl_sec: int = 300) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO analysis_cache (cache_key, payload, ttl_sec)
               VALUES (?, ?, ?)""",
            (key, json.dumps(payload), ttl_sec),
        )
        self._conn.commit()

    def purge_expired(self) -> int:
        cur = self._conn.execute(
            """DELETE FROM analysis_cache
               WHERE datetime(created_at, '+' || ttl_sec || ' seconds') <= datetime('now')"""
        )
        self._conn.commit()
        return cur.rowcount
