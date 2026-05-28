---
template: design
version: 1.3
feature: stock-insight-bot
date: 2026-05-26
author: viw0816
project: stock_report
version_doc: 0.1
status: Draft
---

# Stock Insight Bot Design Document

> **Summary**: KIS Open API + Claude AI 기반 한국 주식 인사이트 봇. 금일 핫 종목 + 관심종목에 검색식+패턴을 적용해 매수 시그널을 텔레그램으로 알리고, 종목/차트 이미지 온디맨드 분석을 제공. Option C(실용 균형) 아키텍처 — 데이터소스·알림 경계만 어댑터화.
>
> **Project**: stock_report
> **Version**: 0.1.0
> **Author**: viw0816
> **Date**: 2026-05-26
> **Status**: Draft
> **Planning Doc**: [stock-insight-bot.plan.md](../../01-plan/features/stock-insight-bot.plan.md)

---

## Context Anchor

> Copied from Plan document. Design→Do 핸드오프에서 전략 맥락 유지.

| Key | Value |
|-----|-------|
| **WHY** | 흩어진 주식 정보(테마·관심종목·차트 패턴·종가 직전 동향)를 수동 추적하느라 타이밍을 놓치고 일관된 판단 근거가 없음 |
| **WHO** | KIS Open API 계정을 보유한 개인 투자자(본인). Python/메신저 환경, 한국 주식 우선 |
| **RISK** | KIS API rate limit·토큰 만료로 데이터 누락/차단; AI 분석 환각으로 잘못된 매매 신호; 매수추천 오인 |
| **SUCCESS** | 금일 핫 종목에 검색식+패턴 적용해 매수추천 여부 알림 발송 · 관심종목 조건 알림 · 종목/이미지 온디맨드 패턴 진단 · 종가 직전 브리핑 · 증시 요약(온디맨드) |
| **SCOPE** | P1 기반(KIS+텔레그램+관심종목) → P2 분석(지표+패턴+이미지+검색식) → P3 핫종목·매수시그널·브리핑 → P4 인사이트 종합/미국 확장 |

---

## 1. Overview

### 1.1 Design Goals

- 핵심 자동 push 파이프라인(핫 종목 → 검색식+패턴 → 매수 시그널 → 알림)을 안정적·저비용으로 구동
- 데이터 소스(KIS)와 알림(Telegram)을 **포트 인터페이스**로 격리해 미국 시장·카카오 확장 시 어댑터만 교체
- AI(Claude) 분석을 보조 수단으로 두고, 모든 매수 시그널에 **수치 근거 + 면책**을 동반
- KIS 외부 호출에 전역 안전 규칙(rate limit·백오프·하드스톱) 일관 적용

### 1.2 Design Principles

- **Ports & Adapters (경계만)**: 바뀔 경계(MarketDataSource, Notifier)만 추상화. Claude·SQLite는 얇은 래퍼로 직접 사용 (과도 추상화 회피)
- **순수 도메인**: indicators/patterns/screener는 외부 의존 없는 순수 함수 → 결정론적 단위 테스트 가능
- **단일 책임**: 기능별 모듈(ranking/alerts/briefing/analysis) 분리
- **Fail-safe 외부 호출**: 외부 API 실패는 재시도→백오프→하드스톱→알림 (전역 CLAUDE.md §7)
- **AI는 보조**: 수치(지표·검색식 충족 항목)가 1차, LLM 코멘트는 2차

---

## 2. Architecture Options

### 2.0 Architecture Comparison

| Criteria | Option A: Minimal | Option B: Clean(DI) | Option C: Pragmatic |
|----------|:-:|:-:|:-:|
| **Approach** | 단일 패키지, 직접 호출 | 포트&어댑터 전면+DI | 바뀔 경계만 어댑터화 |
| **New Files** | ~12 | ~28 | ~18 |
| **Complexity** | Low | High | Medium |
| **Maintainability** | Medium | High | High |
| **Effort** | Low | High | Medium |
| **US 확장** | KIS 호출 흩어져 부담 | 어댑터 추가만 | 어댑터 추가만 |
| **Recommendation** | 빠른 검증 | 장기·대형 | **Default** |

