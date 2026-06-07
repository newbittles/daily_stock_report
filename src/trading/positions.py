"""모의 자동매매 포지션 저장 (SQLite). 서버 재시작에도 보유·stage 복구."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DB = Path("data/paper_positions.db")


@dataclass
class Position:
    ticker: str
    name: str
    entry_date: str
    entry_price: float
    qty: int
    stage: int  # 0=정상보유, 2=2차 50%청산 완료
    strategy: str = ""  # 매칭 전략 CSV("A,C") — 전략별 손절 선택용. ""=구포지션(wide 폴백)


class PositionStore:
    def __init__(self, db_path: Path | str = DEFAULT_DB) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS paper_positions (
                ticker TEXT PRIMARY KEY, name TEXT, entry_date TEXT,
                entry_price REAL, qty INTEGER, stage INTEGER, opened INTEGER DEFAULT 1,
                strategy TEXT DEFAULT ''
            )"""
        )
        # 구 스키마(strategy 컬럼 없음) 마이그레이션 — 기존 포지션은 ''(wide 폴백)
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(paper_positions)")}
        if "strategy" not in cols:
            self._conn.execute(
                "ALTER TABLE paper_positions ADD COLUMN strategy TEXT DEFAULT ''"
            )
        self._conn.commit()

    def is_held(self, ticker: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM paper_positions WHERE ticker=? AND opened=1", (ticker,)
        )
        return cur.fetchone() is not None

    def open_position(
        self, ticker: str, name: str, entry_date: str, entry_price: float, qty: int,
        strategy: str = "",
    ) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO paper_positions
               (ticker, name, entry_date, entry_price, qty, stage, opened, strategy)
               VALUES (?,?,?,?,?,0,1,?)""",
            (ticker, name, entry_date, entry_price, qty, strategy),
        )
        self._conn.commit()

    def get_open(self) -> list[Position]:
        cur = self._conn.execute(
            "SELECT ticker,name,entry_date,entry_price,qty,stage,strategy "
            "FROM paper_positions WHERE opened=1"
        )
        return [Position(*row) for row in cur.fetchall()]

    def update_qty_stage(self, ticker: str, qty: int, stage: int) -> None:
        self._conn.execute(
            "UPDATE paper_positions SET qty=?, stage=? WHERE ticker=?", (qty, stage, ticker)
        )
        self._conn.commit()

    def close(self, ticker: str) -> None:
        self._conn.execute(
            "UPDATE paper_positions SET opened=0, qty=0 WHERE ticker=?", (ticker,)
        )
        self._conn.commit()
