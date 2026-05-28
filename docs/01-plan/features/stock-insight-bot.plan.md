---
template: plan
version: 1.3
feature: stock-insight-bot
date: 2026-05-26
author: viw0816
project: stock_report
status: Draft
---

# Stock Insight Bot Planning Document

> **Summary**: KIS Open API + Claude AI 기반으로 한국 주식의 핫 테마·관심종목 상태·차트 패턴을 분석하고, 텔레그램 봇으로 실시간 알림과 온디맨드 매매 인사이트를 제공하는 종합 주식 알림 프로그램.
>
> **Project**: stock_report
> **Version**: 0.1.0
> **Author**: viw0816
> **Date**: 2026-05-26
> **Status**: Draft

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | 한국 주식 매매 시 테마 동향·관심종목 상태·차트 패턴·종가 직전 흐름 정보가 여러 곳에 흩어져 있어, 수동 추적 중 타이밍을 놓치고 일관된 판단 근거 없이 매매하게 된다. |
| **Solution** | KIS Open API로 시세를 수집하고, 기술지표·패턴·검색식을 결합해 텔레그램 봇으로 **① 금일 핫 종목 리스트(KIS 순위 API) 기반 검색식+패턴 매수추천 알림(핵심)** ② 관심종목 알림 ③ 종목/차트 온디맨드 패턴 진단 ④ 종가 직전 브리핑 ⑤ 증시 요약(온디맨드)을 제공하는 백그라운드 봇. **전체 시장 스캔은 하지 않음.** |
| **Function/UX Effect** | 봇이 알림을 자동 push하면서, 사용자가 종목코드나 차트 이미지를 메신저로 보내면 즉시 패턴을 진단해 응답하는 양방향 인터페이스. 모니터링과 온디맨드 분석을 한 채널에서 처리. |
| **Core Value** | 흩어진 정보를 자동 수집·분석해 매매 의사결정에 쓸 수 있는 근거 있는 인사이트를 적시에 제공 — "정보 추적"을 시스템에 위임하고 판단에만 집중. |

---

## Context Anchor

> Auto-generated from Executive Summary. Propagated to Design/Do documents for context continuity.

| Key | Value |
|-----|-------|
| **WHY** | 흩어진 주식 정보(테마·관심종목·차트 패턴·종가 직전 동향)를 수동 추적하느라 타이밍을 놓치고 일관된 판단 근거가 없음 |
| **WHO** | KIS Open API 계정을 보유한 개인 투자자(본인). Python/메신저 환경, 한국 주식 우선 |
| **RISK** | KIS API rate limit·토큰 만료로 데이터 누락/차단; AI 분석 환각으로 잘못된 매매 신호 |
| **SUCCESS** | 금일 핫 종목에 검색식+패턴 적용해 매수추천 여부 알림 발송 · 관심종목 조건 알림 · 종목/이미지 온디맨드 패턴 진단 · 종가 직전 브리핑 · 증시 요약(온디맨드) |
| **SCOPE** | P1 기반(KIS+텔레그램+관심종목) → P2 분석(지표+패턴+이미지) → P3 테마+종가브리핑 → P4 인사이트 종합/미국 확장 |

---

## 1. Overview

### 1.1 Purpose

한국 주식 투자자가 매매 판단에 필요한 정보(테마 동향, 관심종목 상태, 차트 패턴, 종가 직전 핫 종목과 상승 이유)를 한 메신저 채널에서 자동·온디맨드로 받아볼 수 있게 한다. 정보 수집·분석을 시스템에 위임하고, 사용자는 의사결정에 집중한다.

### 1.2 Background

- 사용자는 KIS(한국투자증권) Open API 계정을 보유하여 합법적인 실시간 시세 수집 채널을 확보하고 있다.
- 한국 시장은 "테마주" 흐름이 강해, 당일/주간 핫 테마 파악이 매매에서 중요하다.
- 관심종목 모니터링과 종가 직전 브리핑은 상시 실행/스케줄링이 전제이며, 패턴 진단(눌림목·돌파매매 등)은 온디맨드 요청이 자연스럽다.
- AI(Claude) 비전·요약 능력으로 차트 이미지 해석과 상승 이유/테마 요약을 보강하고, 기술적 지표 계산으로 수치 근거를 함께 제공한다.

### 1.3 Related Documents

- PRD: 없음 (필요 시 `/pdca pm stock-insight-bot` 권장)
- Design: `docs/02-design/features/stock-insight-bot.design.md` (다음 단계)
- 참고: KIS Open API 공식 문서, Anthropic Claude API 문서, python-telegram-bot 문서

