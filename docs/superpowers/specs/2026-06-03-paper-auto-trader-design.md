# 모의 자동매매 루프 (auto_trader v1) — 설계

> 작성일: 2026-06-03
> 단계: 자동매매 마일스톤 ②(broker/portfolio) + ③(auto_trader), **모의(paper) 전용**
> 선행: 마일스톤 ① `kis_order` 프리미티브(완료, hashkey·order_cash·매수가능·잔고).
> 근거: 자동매매 전략 검토 세션 + 사용자 결정(2026-06-03).

---

## 1. 목적 & 범위

### 목적
모의투자 계좌에서 **종가베팅 Top3를 자동 매수하고, 일봉 기반 손절 규칙으로 자동 청산**하는 루프를 만든다.

### 사용자 결정 (2026-06-03)
- **진입 대상**: 종가베팅 Top3 (`snap.top3` = `select_top3` 결과 — 보고서와 동일 종목).
- **자금 배분**: **1회 매수당 100만원 이내** (모의 예산 5천만원). 종목당 `qty = floor(1,000,000 / 현재가)`.
- **청산 규칙 (일봉 기반)**:
  - 2차: 일봉 20MA **2거래일 연속 종가 이탈** → **50% 매도**.
  - 3차: 일봉 60MA **2거래일 연속 종가 이탈** → **전량 매도**.
  - 1차(60분봉 20MA)는 **범위 밖**(KIS 분봉 ~1.x일 제약으로 라이브 계산 불가 → 다음 단계).
- **스케줄/범위**: 종가 직전 1회 매수 + 청산은 (마감 후) 조건충족 시.

### 완료 기준 (DoD)
- `python -m src.trading.auto_trader buy --send` 실행 시 모의계좌에 Top3가 100만원 이내로 매수되고 포지션이 SQLite에 기록.
- `python -m src.trading.auto_trader sell --send` 실행 시 보유 종목의 일봉 20/60MA 2연속 이탈을 판정해 부분/전량 매도.
- 모든 로직 오프라인 단위 테스트 통과. dry-run 기본.

### 비목표 (다음 단계)
- 1차 60분 손절, 실전(real) 전환, 진입 전략 walk-forward 재검증, 익절(목표가), 정정/취소 주문.

---

## 2. 아키텍처

```
[14:50] 기존 pre 리포트(run_full) → snap.top3
              │ (방어적 JSON 기록)
              ▼
   data/top3_<YYYY-MM-DD>_pre.json   ← 보고서-매매 일관성 브리지
              │
[14:52] python -m src.trading.auto_trader buy   (cron/systemd)
              │  읽기 → 매수가능조회 → order_cash(buy)
              ▼
   data/paper_positions.db (SQLite)  ← 포지션 영속
              ▲
[마감후] python -m src.trading.auto_trader sell  (cron/systemd)
              │  보유별 get_ohlcv(일봉) → 20/60MA 2연속 이탈 판정 → order_cash(sell)
              ▼  텔레그램 체결 알림(기존 Notifier)
```

- 라이브 리포트 프로세스 **비침투**: 스케줄은 별도 CLI를 cron/systemd가 호출(기존 scalping cron 관행과 동일).
- 신규 코드는 `src/trading/`에. 시세 읽기는 기존 `KisAdapter`, 주문은 `KisOrderClient` 재사용.

---

## 3. 컴포넌트

### 3.1 `pipeline.py` 수정 (최소·방어적)
`snap.top3` 확정 직후, pre_close 모드에서만 top3 요약을 JSON 기록:
```python
# 실패해도 리포트를 깨지 않도록 best-effort
try:
    _persist_top3(snap.top3, snap.mode)  # data/top3_<date>_pre.json
except Exception as exc:
    logger.warning("top3_persist_failed error=%s", exc)
```
기록 내용: `{"date": "YYYY-MM-DD", "mode": "pre_close", "picks": [{"ticker","name","price"}...]}`.

### 3.2 `src/trading/positions.py` — 포지션 저장 (SQLite)
- 테이블 `paper_positions(ticker PK, name, entry_date, entry_price, qty, stage, opened INTEGER)`.
  - `stage`: 0=정상보유, 2=2차 50%청산 완료(나머지 보유), (3=종료).
- 메서드: `open_position(...)`, `get_open() -> list[Position]`, `update_qty_stage(ticker, qty, stage)`, `close(ticker)`, `is_held(ticker) -> bool`.
- DB 경로 `data/paper_positions.db` (gitignore).

### 3.3 `src/trading/sizing.py` — 순수 함수
```python
def calc_qty(price: float, budget: int = 1_000_000) -> int:
    """예산 이내 최대 정수 수량. price<=0 또는 price>budget이면 0."""
def split_sell_qty(qty: int) -> tuple[int, int]:
    """2차 50% 매도 분할 → (sell_now, remaining). qty=1이면 (1,0)=전량."""
```

