"""PositionStore SQLite 포지션 저장 테스트 (임시 DB)."""
from __future__ import annotations

from src.trading.positions import PositionStore


def test_position_lifecycle(tmp_path):
    store = PositionStore(tmp_path / "pos.db")
    assert store.is_held("005930") is False
    store.open_position("005930", "삼성전자", "2026-06-04", 82500.0, 12)
    assert store.is_held("005930") is True
    rows = store.get_open()
    assert len(rows) == 1
    assert rows[0].ticker == "005930"
    assert rows[0].qty == 12
    assert rows[0].stage == 0
    store.update_qty_stage("005930", qty=6, stage=2)
    r = store.get_open()[0]
    assert r.qty == 6 and r.stage == 2
    store.close("005930")
    assert store.is_held("005930") is False
    assert store.get_open() == []