---

## 2. Scope

### 2.1 In Scope

- [ ] KIS Open API 연동: OAuth 토큰 발급/자동 갱신, 시세(현재가·OHLCV·등락률·거래량) 조회
- [ ] 관심종목 등록/조회/삭제 및 로컬 영속화
- [ ] 관심종목 상태 모니터링 + 조건 기반 알림(등락률 임계치, 거래량 급증 등)
- [ ] 텔레그램 봇 양방향 인터페이스: 알림 push + 명령/종목/이미지 입력 수신
- [ ] 기술적 지표 계산 엔진(이동평균, 거래량, RSI 등)
- [ ] 규칙 기반 패턴 진단(눌림목, 돌파매매 등)
- [ ] 종목코드 입력 시 온디맨드 패턴 분석 응답 (텍스트 기반)
- [ ] 차트 이미지 입력 시 Claude 비전 기반 패턴 분석 (이미지 기반)
- [ ] 패턴 진단 시 조건식(검색식) 텍스트 제공 (HTS 조건검색에 쓸 수 있는 형태)
- [ ] 금일 핫 종목 리스트 수집 (KIS 순위분석 API: 등락률/거래량/거래대금 상위)
- [ ] 핫 종목 + 관심종목에 검색식+패턴 적용 → 매수추천 여부 판정 → 알림 발송 (핵심 자동 push)
- [ ] 증시 요약 / 핫 테마 분석 (온디맨드, 핫 종목 리스트 기반 + LLM 요약 — 전체 시장 스캔 없이)
- [ ] 종가 직전 브리핑 스케줄러(핫 종목 + 상승 이유 + 테마)
- [ ] 인사이트 종합(여러 신호를 묶은 요약 메시지)

### 2.2 Out of Scope

- 미국 주식 지원 (Phase 4에서 동일 플랫폼 위 확장 — MVP 제외)
- 전체 시장(모든 상장 종목) 스캔/스크리닝 — 평가 유니버스는 KIS 순위 API의 핫 종목 + 관심종목으로 한정
- 자동 주문/매매 실행 (정보·알림 제공만, 직접 주문 X)
- 패턴 학습용 ML 모델 학습/추론 (Phase 4 선택 항목으로 보류)
- 백테스팅 엔진, 포트폴리오 손익 관리
- 다중 사용자/계정 SaaS화 (개인용 단일 사용자 전제)

---

## 3. Requirements

### 3.1 Functional Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-01 | KIS Open API 연동 (OAuth 토큰 발급·자동 갱신, 시세/OHLCV 조회) | High | Pending |
| FR-02 | 관심종목 등록/조회/삭제 및 영속화 (SQLite) | High | Pending |
| FR-03 | 관심종목 상태 모니터링 + 조건 알림 (등락률 임계치·거래량 급증) | High | Pending |
| FR-04 | 텔레그램 봇 양방향 인터페이스 (알림 push + 명령/종목/이미지 수신) | High | Pending |
| FR-05 | 기술적 지표 계산 엔진 (MA, 거래량, RSI 등) | High | Pending |
| FR-06 | 규칙 기반 패턴 진단 (눌림목·돌파매매 등) | High | Pending |
| FR-07 | 종목코드 입력 시 온디맨드 패턴 분석 응답 | High | Pending |
| FR-08 | 차트 이미지 입력 시 Claude 비전 패턴 분석 | High | Pending |
| FR-14 | 패턴 진단 시 조건식(검색식) 텍스트 제공 | High | Pending |
| FR-15 | 금일 핫 종목 리스트 수집 (KIS 순위분석 API) | High | Pending |
| FR-16 | 핫 종목 + 관심종목에 검색식+패턴 적용 → 매수추천 여부 판정·알림 (핵심) | High | Pending |
| FR-09 | 증시 요약 / 핫 테마 분석 (온디맨드, 전체 시장 스캔 없이) | Medium | Pending |
| FR-10 | 종가 직전 브리핑 스케줄러 (핫 종목 + 상승 이유 + 테마) | Medium | Pending |
| FR-11 | 인사이트 종합 리포트 (여러 신호 결합 요약) | Medium | Pending |
| FR-12 | 미국 시장 확장 (데이터 소스 어댑터 교체) | Low | Pending |
| FR-13 | 패턴 학습/ML 모델 (선택, 추후) | Low | Pending |

### 3.2 Non-Functional Requirements

