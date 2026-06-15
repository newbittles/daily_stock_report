"""자동매매 포지션 저장 (SQLite). 서버 재시작에도 보유·stage·리스크상태 복구.

모의(paper)·실전(real)을 db_path로 분리 운용. 리스크 레이어(risk_exit) 연동을 위해
initial_stop(초기 하드스톱)·highest(보유 최고가)·partial_taken(+1R 절반익절 여부) 영속.
"""
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
    initial_stop: float = 0.0   # 진입 하드스톱가(0=미설정, 구포지션)
    highest: float = 0.0        # 진입 후 보유 최고가(트레일링용, 0=미설정)
    partial_taken: bool = False  # +1R 절반익절 완료 여부


class PositionStore:
    def __init__(self, db_path: Path | str = DEFAULT_DB) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS paper_positions (
                ticker TEXT PRIMARY KEY, name TEXT, entry_date TEXT,
                entry_price REAL, qty INTEGER, stage INTEGER, opened INTEGER DEFAULT 1,
                strategy TEXT DEFAULT '',
                initial_stop REAL DEFAULT 0, highest REAL DEFAULT 0,
                partial_taken INTEGER DEFAULT 0
            )"""
        )
        # 구 스키마 마이그레이션 — 누락 컬럼만 ADD(기존 포지션은 기본값/wide 폴백)
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(paper_positions)")}
        for name, ddl in (
            ("strategy", "TEXT DEFAULT ''"),
            ("initial_stop", "REAL DEFAULT 0"),
            ("highest", "REAL DEFAULT 0"),
            ("partial_taken", "INTEGER DEFAULT 0"),
        ):
            if name not in cols:
                self._conn.execute(f"ALTER TABLE paper_positions ADD COLUMN {name} {ddl}")
        self._conn.commit()

    def is_held(self, ticker: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM paper_positions WHERE ticker=? AND opened=1", (ticker,)
        )
        return cur.fetchone() is not None

    def last_entry_date(self, ticker: str) -> str | None:
        """현재 보유 중인 종목의 마지막 진입일. 미보유면 None (피라미딩 중복방지용)."""
        cur = self._conn.execute(
            "SELECT entry_date FROM paper_positions WHERE ticker=? AND opened=1", (ticker,)
        )
        row = cur.fetchone()
        return row[0] if row else None

    def open_position(
        self, ticker: str, name: str, entry_date: str, entry_price: float, qty: int,
        strategy: str = "", initial_stop: float = 0.0, highest: float = 0.0,
    ) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO paper_positions
               (ticker, name, entry_date, entry_price, qty, stage, opened, strategy,
                initial_stop, highest, partial_taken)
               VALUES (?,?,?,?,?,0,1,?,?,?,0)""",
            (ticker, name, entry_date, entry_price, qty, strategy, initial_stop,
             highest or entry_price),
        )
        self._conn.commit()

    def add_to_position(
        self, ticker: str, add_qty: int, add_price: float, entry_date: str,
        initial_stop: float = 0.0,
    ) -> None:
        """피라미딩(추가매수) — 가중평균 평단·수량 합산·진입일 갱신·손절가 재설정."""
        cur = self._conn.execute(
            "SELECT entry_price, qty, highest FROM paper_positions WHERE ticker=? AND opened=1",
            (ticker,),
        )
        row = cur.fetchone()
        if row is None:  # 보유 아님 → 신규로 취급
            self.open_position(ticker, ticker, entry_date, add_price, add_qty,
                               initial_stop=initial_stop)
            return
        old_price, old_qty, old_high = row
        new_qty = old_qty + add_qty
        new_price = (old_price * old_qty + add_price * add_qty) / new_qty
        new_high = max(old_high or 0.0, add_price)
        self._conn.execute(
            """UPDATE paper_positions
               SET entry_price=?, qty=?, entry_date=?, initial_stop=?, highest=?, stage=0
               WHERE ticker=?""",
            (new_price, new_qty, entry_date, initial_stop, new_high, ticker),
        )
        self._conn.commit()

    def get_open(self) -> list[Position]:
        cur = self._conn.execute(
            "SELECT ticker,name,entry_date,entry_price,qty,stage,strategy,"
            "initial_stop,highest,partial_taken "
            "FROM paper_positions WHERE opened=1"
        )
        return [
            Position(t, n, ed, ep, q, st, strat, isp, hi, bool(pt))
            for (t, n, ed, ep, q, st, strat, isp, hi, pt) in cur.fetchall()
        ]

    def update_qty_stage(self, ticker: str, qty: int, stage: int) -> None:
        self._conn.execute(
            "UPDATE paper_positions SET qty=?, stage=? WHERE ticker=?", (qty, stage, ticker)
        )
        self._conn.commit()

    def update_risk_state(
        self, ticker: str, highest: float | None = None,
        partial_taken: bool | None = None, initial_stop: float | None = None,
    ) -> None:
        """리스크 상태 갱신 — 제공된 필드만 부분 업데이트."""
        sets, vals = [], []
        if highest is not None:
            sets.append("highest=?")
            vals.append(highest)
        if partial_taken is not None:
            sets.append("partial_taken=?")
            vals.append(1 if partial_taken else 0)
        if initial_stop is not None:
            sets.append("initial_stop=?")
            vals.append(initial_stop)
        if not sets:
            return
        vals.append(ticker)
        self._conn.execute(
            f"UPDATE paper_positions SET {', '.join(sets)} WHERE ticker=?", vals
        )
        self._conn.commit()

    def close(self, ticker: str) -> None:
        self._conn.execute(
            "UPDATE paper_positions SET opened=0, qty=0 WHERE ticker=?", (ticker,)
        )
        self._conn.commit()
