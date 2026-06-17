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
    def __init__(self, price, closes, quote_fails=False):
        self._price = price
        self._closes = closes
        self._quote_fails = quote_fails  # quote 500 장애 재현(#492)

    async def get_quote(self, ticker):
        if self._quote_fails:
            raise RuntimeError("inquire-price 500")
        return _FakeQuote(self._price)

    async def get_ohlcv(self, ticker, days=100):
        return [_FakeCandle(c) for c in self._closes]

    async def get_price_safe(self, ticker):
        try:
            q = await self.get_quote(ticker)
            if q.price > 0:
                return float(q.price)
        except Exception:  # noqa: BLE001
            pass
        c = await self.get_ohlcv(ticker)
        return float(c[-1].close) if c else 0.0


class FakeOrder:
    def __init__(self):
        self.calls = []
        self._last_qty = {}  # ticker → 마지막 주문수량(체결확인이 전량체결로 회신하도록)

    async def ensure_token(self):  # 루프 전 토큰 1회 선발급(연타 방지, 2026-06-17) — 테스트는 no-op
        return None

    async def inquire_psbl_order(self, ticker, price=0, ord_dvsn="01"):
        return {"output": {"nrcvb_buy_qty": "999"}}

    async def order_cash(self, side, ticker, qty, price=0, ord_dvsn="01"):
        self.calls.append((side, ticker, qty))
        self._last_qty[ticker] = qty
        return {"output": {"ODNO": "1"}, "msg1": "정상"}

    async def confirm_fill(self, ticker, odno, *, today=""):
        # 전량 체결, 평균가 0 → auto_trader가 접수가(현재가)로 폴백(기존 단가 assertion 보존)
        return {"filled_qty": self._last_qty.get(ticker, 0), "avg_price": 0.0,
                "ord_qty": self._last_qty.get(ticker, 0), "rmn_qty": 0}


async def test_buy_sets_initial_stop_and_pyramids(tmp_path):
    """매수 시 하드스톱(−8%) 설정 + 다른 날 재추천이면 추가매수(피라미딩 가중평균)."""
    store = PositionStore(tmp_path / "p.db")
    order = FakeOrder()
    picks = [{"ticker": "005930", "name": "삼성전자", "price": 100000, "strategies": ["B"]}]
    # 1일차 매수 → 신규 진입, 하드스톱 92,000(−8%, ATR 없음)
    await buy_top3(picks, FakeAdapter(100000, []), order, store, send=True, today="2026-06-12")
    pos = store.get_open()[0]
    assert pos.qty == 10 and pos.initial_stop == 92000.0
    # 같은 날 재실행 → 중복매수 금지
    await buy_top3(picks, FakeAdapter(100000, []), order, store, send=True, today="2026-06-12")
    assert store.get_open()[0].qty == 10
    # 다른 날 재추천 → 추가매수(피라미딩): 10주@100k + qty@120k 가중평균
    picks2 = [{"ticker": "005930", "name": "삼성전자", "price": 120000, "strategies": ["B"]}]
    await buy_top3(picks2, FakeAdapter(120000, []), order, store, send=True, today="2026-06-15")
    assert store.get_open()[0].qty == 18  # 10 + 8(120k 예산 8주)


class FakeBalanceOrder(FakeOrder):
    """실전 잔고조회(inquire_balance) 흉내 — 계좌 보유 2종목."""
    async def inquire_balance(self):
        return {"output1": [
            {"pdno": "005930", "prdt_name": "삼성전자", "hldg_qty": "20", "pchs_avg_pric": "70000"},
            {"pdno": "000660", "prdt_name": "SK하이닉스", "hldg_qty": "5", "pchs_avg_pric": "180000"},
        ]}


async def test_sync_account_bootstraps_external_holdings(tmp_path):
    """scope-B: 봇이 안 산 실전계좌 보유도 store에 부트스트랩(평단·하드스톱 산정)."""
    from src.trading.auto_trader import sync_account_to_store

    store = PositionStore(tmp_path / "p.db")
    store.open_position("000660", "SK하이닉스", "2026-06-01", 180000.0, 5)  # 이미 추적 중
    order = FakeBalanceOrder()
    await sync_account_to_store(order, FakeAdapter(0, []), store, "2026-06-15")
    held = {p.ticker: p for p in store.get_open()}
    assert "005930" in held                       # 외부 보유 신규 편입
    assert held["005930"].qty == 20
    assert held["005930"].entry_price == 70000.0
    assert held["005930"].initial_stop == 64400.0  # 70000×0.92 (ATR 없음 → 퍼센트 손절)


async def test_run_sell_risk_hard_stop_fires(tmp_path):
    """리스크 레이어: 하드스톱(초기 −8%) 이탈 시 MA가 HOLD여도 전량 매도."""
    store = PositionStore(tmp_path / "p.db")
    # 진입 100, 하드스톱 92. 종가가 91로 떨어지나 MA추세는 HOLD인 시계열
    store.open_position("005930", "삼성전자", "2026-06-01", 100.0, 10,
                        strategy="C", initial_stop=92.0, highest=100.0)
    order = FakeOrder()
    adapter = FakeAdapter(price=0, closes=[float(i) for i in range(1, 79)] + [91.0])
    await run_sell(adapter, order, store, send=True)
    assert order.calls == [("sell", "005930", 10)]
    assert not store.is_held("005930")


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
    assert any("신규매수" in m and "삼성전자" in m for m in msgs)


