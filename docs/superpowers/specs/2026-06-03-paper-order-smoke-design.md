# 모의 주문 배관 검증 (스모크) — 설계

> 작성일: 2026-06-03
> 단계: 자동매매 로드맵 마일스톤 ① (모의투자 우선)
> 근거: 자동매매 전략 검토(세션) — "실거래 전 모의투자 우선, 주문 TR/hashkey 공식 검증부터"

---

## 1. 목적 & 범위

### 목적
KIS 모의투자(VTS)에서 **주문 API 배관이 실제로 동작함을 end-to-end로 증명**한다.
핵심 미해결점이었던 ① `_request`의 GET 전용 한계 → 주문용 POST 분기, ② hashkey(주문 body 무결성),
③ 주문 TR_ID 의존성을 해소하는 것이 목표.

### 완료 기준 (Definition of Done)
- VTS에서 `hashkey → 매수가능조회 → 1주 매수 → 체결/잔고 확인 → 매도`가 수동 스크립트로 1회 성공.
- 재사용 가능한 주문 프리미티브(`src/trading/kis_order.py`)가 L2 단위 테스트(respx 모킹)로 검증됨.

### 비목표 (Non-Goals) — 다음 마일스톤
- ② broker 모듈(취소/정정 포함 정식 API), portfolio(SQLite position), auto_trader(Top3 오케스트레이션).
- 진입 전략 재검증(walk-forward), 손절 체계 정교화.
- 실전(real) 주문 — 본 마일스톤은 **모의 전용**.

### 마일스톤 순서 (참고)
① 주문 배관 검증(스모크) ← **본 문서** → ② broker 모듈 → ③ 자동 루프(portfolio + auto_trader)

---

## 2. 아키텍처 / 경계

- 신규 `src/trading/` 패키지 (CLAUDE.md §4 지정 위치). 읽기전용 `src/datasource`(시세)와 **주문 쓰기**를 분리.
- `src/trading/kis_order.py` — 주문 프리미티브 단일 파일. 기존 `KisTokenManager`를 **재사용**(동일 토큰 캐시 `data/kis_token.json` 공유, env-keyed).
- `KisAdapter`(datasource)는 **수정하지 않음**.
- 의존 방향: `kis_order → KisTokenManager`(토큰 발급/캐시만). domain(indicators/patterns/screener)과 무관.

```
scripts/smoke_paper_order.py  (CLI, dry-run 기본)
        │
        ▼
src/trading/kis_order.py  (KisOrderClient)
        │  토큰만 의존
        ▼
src/datasource/kis/token.py  (KisTokenManager — 기존, 재사용)
```

---

## 3. 컴포넌트

### 3.1 `KisOrderClient(app_key, app_secret, account_no, env="paper")`
`KisAdapter`와 동일한 생성자 시그니처. 내부에서 `KisTokenManager(app_key, app_secret, env)` 구성.

| 메서드 | 설명 | 비고 |
|--------|------|------|
| `async hashkey(body: dict) -> str` | `POST /uapi/hashkey` (appkey/appsecret 헤더, bearer 불필요) | 주문 body 무결성 해시 |
| `async inquire_psbl_order(ticker, price=0) -> dict` | 매수가능조회 (현금주문 가능 수량/금액) | 주문 전 검증 |
| `async order_cash(side, ticker, qty, price=0, ord_dvsn="01") -> dict` | `POST .../trading/order-cash` → `odno`(주문번호) | side+env로 TR_ID 결정, hashkey 헤더 필요 |
| `async inquire_balance() -> dict` | 잔고/보유 확인 | 체결 확인용 (잔고 TR은 handover 검증치 재사용) |

- 내부 `_post(path, tr_id, body, *, needs_hash: bool) -> dict` 헬퍼: `adapter._request`와 **동일한 재시도·지수 백오프·HARD STOP(429/401/403)·`rt_cd` 검증** 패턴을 미러링. POST + (필요 시) hashkey 헤더 주입.
- 계좌 파싱: `"50190660-01"` → CANO=`50190660` / ACNT_PRDT_CD=`01`. (하이픈 없는 전체 입력도 허용)
- `ord_dvsn`: `"01"`=시장가(ORD_UNPR=0), `"00"`=지정가. 기본 시장가.
- side: `"buy"` / `"sell"` → env+side로 TR_ID 매핑.

