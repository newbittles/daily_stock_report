# 모의 자동매매 루프 (auto_trader v1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans 로 태스크 단위 구현. 체크박스(`- [ ]`)로 추적.

**Goal:** 종가베팅 Top3를 100만원 이내로 자동 매수하고, 일봉 20/60MA 2연속 이탈로 자동 청산하는 모의 루프.

**Architecture:** pre 리포트가 top3를 JSON으로 남기면(브리지) auto_trader CLI가 읽어 매수, 별도 CLI가 일봉 기반으로 청산. 순수 함수(sizing/ma_exit) + SQLite 포지션 + KisAdapter(시세)/KisOrderClient(주문) 재사용. 라이브 리포트 프로세스 비침투.

**Tech Stack:** Python 3.11 async, httpx, sqlite3, pytest+respx, KIS REST. 의존: `src.indicators.core.moving_average`, `KisAdapter.get_quote/get_ohlcv`, `src.trading.kis_order.KisOrderClient`.

---

## File Structure

| 파일 | 책임 |
|------|------|
| `src/trading/sizing.py` (생성) | 순수: `calc_qty`, `split_sell_qty` |
| `src/trading/ma_exit.py` (생성) | 순수: `consecutive_below`, `exit_decision` |
| `src/trading/positions.py` (생성) | SQLite 포지션 저장/조회 |
| `src/trading/auto_trader.py` (생성) | buy/sell 오케스트레이션 + CLI |
| `src/market_report/pipeline.py` (수정) | top3 JSON 방어적 기록 |
| `src/trading/top3_bridge.py` (생성) | top3 JSON 기록/로드 (pipeline·auto_trader 공용) |
| `tests/test_trading_pure.py` (생성) | sizing·ma_exit 단위 |
| `tests/test_positions.py` (생성) | 포지션 SQLite |
| `tests/test_auto_trader.py` (생성) | buy/sell 오케스트레이션(mock) + 브리지 |

---

## Task 1: `sizing.py` — 순수 사이징

**Files:** Create `src/trading/sizing.py`, Test `tests/test_trading_pure.py`

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_trading_pure.py
from src.trading.sizing import calc_qty, split_sell_qty


def test_calc_qty():
    assert calc_qty(82500) == 12          # 1,000,000 // 82500
    assert calc_qty(1_000_000) == 1
    assert calc_qty(1_200_000) == 0       # 1주도 예산 초과
    assert calc_qty(0) == 0
    assert calc_qty(-5) == 0
    assert calc_qty(50000, budget=500_000) == 10


def test_split_sell_qty():
    assert split_sell_qty(12) == (6, 6)
    assert split_sell_qty(11) == (5, 6)
    assert split_sell_qty(1) == (1, 0)    # 1주는 쪼갤 수 없음 → 전량
    assert split_sell_qty(2) == (1, 1)
```

- [ ] **Step 2: 실패 확인** — `python -m pytest tests/test_trading_pure.py -v` → FAIL (ModuleNotFoundError)

- [ ] **Step 3: 구현**

```python
# src/trading/sizing.py
"""주문 수량 계산 — 순수 함수 (외부 의존 없음)."""
from __future__ import annotations

DEFAULT_BUDGET = 1_000_000  # 1회 매수당 예산(원)