async def test_buy_dry_run_no_order(tmp_path):
    store = PositionStore(tmp_path / "p.db")
    order = FakeOrder()
    picks = [{"ticker": "005930", "name": "삼성전자", "price": 82500}]
    await buy_top3(picks, FakeAdapter(82500, []), order, store, send=False, today="2026-06-04")
    assert order.calls == []            # dry-run: 주문 없음
    assert not store.is_held("005930")  # 기록도 없음


async def test_buy_falls_back_to_ohlcv_when_quote_500(tmp_path):
    """quote 500 장애여도 일봉 현재가로 매수 진행 (#492). 종일 quote 장애 대응."""
    store = PositionStore(tmp_path / "p.db")
    order = FakeOrder()
    # quote 실패 + 일봉 마지막 종가 82500 → 12주 매수
    adapter = FakeAdapter(price=0, closes=[80000.0, 82500.0], quote_fails=True)
    picks = [{"ticker": "005930", "name": "삼성전자", "price": 82500}]
    await buy_top3(picks, adapter, order, store, send=True, today="2026-06-08")
    assert order.calls == [("buy", "005930", 12)]
    assert store.get_open()[0].entry_price == 82500.0


async def test_buy_skips_when_price_unavailable(tmp_path):
    """quote·일봉 모두 실패 시 매수 스킵(주문 없음)."""
    store = PositionStore(tmp_path / "p.db")
    order = FakeOrder()
    adapter = FakeAdapter(price=0, closes=[], quote_fails=True)  # 폴백도 빈 일봉
    picks = [{"ticker": "005930", "name": "삼성전자", "price": 82500}]
    await buy_top3(picks, adapter, order, store, send=True, today="2026-06-08")
    assert order.calls == []
    assert not store.is_held("005930")


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


async def test_buy_error_notifies_and_continues(tmp_path):
    """주문 예외 → ⚠️ 텔레그램 알림 + 다음 종목 계속 (조용한 실패 금지, 사용자 2026-06-07)."""
    class FailingOrder(FakeOrder):
        async def order_cash(self, side, ticker, qty, price=0, ord_dvsn="01"):
            if ticker == "005930":
                raise RuntimeError("boom")
            return await super().order_cash(side, ticker, qty, price, ord_dvsn)

    store = PositionStore(tmp_path / "p.db")
    msgs = []

    async def notify(m):
        msgs.append(m)

    picks = [
        {"ticker": "005930", "name": "삼성전자", "price": 82500},
        {"ticker": "000660", "name": "SK하이닉스", "price": 180000},
    ]
    await buy_top3(picks, FakeAdapter(82500, []), FailingOrder(), store,
                   send=True, today="2026-06-08", notify=notify)
    assert any("실패" in m and "005930" in m for m in msgs)   # 에러 알림
    assert store.is_held("000660")                            # 다음 종목은 계속 진행
    assert not store.is_held("005930")


async def test_run_sell_position_summary(tmp_path):
    """매도 잡 끝에 📋 포지션 현황 요약(전략·평가손익·판정) 알림 (사용자 2026-06-07)."""
    store = PositionStore(tmp_path / "p.db")
    store.open_position("005930", "삼성전자", "2026-06-01", 100.0, 10, strategy="C")
    msgs = []

    async def notify(m):
        msgs.append(m)

    # 정상 상승 시계열(HOLD) — 마지막 종가 79.0 → 진입 100 대비 -21.0%
    adapter = FakeAdapter(price=0, closes=[float(i) for i in range(1, 80)])
    await run_sell(adapter, FakeOrder(), store, send=True, notify=notify)
    summary = next((m for m in msgs if "포지션 현황" in m), None)
    assert summary is not None
    assert "삼성전자" in summary and "C" in summary and "HOLD" in summary
    assert "-21.0%" in summary


async def test_sell_error_notifies_and_continues(tmp_path):
    """매도 중 종목 1개 데이터 실패 → ⚠️ 알림 + 나머지 종목 계속."""
    class FlakyAdapter(FakeAdapter):
        async def get_ohlcv(self, ticker, days=100):
            if ticker == "005930":
                raise RuntimeError("api down")
            return await super().get_ohlcv(ticker, days)

    store = PositionStore(tmp_path / "p.db")
    store.open_position("005930", "삼성전자", "2026-06-01", 100.0, 10, strategy="C")
    store.open_position("000660", "SK하이닉스", "2026-06-01", 100.0, 5, strategy="B")
    msgs = []

    async def notify(m):
        msgs.append(m)

    # 000660은 20MA 2연속 이탈 + B(tight) → 전량 매도돼야 함
    adapter = FlakyAdapter(price=0, closes=[100.0] * 19 + [90.0, 90.0])
    order = FakeOrder()
    await run_sell(adapter, order, store, send=True, notify=notify)
    assert any("실패" in m and "005930" in m for m in msgs)
    assert ("sell", "000660", 5) in order.calls


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


