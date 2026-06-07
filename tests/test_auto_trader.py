"""auto_trader 오케스트레이션 + top3 브리지 테스트 (mock, 라이브 0)."""
from __future__ import annotations

from src.trading.auto_trader import buy_top3, run_sell
from src.trading.positions import PositionStore
from src.trading.top3_bridge import load_top3, persist_top3


def test_top3_bridge_roundtrip(tmp_path):
    picks = [
        {"ticker": "005930", "name": "삼성전자", "price": 82500, "score": 9.1, "extra": "x"},
        {"ticker": "000660", "name": "SK하이닉스", "price": 180000},
    ]
    persist_top3(picks, "pre_close", "2026-06-04", base_dir=tmp_path)
    loaded = load_top3("2026-06-04", base_dir=tmp_path)
    assert [p["ticker"] for p in loaded] == ["005930", "000660"]
    assert loaded[0]["name"] == "삼성전자"
    assert loaded[0]["price"] == 82500
    # 날짜 불일치 → None
    assert load_top3("2026-06-05", base_dir=tmp_path) is None


def test_top3_bridge_persists_strategies(tmp_path):
    """전략 라벨(ABCDE별 손절용)이 브리지 JSON에 보존돼야 한다 (2026-06-07)."""
    picks = [
        {"ticker": "005930", "name": "삼성전자", "price": 82500, "strategies": ["A", "C"]},
        {"ticker": "000660", "name": "SK하이닉스", "price": 180000},  # strategies 없음 → []
    ]
    persist_top3(picks, "pre_close", "2026-06-08", base_dir=tmp_path)
    loaded = load_top3("2026-06-08", base_dir=tmp_path)
    assert loaded[0]["strategies"] == ["A", "C"]
    assert loaded[1]["strategies"] == []


def test_position_store_strategy_and_migration(tmp_path):
    """포지션에 strategy 저장 + 구 스키마(컬럼 없음) DB 자동 마이그레이션."""
    import sqlite3

    store = PositionStore(tmp_path / "p.db")
    store.open_position("005930", "삼성전자", "2026-06-08", 100.0, 10, strategy="A,C")
    assert store.get_open()[0].strategy == "A,C"

    # 구 스키마 DB(strategy 컬럼 없음)를 열어도 깨지지 않고 빈 전략으로 복구(wide 폴백)
    old = tmp_path / "old.db"
    conn = sqlite3.connect(str(old))
    conn.execute(
        """CREATE TABLE paper_positions (
            ticker TEXT PRIMARY KEY, name TEXT, entry_date TEXT,
            entry_price REAL, qty INTEGER, stage INTEGER, opened INTEGER DEFAULT 1
        )"""
    )
    conn.execute(
        "INSERT INTO paper_positions VALUES ('000660','SK하이닉스','2026-06-01',180000.0,5,0,1)"
    )
    conn.commit()
    conn.close()
    store2 = PositionStore(old)
    pos = store2.get_open()[0]
    assert pos.ticker == "000660"
    assert pos.strategy == ""


class _FakeQuote:
    def __init__(self, price):
        self.price = price


class _FakeCandle:
    def __init__(self, close):
        self.close = close


class FakeAdapter:
    def __init__(self, price, closes):
        self._price = price
        self._closes = closes

    async def get_quote(self, ticker):
        return _FakeQuote(self._price)

    async def get_ohlcv(self, ticker, days=100):
        return [_FakeCandle(c) for c in self._closes]


class FakeOrder:
    def __init__(self):
        self.calls = []

    async def inquire_psbl_order(self, ticker, price=0, ord_dvsn="01"):
        return {"output": {"nrcvb_buy_qty": "999"}}

    async def order_cash(self, side, ticker, qty, price=0, ord_dvsn="01"):
        self.calls.append((side, ticker, qty))
        return {"output": {"ODNO": "1"}, "msg1": "정상"}


async def test_buy_top3_sizes_and_skips_held(tmp_path):
    store = PositionStore(tmp_path / "p.db")
    store.open_position("000660", "SK하이닉스", "2026-06-04", 180000.0, 5)  # 이미 보유
    adapter = FakeAdapter(price=82500, closes=[])
    order = FakeOrder()
    picks = [
        {"ticker": "005930", "name": "삼성전자", "price": 82500},
        {"ticker": "000660", "name": "SK하이닉스", "price": 180000},
    ]
    await buy_top3(picks, adapter, order, store, send=True, today="2026-06-04")
    # 보유종목(000660) skip, 005930만 12주 매수
    assert order.calls == [("buy", "005930", 12)]
    assert store.is_held("005930")


async def test_buy_emits_notify(tmp_path):
    store = PositionStore(tmp_path / "p.db")
    order = FakeOrder()
    msgs = []

    async def notify(m):
        msgs.append(m)

    picks = [{"ticker": "005930", "name": "삼성전자", "price": 82500}]
    await buy_top3(picks, FakeAdapter(82500, []), order, store,
                   send=True, today="2026-06-04", notify=notify)
    assert any("모의매수" in m for m in msgs)


async def test_buy_dry_run_no_order(tmp_path):
    store = PositionStore(tmp_path / "p.db")
    order = FakeOrder()
    picks = [{"ticker": "005930", "name": "삼성전자", "price": 82500}]
    await buy_top3(picks, FakeAdapter(82500, []), order, store, send=False, today="2026-06-04")
    assert order.calls == []            # dry-run: 주문 없음
    assert not store.is_held("005930")  # 기록도 없음


async def test_buy_top3_persists_strategy(tmp_path):
    """매수 시 picks의 strategies가 포지션 DB에 'A,C' 형태로 저장돼야 한다."""
    store = PositionStore(tmp_path / "p.db")
    picks = [{"ticker": "005930", "name": "삼성전자", "price": 82500, "strategies": ["A", "C"]}]
    await buy_top3(picks, FakeAdapter(82500, []), FakeOrder(), store, send=True, today="2026-06-08")
    assert store.get_open()[0].strategy == "A,C"


async def test_run_sell_tight_ab_full_exit(tmp_path):
    """B(tight) 포지션: 20MA 2연속 이탈 → 50%가 아니라 전량 매도."""
    store = PositionStore(tmp_path / "p.db")
    store.open_position("005930", "삼성전자", "2026-06-01", 100.0, 12, strategy="B")
    order = FakeOrder()
    adapter = FakeAdapter(price=0, closes=[100.0] * 19 + [90.0, 90.0])
    await run_sell(adapter, order, store, send=True)
    assert order.calls == [("sell", "005930", 12)]   # 전량
    assert not store.is_held("005930")


async def test_run_sell_half_then_all(tmp_path):
    store = PositionStore(tmp_path / "p.db")
    store.open_position("005930", "삼성전자", "2026-06-01", 100.0, 12)
    order = FakeOrder()
    # 20MA만 2연속 이탈 시계열 → SELL_HALF (21봉: 60MA 미발동)
    adapter = FakeAdapter(price=0, closes=[100.0] * 19 + [90.0, 90.0])
    await run_sell(adapter, order, store, send=True)
    assert order.calls == [("sell", "005930", 6)]   # 50%
    assert store.get_open()[0].stage == 2 and store.get_open()[0].qty == 6
    # 다음 회차: 60MA 2연속 이탈 → SELL_ALL(잔여 6주)
    order2 = FakeOrder()
    adapter2 = FakeAdapter(price=0, closes=[100.0] * 61 + [40.0, 40.0])
    await run_sell(adapter2, order2, store, send=True)
    assert order2.calls == [("sell", "005930", 6)]
    assert not store.is_held("005930")
