# HANDOFF_FINAL — stock_report 단일 최종 인계 (2026-06-05)

## 0b. 2026-06-05 세션 (서학개미 + 미장 리포트 개선 — 전부 배포 완료)
서학개미(한국인) 미국주식 **종목별 순매수**를 예탁결제원 SEIBro 데이터 endpoint 직접호출로 구현(무인증·서버OK). 상세 스펙·코드맵은 메모리 [[seibro_netbuy]].
- 3f4d173: SEIBro 어댑터(`src/datasource/us/seibro_source.py`·`seibro_symbols.py`) + 텔레그램 pre/post "🇰🇷 한국인 매수 TOP5"(개별/ETF 칸 분리, 티커 병기).
- b25d88e: 미장 종목 가격 포맷 — 장전 `종가(전일%)(프리장%)` / 마감 `장마감 종가(%)(애프터장%)`(us_px 매크로 + postmarket 오버레이). 장전 텔레그램 뉴스 제외→웹 최하단.
- 1b67cb0: Top3·ABCD·섹터/테마 대장 카드에 `🇰🇷 한국인 순매수 전일N억(최근5일M억)` 배지(kr_nb 매크로 + `_attach_kr_netbuy_to_picks`, 장전·장후 둘 다).
- ⚠️ 애프터장은 7시 시점 yfinance 시간외 빈값 가능(괄호만 생략), 전일 서학개미는 결제지연/공휴일이면 '전일 —'. 배지는 SEIBro TOP50 권내만.

---


> **이 문서가 유일한 최신 인계본이다.** 세션 재시작 시 가장 먼저 읽을 것.
> 이전 핸드오프(06-02·06-03·06-04 멀티세션)는 전부 이 문서로 대체됨.
> 충돌 핸드오프를 만들지 말고, 다음 세션도 이 파일을 갱신해 단일성을 유지할 것.

---

## 0. 지금 상태 (한눈에)

- **origin/main 최신 커밋**: `1b67cb0` (서학개미 픽별 배지). 2026-06-05 세션 작업 전부 푸시·배포 완료.
- **서버(`lotto-server` = 134.185.109.195) = origin/main(1b67cb0)과 동기화 + 서비스 재시작 완료.** (로컬수정 `config/screener.yaml` RAM축소판만 autostash 보존)
- **테스트**: `.venv\Scripts\python.exe -m pytest tests/ -q` → **216 passed** (기준선).
- **3개 스트림(자동매매·미국스크리닝·리포트) 전부 main 머지 완료.** 백업 브랜치 `backup/pre-merge-2026-06-03`.
- **SSH**: `ssh lotto-server` (별칭, 키인증 OK). 무암호 sudo 가능(systemctl restart 가능).

---

## 1. 2026-06-04 세션에서 끝낸 일 (✅ 전부 완료·푸시·검증)

1. **정리**(cb98a67): `.claude/` gitignore, 폐기 브랜치 `feat/lightweight-charts` 삭제, `scripts/backtest_60min_stop.py` 보존.
2. **자동매매 코드 재점검**: `src/trading/` 6모듈 + kis_order 건전성 확인(코드변경 없음). ⚠️ 라이브 `--send` 미검증은 그대로.
3. **cross_signal 배지 → 보유종목**(a2bacaf): 보유종목표·텔레그램에 🟢단기눌림/⚠️조정시작 표시.
4. **보유종목 AI요약 + AI폴백**(6af9796): `summarize_holdings`(snap.holdings_summary), AI 실패 시 결정론 폴백(`_fallback_summary`), 텔레그램 마감후에 왜움직였나·내일관전포인트 추가.
   - ※ "마감후 AI요약 누락"은 post 전용버그가 아니라 **간헐적 Gemini 실패**였음(폴백으로 해결).
5. **us_morning 미국 종목 전용**(aa359e8·3b5b4a9): 종목/Top3/강세테마를 미국주식만(S&P500 A/B/C/D 재사용). 한국장 시사점 코멘트는 유지. 미국 reason 거래대금 '억' 오표기 회피.
6. **서버 git 분기 해결 + 근본수정**(c372175):
   - 서버 ahead4/behind52 → `reset --hard origin/main`(폐기=자동리포트4커밋, 무해) + screener.yaml 보존(autostash).
   - **근본원인 = publisher가 push 전 pull/rebase 안 함 → non-ff 거부로 누적** (PAT는 정상, 쓰기권한 검증됨).
   - publisher에 `pull --rebase --autostash origin main` 추가 → 서버 배포 + `systemctl restart stock-report.service` 완료.

상세 이력: 메모리 [[report_improvements]] [[us_screening]] [[paper_auto_trader]] [[server_deployment]].

---

## 2. 남은 작업 (다음 세션) — 우선순위 + 명확한 요구사항

### 🔴 P1. 자동매매 서버 배포 (코드 완료, 배포·검증만 남음)
**요구사항**: 모의(paper) 자동매매를 서버 cron으로 가동하고 장중 첫 라이브 검증.
**막는 것**: 서버 `.env`에 모의 키가 **비어있음**(사용자 시크릿 — 직접 입력 필요).
**정확한 절차**:
1. (사용자) 서버 `/home/ubuntu/stock_report/.env`에 모의 키 3개 입력:
   `KIS_PAPER_APP_KEY=...`, `KIS_PAPER_APP_SECRET=...`, `KIS_PAPER_ACCOUNT_NO=...` (`KIS_ENV=real` 유지).
