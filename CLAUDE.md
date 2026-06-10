# stock_report — 프로젝트 CLAUDE.md

> 전역 `~/.claude/CLAUDE.md`(에이전트 행동 하네스)를 **상속**한다. 이 파일은 본 프로젝트에만 적용되는 규칙·구조·컨벤션만 정의하며, 충돌 시 이 파일이 우선한다.
> 근거 문서: [Plan](docs/01-plan/features/stock-insight-bot.plan.md) · [Design](docs/02-design/features/stock-insight-bot.design.md)

---

## 1. 프로젝트 개요

한국 주식 종합 인사이트 봇. KIS Open API로 시세를 수집하고 기술지표·패턴·검색식 + Claude AI를 결합해, **금일 핫 종목 + 관심종목**에 매수 시그널을 판정해 **텔레그램으로 알림**한다. 종목코드·차트 이미지 온디맨드 분석, 종가 직전 브리핑, 증시 요약(온디맨드)도 제공한다.

**핵심 자동 파이프라인**: 스케줄러 → 핫종목(KIS 순위 API) ∪ 관심종목 → 지표·패턴·검색식 → 매수 시그널 → 텔레그램 알림.

---

## 2. 아키텍처 (Option C — 실용 균형)

Ports & Adapters를 **바뀔 경계에만** 적용한다. 나머지는 기능 모듈로 단순하게 둔다.

- **포트(추상화 대상)**: `MarketDataSource`(미국 확장점), `Notifier`(카카오 교체점). 이 둘만 인터페이스로 격리.
- **순수 도메인**: `indicators` / `patterns` / `screener` 는 외부 의존 없는 순수 함수 → 결정론적 단위 테스트.
- **어댑터 주입**: 구체 어댑터(KIS·Telegram) 조립은 `main.py`에서만. 기능 모듈은 포트에만 의존.

의존 방향: `bot/scheduler → 기능모듈 → domain` / `기능모듈 → ports ← adapters`. **domain은 외부를 절대 import 하지 않는다.**

---

## 3. 기술 스택

| 영역 | 선택 |
|------|------|
| 언어 | Python 3.11+ (타입힌트 필수) |
| 봇 | python-telegram-bot (async) |
| 시세 | KIS(한국투자증권) Open API |
| AI | Anthropic Claude API (비전·요약) |
| 지표 | pandas + pandas-ta |
| 스케줄 | APScheduler |
| 저장소 | SQLite |
| 설정 | pydantic-settings + `.env` |
| 린트/포맷 | ruff (+ ruff format), mypy 권장 |
| 테스트 | pytest, respx/responses, pytest-asyncio |

---

## 4. 디렉토리 구조

```
main.py              봇+스케줄러 기동, 어댑터 조립(주입 지점)
src/
  config/            pydantic 설정·.env
  datasource/        base.py(MarketDataSource port) + kis/ 어댑터
  notify/            base.py(Notifier port) + telegram/ 어댑터
  indicators/        지표 계산 (순수)
  patterns/          눌림목·돌파 등 판정 (순수)
  screener/          검색식 매칭 + 매수 시그널 판정 (순수)
  ranking/           KIS 순위 API로 핫 종목 수집
  alerts/            관심종목 모니터링
  analysis/          Claude 비전·요약 래퍼
  briefing/          종가 직전 브리핑
  scheduler/         APScheduler 잡 등록
  storage/           SQLite repos
  bot/               명령 라우팅 + 메시지 빌더
tests/
  fixtures/          OHLCV·KIS 응답 샘플
```

- 새 코드는 위 모듈 경계에 맞춘다. 도메인 로직을 어댑터/봇에 섞지 않는다.
- 파일/함수: `snake_case`, 클래스: `PascalCase`, 상수: `UPPER_SNAKE`.

---

## 5. 프로젝트 규칙 (MUST)