**Selected**: **Option C — 실용 균형** — **Rationale**: 미국 확장(데이터소스 교체)·카카오 교체라는 명확한 미래 변경점만 포트로 격리하고, Claude/SQLite/스케줄러는 직접 사용해 개인 프로젝트에 맞는 공수·유지보수 균형 확보.

### 2.1 Component Diagram

```
                         ┌──────────────────────────────┐
                         │        Telegram Bot           │
                         │  (명령 수신 / 알림 발송)       │
                         └───────────────┬───────────────┘
                                         │ (Notifier port)
        ┌────────────────────────────────┼────────────────────────────────┐
        │                  Application / 기능 모듈                          │
        │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │
        │  │ ranking  │ │ screener │ │  alerts  │ │ briefing │ │analysis│ │
        │  │ 핫종목   │ │검색식+   │ │관심종목  │ │종가직전  │ │Claude  │ │
        │  │ 수집     │ │매수시그널│ │모니터링  │ │브리핑    │ │비전·요약││
        │  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └───┬────┘ │
        │       │            │            │            │           │      │
        │       └─── indicators / patterns (순수 도메인) ───────────┘      │
        └────────────────────────┬───────────────────────────┬───────────┘
                                  │ (MarketDataSource port)   │
                       ┌──────────┴──────────┐         ┌──────┴───────┐
                       │   KIS Adapter        │         │  SQLite       │
                       │ (시세·OHLCV·순위)    │         │ (storage)     │
                       └──────────────────────┘         └───────────────┘
        ┌──────────────┐
        │  Scheduler    │  APScheduler → ranking/screener/alerts/briefing 주기 실행
        └──────────────┘
```

### 2.2 Data Flow

**① 핵심: 매수 시그널 자동 push**
```
Scheduler(장중 주기 + 종가 전) → ranking.get_hot_stocks() [KIS 순위 API]
  → (핫종목 ∪ 관심종목) 각 종목 → datasource.get_ohlcv()
  → indicators.compute() → patterns.detect() + screener.match_formula()
  → screener.decide_signal()  ── 매수 시그널? ──▶ notify.send_signal_alert()
                                                    (근거 지표·검색식 충족 + 면책)
```

**② 온디맨드: 종목 텍스트 분석**
```
User "/analyze 005930" → bot router → datasource.get_ohlcv()
  → indicators + patterns → screener.formula_text()
  → (선택) analysis.comment() [Claude 요약] → notify.reply()
```

**③ 온디맨드: 차트 이미지 분석**
```
User (차트 이미지 전송) → bot router → analysis.analyze_chart_image() [Claude vision]
  → 패턴 해석 + 면책 → notify.reply()
```

**④ 온디맨드: 증시 요약 / 관심종목 알림**
```
User "/summary" → ranking + analysis.summarize() → reply
Scheduler(장중) → alerts.check_watchlist() → 조건 충족 시 notify.send()
```

### 2.3 Dependencies

| Component | Depends On | Purpose |
|-----------|-----------|---------|
| ranking | datasource(port) | KIS 순위 API로 핫 종목 |
| screener | indicators, patterns | 검색식 매칭 + 매수 시그널 판정 |
| alerts | datasource, screener, storage | 관심종목 조건 평가·이력 |
| briefing | ranking, screener, analysis | 종가 직전 요약 |
| analysis | (Claude SDK 래퍼) | 차트 비전·테마·상승이유 요약 |
| bot | 모든 기능 모듈, notify(port) | 명령 라우팅·응답 |
| scheduler | ranking, alerts, briefing | 주기 실행 등록 |
| 전 모듈 | config | 키·임계치 설정 |

---

## 3. Data Model

### 3.1 Entity Definition

```python
# 관심종목
@dataclass
class WatchItem:
    ticker: str          # 종목코드 (예: "005930")
    name: str            # 종목명
    added_at: datetime
    conditions: dict     # 알림 조건 {change_pct: 5, vol_surge: 2.0, ...}

# 매수 시그널 로그
@dataclass
class SignalRecord:
    id: int
    ticker: str
    signal_type: str     # "buy" | "watch" | "none"
    pattern: str         # "pullback" | "breakout" | ...
    score: float         # 0~1
    reasons: list[str]   # 충족 근거 (검색식·지표)
    created_at: datetime
```

