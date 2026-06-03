# 다음 세션 핸드오프 — 2026-06-03 정리

> 병렬 세션(자동매매·미국스크리닝·리포트)이 꼬여 전부 닫고 재시작. **놓치면 안 되는 pending/계획**을 워크스트림별로 정리.
> 현재 origin/main = `2803ca3` (push 완료). 로컬 테스트 140개 통과.
> 신뢰도: WS1(자동매매)=직접 구현(높음), WS2/WS3=병렬작업 머지 산출물 기준(부분) — 상세는 해당 메모리/커밋 참조.

---

## 🔴 최우선 블로커 (배포 막고 있음) — 서버 git 분기

**서버(`134.185.109.195:/home/ubuntu/stock_report`) main이 origin과 분기.**
- origin보다 **41 behind / 4 ahead**. 서버의 4 ahead = **자동 생성 리포트 커밋이 origin에 push 안 돼 서버에만 쌓임**(`📊 14:40/16:30/16:44`).
- `config/screener.yaml` 로컬 수정(RAM 축소판: kospi 100 / kosdaq 50 / min_amount 1000억)도 있음.
- ⇒ 단순 `git pull` 불가(충돌·덮어쓰기 위험).

**결정 필요(다음 세션 첫 작업):**
1. **(추천)** `git stash`(screener) → `git reset --hard origin/main`(서버 로컬 리포트 커밋 4개 폐기 — 자동생성 HTML이라 무해) → screener 축소 재적용(kospi100/kosdaq50/min 100000000000).
2. 또는 `git merge origin/main`(docs/reports HTML 충돌 수동해소, 지저분).

**별개 근본문제:** 서버가 리포트 커밋을 origin에 **push 못 하고 있음**(분기 누적 원인). 진단 필요 — PAT/원격 URL/충돌 중 무엇인지. (메모리 [[server_deployment]]: 과거 PAT 미설정 이슈.)

---

## WS1. 자동매매 구현 — 코드 완료, 서버 배포만 남음

### ✅ 완료 (origin/main 반영, 140테스트 통과)
- `src/trading/kis_order.py` — `KisOrderClient`: hashkey·order_cash·inquire_psbl_order·inquire_balance.
  - 🔴 주문 TR **공식검증**: 모의 매수 `VTTC0012U`/매도 `VTTC0011U` (구 추정 VTTC0802U/0801U는 **틀림**, 2025 NXT 개편). body에 `EXCG_ID_DVSN_CD="KRX"`. 매수가능 `VTTC8908R`, 잔고 `VTTC8434R`.
- `scripts/smoke_paper_order.py` — 주문 배관 스모크(dry-run 기본, `--send` 게이트).
- `src/trading/{sizing,ma_exit,positions,top3_bridge,auto_trader}.py` — auto_trader v1.
  - 진입: 종가베팅 Top3, **매수당 100만원 이내**(floor(100만/현재가)).
  - 청산 `decide_exit`: 🟢PULLBACK→홀드 / ⚠️CORRECTION→50% / 60MA 2연속→전량 / 20MA 2연속→50%.
  - **데이터=real키 / 주문=paper키 분리**(`_build_clients`, 주문 env="paper" 고정). 모의 도메인 OHLCV 500 회피.
  - dry-run 기본, 텔레그램 체결 알림.
- `src/config/settings.py` — `KIS_PAPER_APP_KEY/SECRET/ACCOUNT_NO` 필드 추가. `.env.example` 문서화.
- `src/market_report/pipeline.py` — pre Top3를 `data/top3_<date>_pre.json`에 방어적 기록(브리지).
- 설계/플랜: `docs/superpowers/specs|plans/2026-06-03-paper-order-smoke*`, `2026-06-03-paper-auto-trader*`.

### ⏳ PENDING (다음 세션) — **사용자 결정: v1 그대로 배포**(동시보유 상한·익절 없이)
1. **서버 배포** (위 블로커 해소 후):
   - 서버 `.env`에 **모의 키 투입**(현재 KIS_PAPER_* 전부 비어있음). 사용자 시크릿 — 서버에서 직접 편집. `KIS_ENV=real` 유지.
   - cron 등록(평일): **buy 15:20**(사용자 확정), **sell ~15:50~16:00**. `venv/bin/python -m src.trading.auto_trader buy|sell --send`.
   - 전제: pre 리포트(14:50)가 top3 JSON 생성해야 buy 동작.
   - 첫날 dry-run(SSH 수동, `--send` 없이) 1회 검증 → 이후 --send cron.