def calc_qty(price: float, budget: int = DEFAULT_BUDGET) -> int:
    """예산 이내 최대 정수 매수 수량. price<=0 또는 1주가 예산 초과면 0."""
    if price <= 0 or price > budget:
        return 0
    return int(budget // price)


def split_sell_qty(qty: int) -> tuple[int, int]:
    """2차 50% 분할 매도 → (지금 매도, 잔여). qty=1이면 (1,0)=전량(쪼갤 수 없음)."""
    if qty <= 1:
        return (qty, 0)
    half = qty // 2
    return (half, qty - half)
```

- [ ] **Step 4: 통과 확인** — `python -m pytest tests/test_trading_pure.py -v` → 2 passed

- [ ] **Step 5: 커밋**
```bash
git add src/trading/sizing.py tests/test_trading_pure.py
git commit -m "feat(trading): 사이징 순수함수 calc_qty/split_sell_qty"
```

---

## Task 2: `ma_exit.py` — 일봉 청산 판정 (순수)

**Files:** Create `src/trading/ma_exit.py`, Test `tests/test_trading_pure.py` (append)

- [ ] **Step 1: 실패 테스트** (append)

```python
from src.trading.ma_exit import consecutive_below, exit_decision


def test_consecutive_below():
    closes = [10, 10, 10]
    ma = [9, 11, 11]   # 최근 2개 모두 close<ma → True
    assert consecutive_below(closes, ma, 2) is True
    ma2 = [9, 9, 11]   # 마지막만 이탈 → False
    assert consecutive_below(closes, ma2, 2) is False
    assert consecutive_below([10], [9], 2) is False  # 길이 부족
    assert consecutive_below([10, 10], [None, 9], 2) is False  # MA None


def test_exit_decision():
    # 60MA 2연속 이탈(가장 심각) → SELL_ALL
    closes_all = [100] * 58 + [40, 40]
    assert exit_decision(closes_all) == "SELL_ALL"
    # 20MA만 2연속 이탈 → SELL_HALF
    closes_half = [100] * 18 + [90, 90]
    assert exit_decision(closes_half) == "SELL_HALF"
    # 정상 상승 → HOLD
    assert exit_decision([float(i) for i in range(1, 80)]) == "HOLD"
```

- [ ] **Step 2: 실패 확인** — `python -m pytest tests/test_trading_pure.py::test_exit_decision -v` → FAIL

- [ ] **Step 3: 구현**

```python
# src/trading/ma_exit.py
"""일봉 이동평균 기반 청산 판정 — 순수 함수.

2차: 20MA 2거래일 연속 종가 이탈 → 50% 매도(SELL_HALF)
3차: 60MA 2거래일 연속 종가 이탈 → 전량 매도(SELL_ALL, 우선)
"""
from __future__ import annotations

from src.indicators.core import moving_average


def consecutive_below(closes: list[float], ma: list[float | None], n: int = 2) -> bool:
    """최근 n개 종가가 모두 대응 MA 아래면 True. MA None이면 False."""
    if len(closes) < n or len(ma) < n:
        return False
    for i in range(-n, 0):
        m = ma[i]
        if m is None or closes[i] >= m:
            return False
    return True


def exit_decision(closes: list[float]) -> str:
    """일봉 종가 시계열 → 'SELL_ALL' | 'SELL_HALF' | 'HOLD'. 60MA(전량) 우선."""
    if consecutive_below(closes, moving_average(closes, 60), 2):
        return "SELL_ALL"
    if consecutive_below(closes, moving_average(closes, 20), 2):
        return "SELL_HALF"
    return "HOLD"
```

- [ ] **Step 4: 통과 확인** — `python -m pytest tests/test_trading_pure.py -v` → 4 passed

- [ ] **Step 5: 커밋**
```bash
git add src/trading/ma_exit.py tests/test_trading_pure.py
git commit -m "feat(trading): 일봉 20/60MA 2연속 이탈 청산 판정(순수)"
```

---

## Task 3: `positions.py` — SQLite 포지션

**Files:** Create `src/trading/positions.py`, Test `tests/test_positions.py`

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_positions.py
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
```

- [ ] **Step 2: 실패 확인** — `python -m pytest tests/test_positions.py -v` → FAIL

- [ ] **Step 3: 구현**

```python
# src/trading/positions.py
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


class PositionStore:
    def __init__(self, db_path: Path | str = DEFAULT_DB) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS paper_positions (
                ticker TEXT PRIMARY KEY, name TEXT, entry_date TEXT,
                entry_price REAL, qty INTEGER, stage INTEGER, opened INTEGER DEFAULT 1
            )"""
        )
        self._conn.commit()

    def is_held(self, ticker: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM paper_positions WHERE ticker=? AND opened=1", (ticker,)
        )
        return cur.fetchone() is not None

    def open_position(self, ticker: str, name: str, entry_date: str, entry_price: float, qty: int) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO paper_positions
               (ticker, name, entry_date, entry_price, qty, stage, opened)
               VALUES (?,?,?,?,?,0,1)""",
            (ticker, name, entry_date, entry_price, qty),
        )
        self._conn.commit()

    def get_open(self) -> list[Position]:
        cur = self._conn.execute(
            "SELECT ticker,name,entry_date,entry_price,qty,stage FROM paper_positions WHERE opened=1"
        )
        return [Position(*row) for row in cur.fetchall()]

    def update_qty_stage(self, ticker: str, qty: int, stage: int) -> None:
        self._conn.execute(
            "UPDATE paper_positions SET qty=?, stage=? WHERE ticker=?", (qty, stage, ticker)
        )
        self._conn.commit()

    def close(self, ticker: str) -> None:
        self._conn.execute("UPDATE paper_positions SET opened=0, qty=0 WHERE ticker=?", (ticker,))
        self._conn.commit()
```

- [ ] **Step 4: 통과 확인** — `python -m pytest tests/test_positions.py -v` → 1 passed

- [ ] **Step 5: 커밋**
```bash
git add src/trading/positions.py tests/test_positions.py
git commit -m "feat(trading): SQLite 포지션 저장 PositionStore"
```

---

## Task 4: `top3_bridge.py` — top3 JSON 기록/로드

**Files:** Create `src/trading/top3_bridge.py`, Test `tests/test_auto_trader.py`

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_auto_trader.py
from src.trading.top3_bridge import persist_top3, load_top3


def test_top3_bridge_roundtrip(tmp_path):
    picks = [
        {"ticker": "005930", "name": "삼성전자", "price": 82500, "score": 9.1, "extra": "x"},
        {"ticker": "000660", "name": "SK하이닉스", "price": 180000},
    ]
    path = persist_top3(picks, "pre_close", "2026-06-04", base_dir=tmp_path)
    loaded = load_top3("2026-06-04", base_dir=tmp_path)
    assert [p["ticker"] for p in loaded] == ["005930", "000660"]
    assert loaded[0]["name"] == "삼성전자"
    assert loaded[0]["price"] == 82500
    # 날짜 불일치 → None
    assert load_top3("2026-06-05", base_dir=tmp_path) is None
```

- [ ] **Step 2: 실패 확인** — `python -m pytest tests/test_auto_trader.py::test_top3_bridge_roundtrip -v` → FAIL

- [ ] **Step 3: 구현**

```python
# src/trading/top3_bridge.py
"""보고서 Top3 ↔ auto_trader 브리지 — pre 리포트가 남긴 top3 JSON 기록/로드.

보고서가 보여준 Top3와 자동매수 종목을 동일하게 보장(일관성)."""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)
DEFAULT_DIR = Path("data")


