"""PositionStore 확장 — 피라미딩(추가매수 가중평균) + 리스크상태(손절/최고가/절반익절) 영속."""
from __future__ import annotations

import sqlite3

from src.trading.positions import PositionStore


def test_risk_state_roundtrip(tmp_path):
    store = PositionStore(tmp_path / "p.db")
    store.open_position("005930", "삼성전자", "2026-06-15", 100.0, 10,
                        strategy="A", initial_stop=92.0, highest=100.0)
    pos = store.get_open()[0]
    assert pos.initial_stop == 92.0
    assert pos.highest == 100.0
    assert pos.partial_taken is False


def test_update_risk_state(tmp_path):
    store = PositionStore(tmp_path / "p.db")
    store.open_position("005930", "삼성전자", "2026-06-15", 100.0, 10, initial_stop=92.0)
    store.update_risk_state("005930", highest=130.0, partial_taken=True)
    pos = store.get_open()[0]
    assert pos.highest == 130.0
    assert pos.partial_taken is True


def test_last_entry_date(tmp_path):
    store = PositionStore(tmp_path / "p.db")
    assert store.last_entry_date("005930") is None
    store.open_position("005930", "삼성전자", "2026-06-12", 100.0, 10)
    assert store.last_entry_date("005930") == "2026-06-12"


def test_pyramid_add_weighted_average(tmp_path):
    store = PositionStore(tmp_path / "p.db")
    store.open_position("005930", "삼성전자", "2026-06-12", 100.0, 10, initial_stop=92.0)
    # 다른 날 재추천 → 추가매수: 10주@100 + 10주@120 = 20주@110(가중평균)
    store.add_to_position("005930", add_qty=10, add_price=120.0,
                          entry_date="2026-06-15", initial_stop=110.4)
    pos = store.get_open()[0]
    assert pos.qty == 20
    assert pos.entry_price == 110.0          # (100*10 + 120*10)/20
    assert pos.entry_date == "2026-06-15"    # 마지막 매수일로 갱신
    assert pos.initial_stop == 110.4         # 새 평단 기준 재산정값(호출자 계산)


def test_old_schema_migration_defaults(tmp_path):
    """구 스키마(리스크 컬럼 없음) DB도 안전하게 열려 기본값으로 복구."""
    old = tmp_path / "old.db"
    conn = sqlite3.connect(str(old))
    conn.execute(
        """CREATE TABLE paper_positions (
            ticker TEXT PRIMARY KEY, name TEXT, entry_date TEXT,
            entry_price REAL, qty INTEGER, stage INTEGER, opened INTEGER DEFAULT 1,
            strategy TEXT DEFAULT ''
        )"""
    )
    conn.execute(
        "INSERT INTO paper_positions VALUES ('000660','SK하이닉스','2026-06-01',180000.0,5,0,1,'C')"
    )
    conn.commit()
    conn.close()
    store = PositionStore(old)
    pos = store.get_open()[0]
    assert pos.ticker == "000660"
    assert pos.initial_stop == 0.0
    assert pos.highest == 0.0
    assert pos.partial_taken is False