2. **라이브 미검증**: `smoke_paper_order.py --send` / `auto_trader --send` **한 번도 안 돌림**. 장중 첫 모의주문으로 TR/파라미터 empirical 확인 필요(rt_cd=0·odno).
3. **deferred(설계상)**: 1차 60분 손절(KIS 분봉 ~1.x거래일 제약으로 라이브 계산 불가 → [[scalping_experiment]]), 동시보유 상한, 익절(목표가), 실전 전환, 진입전략 walk-forward 재검증.

### 참조 메모리
[[paper_auto_trader]] (구현 상태 상세), [[kis_migration]], [[strategy_validation]].

---

## WS2. 미국주식 스크리닝 개선 — (병렬작업, 부분 파악)

### ✅ 머지됨
- 커밋 `eebc3fa` "feat(us): 미국 종목 스크리닝 P1~P4 + 백테스트 고도화 + cross_signal".
- `tests/test_us_screening.py` 존재(내 cross_signal SSOT 상수 사용).

### ⏳ 확인 필요 (이 워크스트림은 병렬이라 내가 상세 미파악)
- **다음 세션 첫 작업: [[us_screening]] 메모리 정독** — A/B/C/D 엔진 무수정 재사용, FDR 섹터, yfinance 배치, "P1(C+S&P500) 완료" 기록. P2/P3/P4 진척·미완 항목 확인.
- us_screening이 cross_signal SSOT(`src/patterns/core.py:ma_cross_signal`)에 의존 → 그 함수 시그니처/반환값(대문자 "PULLBACK"/"CORRECTION") 변경 시 동기 주의.

---

## WS3. 리포트 개선 — (병렬작업, 부분 파악)

### ✅ 머지됨
- 투자자 수급 현황표(지수 아래, 개인/외국인/기관 순매수) — `d10ac91`, `950bf46`(3일 일자별표+텔레그램 개선).
- 주요 지수 2x2 차트 종가베팅 스타일(1주일 확대) — `95623bf`.
- A/B/C/D 스크린 거래대금·시총 **3000억 필터** — `ac42ad1`.
- cross_signal 배지(🟢단기눌림/⚠️조정시작) — `d8ca99d` + **대소문자 버그 수정 `2803ca3`**(report.html 'pullback'→'PULLBACK').
- naver 순위 ticker↔name 오정렬 근본수정 `176be4a`, 주도테마 ETF/비테마 필터 `dc10350`.

### ⏳ PENDING / 미완
- **cross_signal 배지 미표시 영역**: 현재 Top3·전략픽에만. **보유종목표(holdings_status)·텔레그램엔 미표시** — 사용자 핵심용도(보유 대세주 홀드/익절)엔 보유표 배지가 이상적. (auto_trader decide_exit는 내부적으론 이미 사용.)
- report_improvements 메모리의 과거 pending 중 **남은 것 재확인**: [[report_improvements]] 정독(대부분 완료됐으나 5/10크로스·지수차트 등 완료 표시 갱신 필요).
- `to_do.md`의 **lightweight-charts 전환** — mplfinance 캔들로 대체돼 **사실상 폐기**로 보임(확인 후 to_do.md 정리). worktree `feat/lightweight-charts` 정리 대상.

### 참조 메모리
[[report_improvements]], [[telegram_two_bot_setup]].

---

## 정리/위생 (cross-cutting)
- **워크트리 정리**: `worktree-paper-order-smoke`(커밋 전부 main 반영됨 → 삭제 가능), `.worktrees/feat/lightweight-charts`, `.claude/worktrees/closing-bet-improve`(옛 해시 미머지지만 동일기능 main 재반영됨) — 정리 검토.
- **미커밋/untracked**: `scripts/backtest_60min_stop.py`(60분손절 백테스트 WIP, 1차손절 deferred라 보류), `.claude/`.
- **테스트**: `.venv\Scripts\python.exe -m pytest -q` (140 통과 기준선).

## 다음 세션 권장 순서
1. **서버 git 분기 해소**(최우선 블로커) + 서버 push 근본문제 진단.
2. WS1 서버 배포(모의키 투입 → dry-run → cron --send) → 장중 라이브 검증.
3. WS2/WS3는 각 메모리 정독 후 잔여 항목 정리.