def _path(date: str, base_dir: Path) -> Path:
    return base_dir / f"top3_{date}_pre.json"


def persist_top3(picks: list[dict], mode: str, date: str, base_dir: Path | str = DEFAULT_DIR) -> Path:
    """ticker/name/price만 추려 JSON 기록. pre_close 전용."""
    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)
    slim = [{"ticker": p["ticker"], "name": p.get("name", ""), "price": p.get("price", 0)} for p in picks]
    path = _path(date, base)
    path.write_text(
        json.dumps({"date": date, "mode": mode, "picks": slim}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def load_top3(date: str, base_dir: Path | str = DEFAULT_DIR) -> list[dict] | None:
    """오늘자 top3 picks 로드. 파일 없거나 날짜 불일치면 None(구픽 매매 방지)."""
    path = _path(date, Path(base_dir))
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("top3_load_failed error=%s", exc)
        return None
    if data.get("date") != date:
        return None
    return data.get("picks", [])
```

- [ ] **Step 4: 통과 확인** — `python -m pytest tests/test_auto_trader.py::test_top3_bridge_roundtrip -v` → PASS

- [ ] **Step 5: 커밋**
```bash
git add src/trading/top3_bridge.py tests/test_auto_trader.py
git commit -m "feat(trading): top3 JSON 브리지(보고서-매매 일관성)"
```

---

## Task 5: `auto_trader.py` — buy/sell 오케스트레이션 + CLI

**Files:** Create `src/trading/auto_trader.py`, Test `tests/test_auto_trader.py` (append)

의존성 주입(테스트용): `buy_top3`/`run_sell`이 adapter·order_client·store·picks를 인자로 받음. CLI가 실객체 조립.

- [ ] **Step 1: 실패 테스트** (append)

```python
import pytest
from src.trading.auto_trader import buy_top3, run_sell
from src.trading.positions import PositionStore


class _FakeQuote:
    def __init__(self, price): self.price = price


class _FakeCandle:
    def __init__(self, close): self.close = close


class FakeAdapter:
    def __init__(self, price, closes): self._price = price; self._closes = closes
    async def get_quote(self, ticker): return _FakeQuote(self._price)
    async def get_ohlcv(self, ticker, days=100): return [_FakeCandle(c) for c in self._closes]


class FakeOrder:
    def __init__(self): self.calls = []
    async def inquire_psbl_order(self, ticker, price=0, ord_dvsn="01"):
        return {"output": {"nrcvb_buy_qty": "999"}}
    async def order_cash(self, side, ticker, qty, price=0, ord_dvsn="01"):
        self.calls.append((side, ticker, qty)); return {"output": {"ODNO": "1"}, "msg1": "정상"}


async def test_buy_top3_sizes_and_skips_held(tmp_path):
    store = PositionStore(tmp_path / "p.db")
    store.open_position("000660", "SK하이닉스", "2026-06-04", 180000.0, 5)  # 이미 보유
    adapter = FakeAdapter(price=82500, closes=[])
    order = FakeOrder()
    picks = [{"ticker": "005930", "name": "삼성전자", "price": 82500},
             {"ticker": "000660", "name": "SK하이닉스", "price": 180000}]
    await buy_top3(picks, adapter, order, store, send=True, today="2026-06-04")
    # 보유종목(000660) skip, 005930만 12주 매수
    assert order.calls == [("buy", "005930", 12)]
    assert store.is_held("005930")


async def test_buy_dry_run_no_order(tmp_path):
    store = PositionStore(tmp_path / "p.db")
    order = FakeOrder()
    picks = [{"ticker": "005930", "name": "삼성전자", "price": 82500}]
    await buy_top3(picks, FakeAdapter(82500, []), order, store, send=False, today="2026-06-04")
    assert order.calls == []           # dry-run: 주문 없음
    assert not store.is_held("005930")  # 기록도 없음


async def test_run_sell_half_then_all(tmp_path):
    store = PositionStore(tmp_path / "p.db")
    store.open_position("005930", "삼성전자", "2026-06-01", 100.0, 12)
    order = FakeOrder()
    # 20MA만 2연속 이탈 시계열 → SELL_HALF
    adapter = FakeAdapter(price=0, closes=[100.0] * 18 + [90.0, 90.0])
    await run_sell(adapter, order, store, send=True)
    assert order.calls == [("sell", "005930", 6)]   # 50%
    assert store.get_open()[0].stage == 2 and store.get_open()[0].qty == 6
    # 다음 회차: 60MA 2연속 이탈 → SELL_ALL(잔여 6주)
    order2 = FakeOrder()
    adapter2 = FakeAdapter(price=0, closes=[100.0] * 58 + [40.0, 40.0])
    await run_sell(adapter2, order2, store, send=True)
    assert order2.calls == [("sell", "005930", 6)]
    assert not store.is_held("005930")
```

- [ ] **Step 2: 실패 확인** — `python -m pytest tests/test_auto_trader.py -v` → FAIL (import)

- [ ] **Step 3: 구현**

```python
# src/trading/auto_trader.py
"""모의 자동매매 루프 — 종가베팅 Top3 매수 + 일봉 20/60MA 청산.

CLI: python -m src.trading.auto_trader {buy|sell} [--send]
모의(paper) 전용. dry-run 기본(--send 명시 시에만 실제 주문)."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # cp949 콘솔 보호

from src.trading.ma_exit import exit_decision  # noqa: E402
from src.trading.positions import PositionStore  # noqa: E402
from src.trading.sizing import calc_qty, split_sell_qty  # noqa: E402
from src.trading.top3_bridge import load_top3  # noqa: E402

logger = logging.getLogger(__name__)


async def buy_top3(picks, adapter, order, store, *, send: bool, today: str) -> None:
    print(f"=== auto-buy {today} · {len(picks)}종목 후보 ({'LIVE' if send else 'dry-run'}) ===")
    for p in picks:
        ticker, name = p["ticker"], p.get("name", "")
        if store.is_held(ticker):
            print(f"  skip {ticker} {name} — 이미 보유")
            continue
        quote = await adapter.get_quote(ticker)
        qty = calc_qty(quote.price)
        if qty < 1:
            print(f"  skip {ticker} {name} — 현재가 {quote.price} 1주도 예산초과")
            continue
        psbl = await order.inquire_psbl_order(ticker, price=0, ord_dvsn="01")
        max_qty = int(psbl.get("output", {}).get("nrcvb_buy_qty", "0") or "0")
        qty = min(qty, max_qty) if max_qty else qty
        if qty < 1:
            print(f"  skip {ticker} {name} — 매수가능수량 0")
            continue
        print(f"  BUY {ticker} {name} x{qty} (현재가 {quote.price}, 시장가)")
        if not send:
            continue
        res = await order.order_cash("buy", ticker, qty, price=0, ord_dvsn="01")
        print(f"    → odno={res.get('output', {}).get('ODNO')} msg={res.get('msg1')}")
        store.open_position(ticker, name, today, float(quote.price), qty)


async def run_sell(adapter, order, store, *, send: bool) -> None:
    open_pos = store.get_open()
    print(f"=== auto-sell · 보유 {len(open_pos)}종목 ({'LIVE' if send else 'dry-run'}) ===")
    for pos in open_pos:
        candles = await adapter.get_ohlcv(pos.ticker, days=80)
        closes = [c.close for c in candles]
        decision = exit_decision(closes)
        print(f"  {pos.ticker} {pos.name} qty={pos.qty} stage={pos.stage} → {decision}")
        if decision == "HOLD":
            continue
        if decision == "SELL_HALF" and pos.stage < 2:
            sell_qty, remaining = split_sell_qty(pos.qty)
            print(f"    SELL_HALF x{sell_qty} (잔여 {remaining})")
            if send:
                await order.order_cash("sell", pos.ticker, sell_qty, price=0, ord_dvsn="01")
                if remaining > 0:
                    store.update_qty_stage(pos.ticker, remaining, 2)
                else:
                    store.close(pos.ticker)
        elif decision == "SELL_ALL":
            print(f"    SELL_ALL x{pos.qty}")
            if send:
                await order.order_cash("sell", pos.ticker, pos.qty, price=0, ord_dvsn="01")
                store.close(pos.ticker)


def _build_clients():
    from src.config.settings import get_settings
    from src.datasource.kis.adapter import KisAdapter
    from src.trading.kis_order import KisOrderClient
    s = get_settings()
    if s.kis_env != "paper":
        print(f"[중단] KIS_ENV={s.kis_env} — auto_trader v1은 모의(paper) 전용.")
        raise SystemExit(1)
    adapter = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, env="paper")
    order = KisOrderClient(s.kis_app_key, s.kis_app_secret, s.kis_account_no, env="paper")
    return adapter, order


async def _main(action: str, send: bool) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    store = PositionStore()
    adapter, order = _build_clients()
    if action == "buy":
        picks = load_top3(today)
        if not picks:
            print(f"[중단] 오늘({today}) top3 JSON 없음 — pre 리포트 먼저 실행 필요(구픽 매매 금지).")
            return 1
        await buy_top3(picks, adapter, order, store, send=send, today=today)
    else:
        await run_sell(adapter, order, store, send=send)
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="모의 자동매매(auto_trader v1)")
    ap.add_argument("action", choices=["buy", "sell"])
    ap.add_argument("--send", action="store_true", help="실제 모의주문 전송(미지정 시 dry-run)")
    a = ap.parse_args()
    raise SystemExit(asyncio.run(_main(a.action, a.send)))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 통과 확인** — `python -m pytest tests/test_auto_trader.py -v` → 4 passed

- [ ] **Step 5: 커밋**
```bash
git add src/trading/auto_trader.py tests/test_auto_trader.py
git commit -m "feat(trading): auto_trader buy/sell 오케스트레이션 + CLI(dry-run 기본)"
```

---

## Task 6: `pipeline.py` — top3 JSON 방어적 기록

**Files:** Modify `src/market_report/pipeline.py`

- [ ] **Step 1: top3 확정 지점 확인**

Run: `grep -n "snap.top3 = select_top3" src/market_report/pipeline.py`
Expected: pre_close 경로의 `snap.top3 = select_top3(...)` 라인 번호 확보(두 곳이면 pre_close/post_close 구분 — `snap.mode == "pre_close"` 가까운 쪽).

- [ ] **Step 2: 방어적 기록 추가**

`snap.top3` 가 채워진 직후(pre_close 경로)에 삽입:
```python
            # 자동매매 브리지: 보고서 Top3를 JSON으로 남겨 auto_trader가 동일 종목 매수
            if snap.mode == "pre_close":
                try:
                    from datetime import datetime as _dt
                    from src.trading.top3_bridge import persist_top3
                    persist_top3(snap.top3, snap.mode, _dt.now().strftime("%Y-%m-%d"))
                except Exception as exc:  # 리포트를 깨지 않도록 best-effort
                    logger.warning("top3_persist_failed error=%s", exc)
```

- [ ] **Step 3: 회귀 확인** (기존 리포트 테스트 깨지지 않음)

Run: `python -m pytest -q`
Expected: 전체 통과 (신규 포함).

- [ ] **Step 4: 커밋**
```bash
git add src/market_report/pipeline.py
git commit -m "feat(report): pre Top3를 auto_trader용 JSON으로 방어적 기록"
```

---

## Task 7: CLI 오프라인 검증 (수동 --send는 장중 별도)

**Files:** 없음 (검증만)

- [ ] **Step 1: import·argparse 검증**

Run: `python -m src.trading.auto_trader --help`
Expected: usage 출력(에러 없음), `{buy,sell}` · `--send` 표시.

- [ ] **Step 2: 전체 스위트 최종 회귀**

Run: `python -m pytest -q`
Expected: 전체 통과.

> ⚠️ 실제 모의주문(`buy --send`/`sell --send`)은 **장중 + .env(모의 키) 환경**에서 사용자가 실행. 선행: 마일스톤 ① Task 7 스모크로 주문 1건 정상 확인 후.

---

## Self-Review (작성자 점검)

- **Spec 커버리지:** §3.1 pipeline 브리지=Task6 / §3.2 positions=Task3 / §3.3 sizing=Task1 / §3.4 ma_exit=Task2 / §3.5 auto_trader=Task5 / 브리지=Task4 / §5 테스트=Task1~5. 전부 대응.
- **플레이스홀더:** 없음. 모든 코드·명령·기대값 명시.
- **타입/명명 일관성:** `calc_qty`·`split_sell_qty`·`exit_decision`·`consecutive_below`·`PositionStore`(open_position/get_open/update_qty_stage/close/is_held)·`Position`(ticker/name/entry_date/entry_price/qty/stage)·`buy_top3`/`run_sell`·`persist_top3`/`load_top3` 전 태스크 일관. 의존 `moving_average`/`get_quote`(.price)/`get_ohlcv`(.close)/`KisOrderClient.order_cash·inquire_psbl_order` 검증된 시그니처.
- **결정 반영:** 100만원 사이징·2·3차 부분/전량·1주 엣지(split_sell_qty 1→(1,0))·종가베팅 Top3 JSON 브리지·dry-run 기본·paper 강제 모두 반영.