# ── 체결확인(inquire-daily-ccld) 통합: 접수≠체결 보정 (2026-06-16) ──
class _ConfirmOrder(FakeOrder):
    """confirm_fill이 지정 체결결과를 회신하는 주문 더블."""
    def __init__(self, fill):
        super().__init__()
        self._fill = fill

    async def confirm_fill(self, ticker, odno, *, today=""):
        return self._fill


async def test_buy_records_confirmed_fill_qty_and_price(tmp_path):
    """체결확인되면 접수수량/현재가가 아니라 실제 체결수량·평균가로 포지션 기록."""
    store = PositionStore(tmp_path / "p.db")
    order = _ConfirmOrder({"filled_qty": 5, "avg_price": 83000.0, "ord_qty": 12, "rmn_qty": 7})
    picks = [{"ticker": "005930", "name": "삼성전자", "price": 82500, "strategies": ["C"]}]
    await buy_top3(picks, FakeAdapter(82500, []), order, store, send=True, today="2026-06-16")
    pos = store.get_open()[0]
    assert order.calls == [("buy", "005930", 12)]   # 접수는 12주
    assert pos.qty == 5                              # 실제 체결 5주만 기록
    assert pos.entry_price == 83000.0               # 실제 체결 평균가
    assert pos.initial_stop == 76360.0              # 83000×0.92 (실체결가 기준 -8%)


async def test_buy_skips_when_unfilled(tmp_path):
    """체결확인 결과 미체결(0주)이면 주문은 보냈어도 포지션을 기록하지 않는다."""
    store = PositionStore(tmp_path / "p.db")
    order = _ConfirmOrder({"filled_qty": 0, "avg_price": 0.0, "ord_qty": 12, "rmn_qty": 12})
    msgs = []

    async def notify(m):
        msgs.append(m)

    picks = [{"ticker": "005930", "name": "삼성전자", "price": 82500}]
    await buy_top3(picks, FakeAdapter(82500, []), order, store,
                   send=True, today="2026-06-16", notify=notify)
    assert order.calls == [("buy", "005930", 12)]   # 주문은 전송됨
    assert not store.is_held("005930")              # 미체결 → 미기록
    assert any("미체결" in m for m in msgs)


async def test_buy_fallback_when_confirm_errors(tmp_path):
    """체결조회 실패는 매매를 막지 않는다 — 접수가 기준으로 기록 + ⚠️ 알림(점검요)."""
    class _ErrConfirm(FakeOrder):
        async def confirm_fill(self, ticker, odno, *, today=""):
            raise RuntimeError("inquire-daily-ccld 500")

    store = PositionStore(tmp_path / "p.db")
    msgs = []

    async def notify(m):
        msgs.append(m)

    picks = [{"ticker": "005930", "name": "삼성전자", "price": 82500}]
    await buy_top3(picks, FakeAdapter(82500, []), _ErrConfirm(), store,
                   send=True, today="2026-06-16", notify=notify)
    pos = store.get_open()[0]
    assert pos.qty == 12 and pos.entry_price == 82500.0   # 접수가 폴백
    assert any("체결확인 실패" in m for m in msgs)


async def test_sell_all_unfilled_keeps_position(tmp_path):
    """매도 미체결이면 store를 닫지 않고 보유 유지(다음 회차 재시도)."""
    store = PositionStore(tmp_path / "p.db")
    store.open_position("005930", "삼성전자", "2026-06-01", 100.0, 10, strategy="B")
    order = _ConfirmOrder({"filled_qty": 0, "avg_price": 0.0, "ord_qty": 10, "rmn_qty": 10})
    msgs = []

    async def notify(m):
        msgs.append(m)

    adapter = FakeAdapter(price=0, closes=[100.0] * 19 + [90.0, 90.0])  # B(tight) 전량 청산 신호
    await run_sell(adapter, order, store, send=True, notify=notify)
    assert order.calls == [("sell", "005930", 10)]   # 주문은 전송됨
    assert store.is_held("005930")                    # 미체결 → 보유 유지
    assert any("미체결" in m for m in msgs)


async def test_sell_all_partial_fill_keeps_remainder(tmp_path):
    """매도 부분체결이면 체결분만큼만 차감하고 잔여를 보유 유지."""
    store = PositionStore(tmp_path / "p.db")
    store.open_position("005930", "삼성전자", "2026-06-01", 100.0, 10, strategy="B")
    order = _ConfirmOrder({"filled_qty": 4, "avg_price": 90.0, "ord_qty": 10, "rmn_qty": 6})
    adapter = FakeAdapter(price=0, closes=[100.0] * 19 + [90.0, 90.0])
    await run_sell(adapter, order, store, send=True)
    assert store.is_held("005930")
    assert store.get_open()[0].qty == 6   # 10 - 4(체결) = 6 잔여