| Category | Criteria | Measurement Method |
|----------|----------|-------------------|
| Performance | 시세 조회 응답 < 2s, 온디맨드 분석(AI 포함) 응답 < 10s | 로그 타임스탬프 측정 |
| Reliability | KIS API rate limit 준수, 토큰 자동 갱신, 실패 시 지수 백오프 후 하드스톱 | 장시간 가동 로그·에러율 |
| 평가 부하 | 평가 대상은 핫 종목(순위 API) + 관심종목으로 한정, 배치 호출 + 캐시로 KIS 호출 최소화 (전체 스캔 없음) | 평가당 호출 수·소요시간 |
| Security | API 키/봇 토큰은 `.env` 분리, 로그·메시지에 비밀키 미노출, 허용 chat_id 화이트리스트 | 코드 리뷰·시크릿 스캔 |
| Cost | Claude API 호출 비용 관리 (이미지/요약 호출 최소화 + 결과 캐싱) | 일/월 호출 수·토큰 집계 |
| Maintainability | 데이터 소스(KIS)와 메신저(텔레그램)를 어댑터로 분리해 추후 교체 가능 | 인터페이스 경계 확인 |

---

## 4. Success Criteria

### 4.1 Definition of Done

- [ ] 관심종목 조건 알림이 실제로 텔레그램에 정상 발송됨
- [ ] 종목코드 입력 시 기술지표 기반 패턴 진단이 응답됨
- [ ] 차트 이미지 입력 시 AI가 패턴을 읽어 응답함
- [ ] 패턴 진단 시 조건식(검색식)이 텍스트로 함께 제공됨
- [ ] 금일 핫 종목 + 관심종목에 검색식+패턴을 적용해 매수추천 여부를 알림으로 발송함
- [ ] 증시 요약/핫 테마가 온디맨드로 제공됨
- [ ] 종가 직전 브리핑이 스케줄에 맞춰 자동 발송됨
- [ ] KIS 토큰이 만료 시 자동 갱신되어 무중단 동작함

### 4.2 Quality Criteria

- [ ] 비밀키(.env)가 코드/로그/git에 노출되지 않음 (`.gitignore` 포함)
- [ ] KIS API 호출에 rate limit + 재시도(지수 백오프) + 하드스톱 적용
- [ ] 핵심 모듈(지표 계산, 패턴 판정)에 단위 테스트 존재
- [ ] AI 출력에 면책 문구와 지표 수치 병기 (환각 완화)

---

## 5. Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| KIS API 호출 제한 초과·토큰 만료로 데이터 누락/차단 | High | Medium | rate limiter + 토큰 자동 갱신 + 지수 백오프; 연속 실패 시 하드스톱 + 알림 (전역 CLAUDE.md §7 준수) |
| AI 분석 환각으로 잘못된 매매 신호 | High | Medium | AI는 보조 수단으로 명시, 지표 수치 병기, 모든 분석에 면책 문구 |
| 봇 토큰 노출 시 무단 접근 | Medium | Low | 허용 chat_id 화이트리스트, 토큰 `.env` 분리, git 제외 |
| 시세 데이터 지연/정확도 한계 | Medium | Medium | KIS 공식 API 사용, 캐시 TTL 설정, 데이터 시각 표기 |
| 매수추천 알림이 투자자문/확정 신호로 오인 | High | Medium | '추천' 대신 '시그널/참고'로 표기, 판정 근거(지표·패턴·검색식 충족 항목) 병기, 면책 문구 필수, 최종 책임은 사용자 |
| 핫 종목 평가 시 KIS 호출 증가 | Medium | Low | 평가 대상을 핫 종목(순위 API)+관심종목으로 한정, 배치 호출 + 캐시 (전체 스캔 안 함) |
| Claude API 비용 과다 | Medium | Medium | 이미지/요약 호출 최소화, 결과 캐싱, 일일 호출 상한 |
| 투자자문으로 오해될 법적 리스크 | Medium | Low | 개인용 정보 도구임을 명시, 투자 책임은 사용자에게 있음 면책 |

---

## 6. Impact Analysis

> 신규 프로젝트(빈 디렉토리)로, 기존 코드 소비자가 없음. 외부 시스템 의존성을 명시한다.

### 6.1 Changed Resources

| Resource | Type | Change Description |
|----------|------|--------------------|
| KIS Open API | External API | 신규 연동 — 시세 조회, OAuth 토큰 발급/갱신 |
| Telegram Bot API | External API | 신규 연동 — 메시지 송수신, 이미지 수신 |
| Anthropic Claude API | External API | 신규 연동 — 차트 이미지 비전, 테마/상승이유 요약 |
| SQLite DB | New Storage | 신규 생성 — 관심종목·알림 이력·분석 캐시 |