### 3.2 `scripts/smoke_paper_order.py` (CLI)
- 인자: `--ticker 005930 --qty 1 [--price P] [--send]`
- **dry-run 기본**: 토큰 확보 → hashkey(샘플 body) 해시 반환 확인 → 매수가능조회 출력 → 정지("실제 주문은 `--send`").
- `--send`: 위 + 매수 1주 → 체결/잔고 확인 → 매도 → 최종 잔고 확인. 단계마다 랜덤 딜레이(외부 API 하네스).
- 각 단계 결과를 사람이 읽을 수 있게 출력(hashkey OK, 매수가능 N주, odno, 체결/보유, 청산).

---

## 4. 데이터 흐름 (스모크 시퀀스)

```
토큰 확보
  → hashkey(샘플 body)          [해시 반환 확인]
  → inquire_psbl_order          [매수가능 N주 확인]
  ── dry-run이면 여기서 종료 ──
  → order_cash(buy, 1주)        [odno 수신]
  → inquire_balance             [체결·보유 수량 확인]
  → order_cash(sell, 1주)       [청산]
  → inquire_balance             [최종 잔고 확인]
```

---

## 5. 에러 처리 / 안전 게이트

- **env 강제**: `KIS_ENV != paper`면 즉시 거부(실전 오발주 방지). 본 마일스톤 모의 전용.
- **수량 캡**: `--qty > 10`이면 거부(모의여도 fat-finger 방지). 기본 1.
- **전송 전 확인 출력**: 실제 주문 body(종목·수량·구분)를 출력하고 `--send`일 때만 전송.
- **HARD STOP**(429/401/403): 재시도 없이 즉시 중단 + 알림.
- **주문 거부**(`rt_cd != "0"`): `msg1` 그대로 노출(삼킴 금지).
- **hashkey 실패**: 즉시 중단(잘못된 해시로 주문 전송 금지).
- 시크릿(appkey/secret/token) 로그·출력 마스킹.

---

## 6. 테스트

### L2 단위 (respx 모킹, 라이브 호출 0)
1. `order_cash` body 조립 검증: CANO/ACNT_PRDT_CD 분리, side+env별 TR_ID, ord_dvsn·ORD_UNPR.
2. `hashkey` 응답 파싱(HASH 추출).
3. `inquire_psbl_order` 파싱(가능 수량/금액).
4. `rt_cd != "0"` → `KisError` 발생.
5. HTTP 429 → HARD STOP 발생(재시도 없음).

### 수동 (CI 제외)
- 실제 VTS `--send` 1회 성공으로 배관 검증.

---

## 7. 사전 검증 필요 (plan 단계, 하드코딩 전)

프로젝트 규칙 §6 "추측금지". 다음을 **KIS 공식 문서(koreainvestment/open-trading-api GitHub 또는 apiportal)로 검증한 뒤** 상수 확정:

| 항목 | 현재 추정값 | 검증 대상 |
|------|------------|-----------|
| hashkey 엔드포인트 | `POST /uapi/hashkey` | 경로·헤더·응답 필드 |
| order-cash 경로 | `/uapi/domestic-stock/v1/trading/order-cash` | 경로·body 필드명 |
| 모의 매수 TR_ID | `VTTC0802U` (추정) | 공식 확인 |
| 모의 매도 TR_ID | `VTTC0801U` (추정) | 공식 확인 |
| 매수가능조회 경로/TR | `inquire-psbl-order` / TR 미확정 | 공식 확인 |
| body 필드 | CANO·ACNT_PRDT_CD·PDNO·ORD_DVSN·ORD_QTY·ORD_UNPR | 필드명·시장가 처리(ORD_UNPR=0) |
| 잔고 TR(모의) | `VTTC8434R` (handover 검증치) | 재확인 |

신뢰도: 잘 알려진 값이라 ~85%지만, 규칙상 **실제 호출 전 공식 확인 필수**.

---

## 8. 산출물 요약

- 신규: `src/trading/__init__.py`, `src/trading/kis_order.py`
- 신규: `scripts/smoke_paper_order.py`
- 신규: `tests/test_kis_order.py` (L2 respx)
- `.env`: `KIS_ENV=paper` + 모의 키/계좌 (사용자 설정 완료)