### 3.2 Entity Relationships

```
[WatchItem] ──(ticker)── [SignalRecord] ──(logged in)── [AlertHistory]
[AnalysisCache] : ticker/이미지 해시 기반 결과 캐시 (독립)
```

### 3.3 Database Schema (SQLite)

```sql
CREATE TABLE watchlist (
  ticker      TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  conditions  TEXT NOT NULL DEFAULT '{}',   -- JSON
  added_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE signal_log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker      TEXT NOT NULL,
  signal_type TEXT NOT NULL,                -- buy/watch/none
  pattern     TEXT,
  score       REAL,
  reasons     TEXT,                         -- JSON array
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE alert_history (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id     TEXT NOT NULL,
  alert_type  TEXT NOT NULL,                -- signal/watch/briefing
  ticker      TEXT,
  message     TEXT NOT NULL,
  sent_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE analysis_cache (
  cache_key   TEXT PRIMARY KEY,             -- ticker:date / img_hash
  payload     TEXT NOT NULL,                -- JSON
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  ttl_sec     INTEGER NOT NULL DEFAULT 300
);

CREATE TABLE settings (
  key         TEXT PRIMARY KEY,
  value       TEXT NOT NULL
);
```

> KIS access token은 DB 대신 `.bkit/runtime`와 무관한 런타임 캐시 파일(`token.json`, gitignore)에 만료시각과 함께 저장.

---

## 4. External API & Bot Command Spec

> 본 시스템은 자체 REST API가 없음. 외부 API(KIS/Claude) 소비 + 텔레그램 명령 인터페이스를 정의한다.

### 4.1 KIS Open API (소비) — 필요 capability

| Capability | 용도 | 비고 |
|-----------|------|------|
| OAuth 토큰 발급/갱신 | access_token 발급, 만료 자동 갱신 | TR/엔드포인트는 Do 단계에서 KIS 공식 문서로 확정 |
| 현재가 시세 조회 | 현재가·등락률·거래량 | 관심종목 알림·시그널 |
| 기간별(일/분) 시세(OHLCV) | 지표·패턴 계산 입력 | 일봉 N일치 |
| 순위분석(등락률/거래량/거래대금 상위) | 금일 핫 종목 리스트 | "전체 스캔" 대체 |

> **확신도 70%**: KIS가 위 4종 capability를 제공함은 일반적 사실이나, 정확한 경로/TR_ID/요청 파라미터는 계정 환경(실전/모의)에 따라 다르므로 **Do 단계 첫 작업에서 KIS 공식 문서로 검증** 후 `datasource/kis` 어댑터에 반영한다.

### 4.2 Telegram Bot 명령

| Command | 설명 | Auth |
|---------|------|------|
| `/start`, `/help` | 안내 | 화이트리스트 |
| `/watch <종목코드>` | 관심종목 추가 | 화이트리스트 |
| `/unwatch <종목코드>` | 관심종목 제거 | 화이트리스트 |
| `/watchlist` | 관심종목 목록 | 화이트리스트 |
| `/analyze <종목코드>` | 온디맨드 패턴+검색식 분석 | 화이트리스트 |
| (차트 이미지 전송) | 이미지 패턴 분석(Claude vision) | 화이트리스트 |
| `/hot` | 금일 핫 종목 + 시그널 (온디맨드) | 화이트리스트 |
| `/summary` | 증시 요약 (온디맨드) | 화이트리스트 |
| `/briefing` | 종가 직전 브리핑 (수동 트리거) | 화이트리스트 |
| `/settings` | 임계치(등락률·거래량배수 등) 조회/변경 | 화이트리스트 |

### 4.3 Notifier Port (인터페이스)

```python
class Notifier(Protocol):
    async def send(self, chat_id: str, text: str, *, parse_mode="Markdown") -> None: ...
    async def send_signal_alert(self, chat_id: str, record: SignalRecord) -> None: ...
```

### 4.4 MarketDataSource Port (인터페이스)