### 6.2 Current Consumers

| Resource | Operation | Code Path | Impact |
|----------|-----------|-----------|--------|
| (none) | - | 신규 프로젝트, 기존 소비자 없음 | None |

### 6.3 Verification

- [x] 신규 프로젝트로 기존 기능 파괴 위험 없음
- [ ] 외부 API 3종(KIS/Telegram/Claude)의 인증·rate limit 정책 확인
- [ ] `.env`/시크릿 git 제외 확인

---

## 7. Architecture Considerations

### 7.1 Project Level Selection

> 본 프로젝트는 웹 앱이 아닌 **Python 백그라운드 서비스 + 봇** 형태. bkit 레벨 기준으로는 기능별 모듈 구조의 Dynamic에 해당.

| Level | Characteristics | Recommended For | Selected |
|-------|-----------------|-----------------|:--------:|
| **Starter** | 단순 구조 | 정적 사이트 | ☐ |
| **Dynamic** | 기능별 모듈, 외부 API 통합 | 백엔드/봇/외부 API 앱 | ☑ |
| **Enterprise** | 엄격한 레이어 분리, DI | 고트래픽·복잡 시스템 | ☐ |

### 7.2 Key Architectural Decisions

| Decision | Options | Selected | Rationale |
|----------|---------|----------|-----------|
| Language | Python / Node.js | **Python 3.11+** | 데이터 분석(pandas)·KIS 파이썬 예제·기존 사용자 환경에 적합 |
| 인터페이스 | 텔레그램 / 카카오톡 | **텔레그램 (기본)** | 봇이 이미지·텍스트를 양방향 수신 가능; 카카오는 개인용 봇/이미지 수신 제약 큼. 카카오는 push-only 대안으로 보류 |
| 봇 프레임워크 | python-telegram-bot / aiogram | **python-telegram-bot** | 성숙·문서 풍부, async 지원 |
| 시세 데이터 | KIS Open API / 무료 라이브러리 | **KIS Open API** | 사용자 보유, 합법·정확. 어댑터로 분리해 추후 교체 가능 |
| AI 분석 | Claude API / 로컬 모델 | **Anthropic Claude API** | 비전(차트 이미지) + 요약 품질, prompt caching으로 비용 관리 |
| 평가 유니버스 | 시장 전체 / 핫 종목+관심종목 | **금일 핫 종목(KIS 순위 API) + 관심종목** | 전체 스캔 불필요, KIS 호출 최소화, 핵심 가치(매수 시그널)에 집중 |
| 지표 계산 | pandas-ta / TA-Lib / 직접 구현 | **pandas + pandas-ta** | 설치 용이(TA-Lib 빌드 부담 회피), 충분한 지표 |
| 스케줄링 | APScheduler / cron | **APScheduler** | 프로세스 내 스케줄(종가 브리핑·주기 모니터링), 크로스플랫폼 |
| Storage | SQLite / JSON 파일 | **SQLite** | 관심종목·이력·캐시에 적합, 경량 |
| Config/Secret | pydantic-settings + .env | **pydantic-settings + .env** | 타입 안전 설정, 시크릿 분리 |

### 7.3 Clean Architecture Approach

```
Selected Level: Dynamic (Python 적용)

Folder Structure Preview (Python adaptation):
┌─────────────────────────────────────────────────────┐
│ src/                                                 │
│   bot/          텔레그램 핸들러·명령·라우팅           │
│   datasource/   KIS API 클라이언트(어댑터 인터페이스) │
│   indicators/   기술지표 계산 엔진                    │
│   patterns/     눌림목·돌파 등 규칙 기반 패턴 판정    │
│   screener/     검색식 정의·핫종목+관심종목 매수시그널 │
│   ranking/      KIS 순위 API로 금일 핫 종목 수집       │
│   analysis/     AI(Claude) 비전·테마·상승이유 분석    │
│   themes/       핫 테마 집계·그룹핑                   │
│   alerts/       관심종목 모니터링·조건 알림           │
│   briefing/     종가 직전 브리핑 생성                 │
│   scheduler/    APScheduler 작업 등록                 │
│   storage/      SQLite 모델·리포지토리                │
│   config/       pydantic 설정·.env 로딩               │
│ main.py         엔트리포인트(봇+스케줄러 기동)        │
│ tests/          단위 테스트                           │
└─────────────────────────────────────────────────────┘
```