### 3.4 `src/trading/ma_exit.py` — 순수 함수 (청산 판정)
```python
def consecutive_below(closes: list[float], ma: list[float], n: int = 2) -> bool:
    """최근 n개 종가가 모두 대응 MA 아래면 True (2연속 이탈)."""
def exit_decision(daily_closes: list[float]) -> str:
    """일봉 종가 시계열 → 'SELL_HALF'(20MA 2연속) | 'SELL_ALL'(60MA 2연속) | 'HOLD'.
    60MA 우선(더 심각). 20MA만이면 SELL_HALF."""
```
- 20MA/60MA는 `src/indicators`의 순수 함수 재사용(있으면) 또는 단순 rolling mean.

### 3.5 `src/trading/auto_trader.py` — 오케스트레이션 + CLI
- `async buy(send: bool)`:
  1. 오늘자 `data/top3_<date>_pre.json` 로드(없거나 날짜 불일치 → 중단, 구픽 매매 금지).
  2. 각 ticker: 이미 보유면 skip. `KisAdapter.get_quote`로 현재가 → `calc_qty`. qty<1 → skip+log.
  3. `inquire_psbl_order`로 매수가능수량 확인(부족 시 min). dry-run이면 여기까지 출력.
  4. `order_cash("buy", qty, 시장가)` → `positions.open_position` → 텔레그램 알림.
- `async sell(send: bool)`:
  1. `positions.get_open()` + `inquire_balance` 교차검증.
  2. 각 보유: `get_ohlcv(days=80)` → `exit_decision`.
     - `SELL_HALF` & stage<2: `split_sell_qty` → 50% 매도 → `update_qty_stage(stage=2)`.
     - `SELL_ALL`: 전량 매도 → `close`.
  3. 매도 체결 텔레그램 알림.
- CLI: `python -m src.trading.auto_trader {buy|sell} [--send]`.

---

## 4. 안전 게이트 (필수)
- **`KIS_ENV != paper` → 즉시 중단**(실전 거부). v1 모의 전용.
- **dry-run 기본**: 실제 주문은 `--send` 명시 시에만. 미전송 시 의도만 출력.
- **중복 매수 방지**: 이미 보유(`positions.is_held` + 잔고 교차) 종목 재매수 금지.
- **일일 매수 상한**: Top3(≤3종목)만. JSON 픽 외 매수 금지.
- **포지션 영속**: SQLite로 서버 재시작에도 보유·stage 복구.
- 주문 거부(rt_cd≠0)/HARD STOP은 `KisOrderClient`가 처리(로깅·중단).
- stdout utf-8 재설정(cp949 콘솔 보호).

---

## 5. 테스트 (오프라인, respx/mock, 라이브 0)
1. `sizing.calc_qty`: 100만/82500=12, price=0→0, price>100만→0.
2. `sizing.split_sell_qty`: 12→(6,6), 11→(5,6), 1→(1,0).
3. `ma_exit.consecutive_below` / `exit_decision`: 20MA 2연속→SELL_HALF, 60MA 2연속→SELL_ALL, 정상→HOLD.
4. `positions`: open/get_open/update/close/is_held (임시 SQLite).
5. `auto_trader.buy`: mock KisAdapter/KisOrderClient → 보유종목 skip·qty 계산·order_cash 호출 인자 검증.
6. `auto_trader.sell`: mock → SELL_HALF면 50% 매도+stage=2, SELL_ALL이면 전량+close.
7. JSON 브리지: `_persist_top3` 기록/로드 라운드트립.

### 수동 (장중, CI 제외)
- `buy --send` → 모의 Top3 매수 확인. 익일 이후 `sell --send` → 조건 시 청산 확인.

---

## 6. 사전 검증 (완료/재사용)
- 주문 TR/엔드포인트: 마일스톤 ①에서 공식 검증(VTTC0012U/0011U·order-cash·hashkey·매수가능 VTTC8908R·잔고 VTTC8434R).
- 일봉: `KisAdapter.get_ohlcv(days)` 기존 검증(100+봉).
- ⚠️ 잔고/주문 실호출은 마일스톤 ① Task 7 스모크(`--send`)로 먼저 1회 확인 후 본 루프 라이브 가동.

---

## 7. 산출물
- 수정: `src/market_report/pipeline.py` (top3 JSON 방어적 기록 ~5줄)
- 신규: `src/trading/{positions,sizing,ma_exit,auto_trader}.py`
- 신규: `tests/test_auto_trader.py` (+ sizing/ma_exit/positions)