```python
class MarketDataSource(Protocol):
    async def get_quote(self, ticker: str) -> Quote: ...
    async def get_ohlcv(self, ticker: str, days: int) -> list[Candle]: ...
    async def get_ranking(self, kind: RankingKind, top: int) -> list[RankedStock]: ...
```

---

## 5. UI/UX Design (Telegram 메시지 포맷)

### 5.1 매수 시그널 알림 포맷

```
🔔 [매수 시그널] 삼성전자 (005930)
패턴: 눌림목 (score 0.78)
근거:
 • 20일선 위 + 5일선 근접(이격 1.2%)
 • 거래량 20일평균 대비 0.6배(수축)
 • RSI 48 (40~55)
검색식: "종가>MA20 AND |종가-MA5|/MA5<2% AND 거래량<MA_VOL20 AND 40<=RSI<=55"
※ 참고용 시그널입니다. 투자 판단·책임은 본인에게 있습니다.
(데이터 기준 14:58)
```

### 5.2 User Flow

```
[자동] 스케줄러 → 핫종목 평가 → 시그널 발생분만 알림
[수동] /analyze 005930 → 분석 카드 응답
[수동] 차트 이미지 → 이미지 해석 카드 응답
[수동] /hot, /summary, /briefing → 요약 응답
```

### 5.3 Component List (메시지 빌더)

| Component | Location | Responsibility |
|-----------|----------|----------------|
| SignalCard | `bot/messages.py` | 시그널 알림 포맷팅 |
| AnalysisCard | `bot/messages.py` | 온디맨드 분석 응답 |
| BriefingCard | `bot/messages.py` | 종가 브리핑 포맷 |
| Disclaimer | `bot/messages.py` | 모든 분석/시그널 공통 면책 |

### 5.4 Message Element Checklist

#### 매수 시그널 알림
- [ ] 종목명 + 종목코드
- [ ] 패턴명 + score
- [ ] 근거 리스트(지표 수치 포함)
- [ ] 검색식(텍스트, HTS 사용 가능 형태)
- [ ] 면책 문구
- [ ] 데이터 기준 시각

#### 온디맨드 분석(/analyze)
- [ ] 현재가·등락률
- [ ] 패턴 진단(없으면 "해당 패턴 없음")
- [ ] 검색식 텍스트
- [ ] (선택) LLM 코멘트
- [ ] 면책 문구

#### 차트 이미지 분석
- [ ] 인식한 패턴/추세 설명
- [ ] 주의/리스크 코멘트
- [ ] 면책 문구(이미지 기반 추정임을 명시)

---

## 6. Error Handling

### 6.1 Error 분류 및 처리

| Code | 상황 | 원인 | 처리 |
|------|------|------|------|
| KIS_AUTH | 토큰 만료/인증 실패 | access_token 만료 | 자동 재발급 1회 → 실패 시 하드스톱+알림 |
| KIS_RATE | 호출 제한(초당/일일) | rate limit 초과 | 백오프 후 재시도, 연속 실패 시 하드스톱 |
| KIS_DATA | 빈/이상 응답 | 휴장·종목 없음 | 스킵+로그, 사용자엔 "데이터 없음" |
| TG_SEND | 텔레그램 전송 실패 | 네트워크/토큰 | 재시도 3회, 실패 시 로그 |
| AI_FAIL | Claude 호출 실패/한도 | API 오류 | 분석 생략(지표만 제공), 비용 한도 시 차단 |
| IMG_PARSE | 이미지 인식 실패 | 비차트 이미지 | "차트로 보이지 않음" 안내 |

### 6.2 외부 호출 안전 규칙 (전역 CLAUDE.md §7 준수)

```python
# KIS/Claude 공통 래퍼
- 요청 간 랜덤 딜레이(고정값 금지)
- 배치(예: 핫종목 N개) 사이 휴식
- 최대 재시도 3회 + 지수 백오프
- HARD STOP: 429/인증급변/연속 타임아웃 3회 → 즉시 중단 + notify_user
- 세션 상태 추적(성공/실패/마지막 성공)
```

### 6.3 Error 응답 포맷 (사용자)

```
⚠️ 분석 실패: {원인 요약}
- 시도: {n}회 / 마지막 성공: {시각}
- 조치: 잠시 후 다시 시도하거나 /help 참고
```