---

## 8. Convention Prerequisites

### 8.1 Existing Project Conventions

- [ ] `CLAUDE.md` 코딩 컨벤션 섹션 (프로젝트 로컬 — 미존재)
- [x] 전역 `~/.claude/CLAUDE.md` 행동 하네스 존재 (외부 서비스 안전 규칙 §7 적용 필수)
- [ ] `CONVENTIONS.md` (미존재)
- [ ] Ruff/Black 설정 (`pyproject.toml`)
- [ ] 타입 체크 (`mypy` 설정)

### 8.2 Conventions to Define/Verify

| Category | Current State | To Define | Priority |
|----------|---------------|-----------|:--------:|
| **Naming** | missing | snake_case(함수/변수), PascalCase(클래스) | High |
| **Folder structure** | missing | 위 7.3 모듈 구조 | High |
| **Import order** | missing | isort/ruff 기준 (stdlib→3rd→local) | Medium |
| **Environment variables** | missing | 8.3 목록 | High |
| **Error handling** | missing | 외부 API 재시도·하드스톱 패턴(전역 §7) | High |
| **Logging** | missing | 구조적 로그, 비밀키 마스킹 | Medium |

### 8.3 Environment Variables Needed

| Variable | Purpose | Scope | To Be Created |
|----------|---------|-------|:-------------:|
| `KIS_APP_KEY` | KIS Open API 앱 키 | Server | ☑ |
| `KIS_APP_SECRET` | KIS Open API 앱 시크릿 | Server | ☑ |
| `KIS_ACCOUNT_NO` | KIS 계좌번호(시세용) | Server | ☑ |
| `TELEGRAM_BOT_TOKEN` | 텔레그램 봇 토큰 | Server | ☑ |
| `TELEGRAM_ALLOWED_CHAT_IDS` | 허용 사용자 chat_id 화이트리스트 | Server | ☑ |
| `ANTHROPIC_API_KEY` | Claude API 키 | Server | ☑ |
| `DB_PATH` | SQLite 파일 경로 | Server | ☑ |

### 8.4 Pipeline Integration

| Phase | Status | Document Location | Command |
|-------|:------:|-------------------|---------|
| Phase 1 (Schema) | ☐ | `docs/01-plan/schema.md` | `/pipeline-next` |
| Phase 2 (Convention) | ☐ | `docs/01-plan/conventions.md` | `/pipeline-next` |

---

## 9. Next Steps

1. [ ] Design 문서 작성 (`/pdca design stock-insight-bot`) — 3가지 아키텍처 옵션 비교, 모듈/세션 가이드, KIS 인증 흐름·DB 스키마·메시지 포맷 확정
2. [ ] 텔레그램 vs 카카오 최종 확정 (Design에서 검증)
3. [ ] Phase 1(MVP) 구현 시작: KIS 연동 + 텔레그램 봇 + 관심종목 알림

### 권장 구현 단계 (Phase 분할)

| Phase | 내용 | 관련 FR |
|-------|------|---------|
| **P1 — 기반(MVP)** | KIS 연동·토큰 관리, 텔레그램 봇 골격, 관심종목 관리·조건 알림 | FR-01~04 |
| **P2 — 분석 코어** | 지표 엔진, 규칙 기반 패턴 진단, 종목 온디맨드 분석, 차트 이미지 AI 분석, 조건식(검색식) 텍스트 제공 | FR-05~08, FR-14 |
| **P3 — 핫종목·매수시그널·브리핑** | 금일 핫 종목 수집(순위 API), 검색식+패턴 매수추천 알림(핵심), 증시 요약(온디맨드), 종가 직전 브리핑 | FR-09~10, FR-15~16 |
| **P4 — 종합·확장** | 인사이트 종합, 미국 시장 확장, (선택) 패턴 학습 | FR-11~13 |

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-05-26 | Initial draft (요구사항 확인 4문답 반영) | viw0816 |
| 0.2 | 2026-05-26 | 패턴 분석을 이미지+텍스트 병행으로 확정, 조건식(검색식) 제공(FR-14) + 조건식 스캐너(FR-15) 추가 | viw0816 |
| 0.3 | 2026-05-26 | 핵심 구조 명확화: 전체 시장 스캔 제외 → 금일 핫 종목(FR-15)+관심종목에 검색식+패턴 적용해 매수추천 알림(FR-16). 증시 요약은 온디맨드(FR-09) | viw0816 |