1. **전체 시장 스캔 금지.** 평가 유니버스 = 금일 핫 종목(KIS 순위 API) + 관심종목. 모든 상장 종목 순회 X.
2. **매수 "추천"이라 단정하지 않는다.** "참고용 시그널"로 표기하고, 모든 시그널/분석에 **근거 수치(지표·검색식 충족 항목) + 면책 문구**를 반드시 동반한다. (환각·법적 오인 방지)
3. **AI는 보조.** 1차 근거는 지표·검색식 수치, 2차가 LLM 코멘트. 수치 없는 AI 단독 결론으로 시그널을 내지 않는다.
4. **domain(indicators/patterns/screener)은 순수 유지.** 네트워크·DB·SDK import 금지. 입력은 OHLCV 등 값, 출력은 값.
5. **code + test = 1세트.** 모듈 구현 시 대응 테스트를 함께 작성한다. 테스트 없는 모듈은 "완료" 아님.
6. **KIS 정확한 엔드포인트/TR_ID는 추측 금지.** 어댑터 구현 전 KIS 공식 문서로 경로·TR_ID·파라미터를 검증하고 반영한다. (실전/모의 환경 차이 주의)
7. **완료 주장 전 검증.** "동작한다"고 말하기 전 실제 실행/테스트 결과를 확인한다. (전역 §4)
8. **기능 완전성 전수 점검 (요청 누락 방지).** 종목/섹션 단위 기능(예: AI요약·배지·뉴스)을 추가할 때는 **영향받는 모든 경로·섹션을 한 번에 전수 확인**한다. 한 섹션만 고치고 끝내지 않는다.
   - 체크: (a) 그 기능이 표시돼야 할 **모든 리포트 모드**(KR pre/post/midday/premarket/open, US morning/premarket/intraday/afterhours)에 들어갔는가? (b) 템플릿이 **렌더하는 모든 종목 리스트(snap.X)**가 데이터 생성 경로(예: `summarize_*_stocks` pools)에 포함됐는가? (렌더 리스트 ↔ 생성 pools 불일치 = 버그. 예: 스크리닝이 `us_screen_ranked`인데 pools엔 `us_screen_groups`만 → AI버튼 누락)
   - 가드: `tests/test_report_ai_coverage.py`(렌더 종목리스트 ⊆ summarize pools 강제) + 일관성 점검 크론(`report_audit`, 평일 14:00). 큰 변경 후 `/code-review` 또는 code-analyzer로 전수 리뷰.

---

## 6. 외부 API 안전 (전역 CLAUDE.md §7 적용)

KIS·Claude·Telegram **모든 외부 호출**에 다음을 적용한다:

- 요청 간 **랜덤 딜레이**(고정값 금지), 배치(핫종목 N개) 사이 휴식
- 최대 **재시도 3회 + 지수 백오프**
- **HARD STOP**: HTTP 429 / 인증 급변(갑작스러운 401·403) / 연속 타임아웃 3회 → 즉시 중단 + 사용자 알림, 자동 재시도 금지
- KIS **access_token 자동 갱신**(만료 전), 토큰은 gitignore된 런타임 캐시 파일에 보관
- 세션 상태(성공/실패/마지막 성공) 추적

---

## 7. 시크릿 · 보안

- `KIS_APP_KEY`/`KIS_APP_SECRET`/`KIS_ACCOUNT_NO`, `TELEGRAM_BOT_TOKEN`, `ANTHROPIC_API_KEY` 는 **`.env`에만** 두고 git에서 제외(`.gitignore`).
- 텔레그램은 `TELEGRAM_ALLOWED_CHAT_IDS` **화이트리스트** 외 메시지는 무시.
- 로그·메시지에 토큰/시크릿 **마스킹**. 절대 출력·커밋하지 않는다.
- 종목코드 입력 검증(6자리 숫자) 등 명령 인자 sanitize.
- Claude는 `AI_DAILY_CALL_LIMIT`로 일일 호출 상한, 초과 시 AI 기능만 비활성.

---

## 8. 테스트

- L1 단위: `indicators`/`patterns`/`screener` 순수 로직 — `tests/fixtures`의 눌림목·돌파 OHLCV 픽스처로 결정론 검증.
- L2 통합: KIS 어댑터(respx 모킹) — 토큰 갱신·OHLCV 파싱·429 백오프·순위 파싱 / Notifier·Claude 모킹.
- L3 E2E(시뮬): 외부 모킹 상태로 `/analyze`·시그널 잡·관심종목 알림·이미지 분석·권한 차단 플로우.

---

## 9. 하지 말 것 (프로젝트 한정)

- 전체 시장 종목 순회/스크리닝
- 시그널을 "확정 추천"으로 단정 (면책·근거 없이 발송)
- domain 레이어에 외부 의존(HTTP/DB/SDK) 주입
- KIS 엔드포인트/TR_ID를 문서 검증 없이 하드코딩
- 시크릿을 코드/로그/커밋에 노출
- 외부 호출 시 고정 딜레이 사용

---

## 10. 참조

- 진행: bkit PDCA. 상태 `/pdca status`. 다음 구현은 `/pdca do stock-insight-bot --scope module-1`.
- Module Map: module-1(기반) → module-2(분석 코어) → module-3(핫종목·시그널·브리핑) → module-4(종합·미국 확장). 상세는 Design §11.3.