---

## 7. Security Considerations

- [ ] KIS APP_KEY/SECRET, TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY → `.env`만, git 제외(`.gitignore`)
- [ ] 허용 `chat_id` 화이트리스트(`TELEGRAM_ALLOWED_CHAT_IDS`) — 그 외 메시지 무시
- [ ] 로그·메시지에 시크릿/토큰 마스킹
- [ ] access_token 캐시 파일 권한 제한 + git 제외
- [ ] 입력 검증: 종목코드 정규식(6자리 숫자), 명령 인자 sanitize
- [ ] Claude 비용 한도(일일 호출 상한) → 초과 시 AI 기능만 비활성
- [ ] 외부 호출 rate limit/하드스톱(§6.2)

---

## 8. Test Plan

> WHAT을 정의. 테스트 코드는 Do 단계에서 모듈과 1:1로 작성(코드+테스트=1세트). Tool: **pytest**, HTTP 모킹 **respx/responses**, **pytest-asyncio**.

### 8.1 Test Scope

| Type | Target | Tool | Phase |
|------|--------|------|-------|
| L1: Unit | indicators/patterns/screener 순수 로직 | pytest (픽스처 OHLCV) | Do |
| L2: Integration | KIS 어댑터·Notifier·Claude 래퍼(모킹) | pytest + respx | Do |
| L3: E2E(시뮬) | 봇 명령·스케줄 잡 플로우(외부 모킹) | pytest-asyncio | Do |

### 8.2 L1: Unit 시나리오

| # | Target | 입력 | 기대 |
|---|--------|------|------|
| 1 | indicators.ma/rsi/vol_avg | 고정 OHLCV 픽스처 | 알려진 값과 일치(허용오차) |
| 2 | patterns.detect_pullback | 눌림목 샘플 캔들 | pattern="pullback", 근거 리스트 |
| 3 | patterns.detect_breakout | 돌파 샘플 캔들 | pattern="breakout" |
| 4 | patterns.detect_* | 무패턴 샘플 | "none" |
| 5 | screener.match_formula | 조건 충족/미충족 케이스 | bool + 충족 항목 |
| 6 | screener.decide_signal | 패턴+검색식 조합 | signal_type/score/reasons |

### 8.3 L2: Integration 시나리오 (외부 모킹)

| # | Target | 시나리오 | 기대 |
|---|--------|---------|------|
| 1 | KISAdapter.token | 만료 토큰 → 자동 재발급 | 새 토큰 사용, 재호출 성공 |
| 2 | KISAdapter.get_ohlcv | 정상 응답 모킹 | Candle 리스트 파싱 정확 |
| 3 | KISAdapter | 429 응답 | 백오프 후 재시도, 연속시 하드스톱 |
| 4 | KISAdapter.get_ranking | 순위 응답 모킹 | RankedStock 리스트 |
| 5 | Notifier.send | 전송 실패 모킹 | 3회 재시도 후 로그 |
| 6 | analysis.analyze_chart_image | Claude 모킹 | 캐시 저장, 결과 반환 |

### 8.4 L3: E2E(시뮬) 시나리오

| # | Scenario | Steps | Success |
|---|----------|-------|---------|
| 1 | 온디맨드 분석 | `/analyze 005930`(모킹 OHLCV) → 응답 | 카드에 §5.4 요소 전부 |
| 2 | 시그널 잡 | 스케줄 잡 실행(모킹 핫종목/시세) | 시그널 종목만 알림 + signal_log 기록 |
| 3 | 관심종목 알림 | watch 등록 → 조건충족 모킹 → 모니터 | 알림 1건 + alert_history |
| 4 | 이미지 분석 | 차트 이미지 → 응답 | 패턴 설명 + 면책 포함 |
| 5 | 권한 | 비허용 chat_id 메시지 | 무시(응답 없음) |

### 8.5 Seed/Fixture Requirements