2. **장중 1회 수동 dry-run 검증**(SSH): `cd ~/stock_report && venv/bin/python -m src.trading.auto_trader buy`
   (→ `--send` 없이 dry-run. 매수가능조회·수량계산 로그 확인. rt_cd/TR 파라미터 empirical 확인).
3. 정상 확인 후 cron 등록(평일):
   - **15:20 buy**: `venv/bin/python -m src.trading.auto_trader buy --send`
   - **15:50 sell**: `venv/bin/python -m src.trading.auto_trader sell --send`
   - ⚠️ 전제: pre 리포트(14:50)가 `data/top3_<date>_pre.json` 생성해야 buy 동작(브리지).
4. 첫 `--send` 매수일에 KIS 모의계좌에서 체결·odno 확인.
**⚠️ 사용자 결정 필요(코딩 전 협의)**: 손절 전략 확정(① 일봉 20/60MA[현재 구현] vs ② 60분 20MA 1차손절[`scripts/backtest_60min_stop.py`로 백테스트 가능, KIS 분봉 제약→yfinance 우회 필요] vs ③ ATR). 동시보유 상한·익절(목표가) 추가 여부.

### 🟠 P2. 미국 스크리닝 잔버그
- **심볼 정규화**: FDR `BRKB`/`BFB` → yfinance `BRK-B`/`BF-B` (2종목 OHLCV 실패). us_morning 라이브에 미국 스크리닝이 들어왔으니 우선순위 ↑. 양방향 매핑(요청·결과 키) 주의.
- (선택) us_morning 유니버스를 S&P500 only → combined(S&P500 ∪ 나스닥 핫)로 확장 검토. 단 yfinance rate limit·07:30 지연 고려. 현재는 best-effort(실패 시 지수·섹터만 발송).

### 🟡 P3. 리포트 선택 개선
- (선택) 수급 3일치 즉시화: `.env`에 `KRX_ID`/`KRX_PW` + pykrx 백필(현재 거래일마다 누적).
- (선택) 5·10 크로스 임계값(7%/15%) 사용자 7케이스 검증 후 조정.
- (선택) VOLUME 스크래퍼 `<tr>` 단위 파싱으로 커버리지 개선.

---

## 3. 오늘(06-04) 자동 검증 포인트
- 06-04는 정상 거래일 → 서비스가 **14:50 pre / 16:30 post** 자동 발행 예정.
- **publisher 수정의 실전 검증**: 이때 `git pull --rebase --autostash` → push가 성공하는지 확인(로그 `published` / `publish_push_failed` 없음). 분기 재발 없으면 근본수정 성공.
- 확인법: `ssh lotto-server "cd ~/stock_report && git status -sb && git log --oneline -3"` → ahead/behind 0 유지면 OK.

---

## 4. 운영 규칙·함정 (필독)
- **단일 main 공유**: 개발 머신 + 서버 자동발행이 같은 main을 씀. 이제 publisher가 pull-rebase하므로 분기 안 쌓임. 단 개발 중 서버 발행 시각(14:50/16:30) 겹치면 잠깐 경합 가능.
- **서버 screener.yaml**: RAM 1GB 대응 축소판(kospi100/kosdaq50/min1000억) + 3000억 필터 제거 = **미커밋 로컬수정**으로 유지. autostash가 자동 보존. (origin 버전 채택 여부는 사용자 판단 보류 항목.)
- **테스트**: `.venv\Scripts\python.exe -m pytest tests/ -q` (150 기준).
- **커밋 메시지**: Bash 툴에서 PowerShell here-string(`@'..'@`) 금지(누출). `git commit -F 파일` 또는 Bash `<<'EOF'` 사용.
- **콘솔 인코딩**: Windows cp949 — 한글 검증결과는 utf-8 파일로 써서 Read.
- **editable install**(`pip install -e .`)이 main repo/src 참조. main repo 직접작업 시 무관.
- **KIS 엔드포인트/TR**: 공식 문서 검증 없이 하드코딩 금지.

## 5. 핵심 파일 맵
- `src/market_report/pipeline.py` — run_full, generate_report, `_collect_us_screening`, 휴장스킵
- `src/market_report/analyzer.py` — analyze(+`_fallback_summary`), summarize_stocks, summarize_holdings
- `src/market_report/publisher.py` — publish(add→commit→**pull --rebase --autostash**→push)
- `src/market_report/telegram_notify.py` — pre/post/us_morning 메시지
- `src/alerts/holdings_report.py` — diagnose_holdings(+cross_signal), cross_badge
- `src/screener/us_pipeline.py`·`us_report.py` — 미국 A/B/C/D
- `src/trading/` — 자동매매 v1(auto_trader/kis_order/ma_exit/positions/sizing/top3_bridge)
- `config/screener.yaml`(개발) / 서버는 축소판 로컬수정 · `config/screener_us.yaml`(미국)
- `src/market_report/templates/report.html` — 전체 리포트(미국 섹션 포함)
- `scripts/backtest_60min_stop.py` — 자동매매 1차손절 백테스트(deferred)