| Fixture | 내용 | 용도 |
|---------|------|------|
| `tests/fixtures/ohlcv_pullback.json` | 눌림목 캔들 시퀀스 | 패턴 결정론 테스트 |
| `tests/fixtures/ohlcv_breakout.json` | 돌파 캔들 시퀀스 | 패턴 테스트 |
| `tests/fixtures/kis_*.json` | KIS 응답 샘플 | 어댑터 파싱 테스트 |

---

## 9. Clean Architecture (Python 적용)

### 9.1 Layer Structure

| Layer | Responsibility | Location |
|-------|---------------|----------|
| **Domain (순수)** | 지표·패턴·검색식·시그널 규칙 | `src/indicators/`, `src/patterns/`, `src/screener/` |
| **Application (기능)** | ranking/alerts/briefing 오케스트레이션 | `src/ranking/`, `src/alerts/`, `src/briefing/`, `src/analysis/` |
| **Ports** | 외부 경계 인터페이스 | `src/datasource/base.py`, `src/notify/base.py` |
| **Adapters/Infra** | KIS, Telegram, SQLite, Claude, 스케줄러 | `src/datasource/kis/`, `src/notify/telegram/`, `src/storage/`, `src/scheduler/`, `src/config/` |

### 9.2 Dependency Rules

```
bot/scheduler (진입) ─→ 기능모듈(app) ─→ domain(순수)
                              │
                              └─→ ports ←── adapters(KIS/Telegram)
규칙: domain은 외부 무의존. 기능 모듈은 ports에만 의존(구체 어댑터 X).
      어댑터 주입은 main.py(조립 지점)에서.
```

### 9.3 Import Rules

| From | Can Import | Cannot Import |
|------|-----------|---------------|
| domain(indicators/patterns/screener) | (없음, 표준 라이브러리만) | 모든 외부/어댑터 |
| 기능 모듈(ranking/alerts/…) | domain, ports | 구체 어댑터 직접 |
| adapters | ports, domain 타입 | 기능 모듈, bot |
| bot/scheduler | 기능 모듈, ports | (어댑터는 main 주입) |

### 9.4 This Feature's Layer Assignment

| Component | Layer | Location |
|-----------|-------|----------|
| compute_indicators | Domain | `src/indicators/` |
| detect_pattern | Domain | `src/patterns/` |
| match_formula / decide_signal | Domain | `src/screener/` |
| HotStockService | Application | `src/ranking/` |
| WatchlistMonitor | Application | `src/alerts/` |
| BriefingService | Application | `src/briefing/` |
| ChartAnalyzer (Claude) | Application | `src/analysis/` |
| MarketDataSource(port) / KISAdapter | Ports/Infra | `src/datasource/` |
| Notifier(port) / TelegramAdapter | Ports/Infra | `src/notify/` |
| SQLite repos | Infra | `src/storage/` |

---

## 10. Coding Convention Reference

### 10.1 Naming

| Target | Rule | Example |
|--------|------|---------|
| 함수/변수 | snake_case | `compute_rsi()`, `hot_stocks` |
| 클래스 | PascalCase | `KISAdapter`, `SignalRecord` |
| 상수 | UPPER_SNAKE | `MAX_RETRY`, `RANKING_TOP_N` |
| 모듈/파일 | snake_case.py | `kis_adapter.py` |
| 패키지 | snake_case | `datasource/`, `screener/` |

### 10.2 Tooling

- 포매터/린터: **ruff** (+ ruff format) — `pyproject.toml`
- 타입: 타입힌트 필수, `mypy` 권장
- import 순서: stdlib → 3rd-party → local (ruff isort)
- async: 봇/외부호출은 async, 도메인 계산은 동기 순수 함수

### 10.3 Environment Variables

| Variable | Scope | 용도 |
|----------|-------|------|
| `KIS_APP_KEY` / `KIS_APP_SECRET` / `KIS_ACCOUNT_NO` | Server | KIS 인증 |
| `KIS_ENV` | Server | real/paper(모의) 구분 |
| `TELEGRAM_BOT_TOKEN` | Server | 봇 토큰 |
| `TELEGRAM_ALLOWED_CHAT_IDS` | Server | 허용 사용자(콤마 구분) |
| `ANTHROPIC_API_KEY` | Server | Claude |
| `AI_DAILY_CALL_LIMIT` | Server | AI 비용 상한 |
| `DB_PATH` | Server | SQLite 경로 |

### 10.4 This Feature's Conventions

| Item | Applied |
|------|---------|
| 설정 로딩 | pydantic-settings + `.env` |
| 외부 호출 | 공통 안전 래퍼(rate limit/백오프/하드스톱) |
| 에러 처리 | 커스텀 예외(KISAuthError 등) + 사용자 메시지 분리 |
| 로깅 | structlog 또는 표준 logging + 시크릿 마스킹 |

---

## 11. Implementation Guide

### 11.1 File Structure

```
stock_report/
├── main.py                  # 봇+스케줄러 기동, 어댑터 조립(주입)
├── pyproject.toml           # 의존성/ruff/mypy
├── .env                     # 시크릿 (gitignore)
├── src/
│   ├── config/              # pydantic 설정
│   ├── datasource/
│   │   ├── base.py          # MarketDataSource(Protocol) + 타입(Quote/Candle/RankedStock)
│   │   └── kis/             # KIS 어댑터(인증·시세·OHLCV·순위) + 안전 래퍼
│   ├── notify/
│   │   ├── base.py          # Notifier(Protocol)
│   │   └── telegram/        # 텔레그램 어댑터
│   ├── indicators/          # 순수 지표 계산
│   ├── patterns/            # 눌림목·돌파 등 판정
│   ├── screener/            # 검색식 매칭 + 매수 시그널 판정
│   ├── ranking/             # 핫 종목 수집(순위 API)
│   ├── alerts/              # 관심종목 모니터링
│   ├── analysis/            # Claude 비전·요약 래퍼
│   ├── briefing/            # 종가 직전 브리핑
│   ├── scheduler/           # APScheduler 잡 등록
│   ├── storage/             # SQLite repos(watchlist/signal_log/…)
│   └── bot/                 # 명령 라우팅 + 메시지 빌더
└── tests/
    ├── fixtures/
    └── ...
```

### 11.2 Implementation Order

1. [ ] config + storage(SQLite) + 안전 래퍼 골격
2. [ ] datasource/base + KIS 어댑터(인증·시세·OHLCV·순위) — **KIS 문서 검증 선행**
3. [ ] notify/base + Telegram 어댑터 + bot 명령 골격
4. [ ] indicators → patterns → screener(검색식/시그널) (+단위 테스트)
5. [ ] analysis(Claude 비전·요약)
6. [ ] ranking + screener 결합 → 매수 시그널 파이프라인
7. [ ] alerts(관심종목) + briefing + scheduler
8. [ ] 통합/E2E(시뮬) 테스트

### 11.3 Session Guide

> `/pdca do stock-insight-bot --scope module-N` 으로 세션별 점진 구현.

#### Module Map

| Module | Scope Key | Description | Phase | Est. Turns |
|--------|-----------|-------------|:----:|:----------:|
| 기반(config·storage·KIS 인증·Telegram·봇 골격) | `module-1` | 봇이 켜지고 관심종목 등록·조건 알림 동작 | P1 | 40-55 |
| 분석 코어(indicators·patterns·screener·analysis) | `module-2` | 종목/이미지 온디맨드 분석 + 검색식 텍스트 | P2 | 45-60 |
| 핫종목·매수시그널·브리핑·스케줄러 | `module-3` | 핫종목 평가→매수 시그널 자동 알림, 종가 브리핑 | P3 | 40-55 |
| 인사이트 종합·미국 확장 | `module-4` | 시그널 종합 요약, US datasource 어댑터 | P4 | 30-45 |

#### Recommended Session Plan

| Session | Phase | Scope | Turns |
|---------|-------|-------|:-----:|
| Session 1 | Plan + Design | 전체 | 30-35 (완료) |
| Session 2 | Do | `--scope module-1` | 40-55 |
| Session 3 | Do | `--scope module-2` | 45-60 |
| Session 4 | Do | `--scope module-3` | 40-55 |
| Session 5 | Check + QA + Report | 전체 | 30-45 |

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-05-26 | Initial design — Option C(실용 균형) 선택, Python 적용, 핫종목 매수 시그널 파이프라인 중심 | viw0816 |
