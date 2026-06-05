# HANDOFF_FINAL — stock_report 단일 최종 인계 (2026-06-05)

## 0b. 2026-06-05 세션 (서학개미 + 미장 리포트 개선 — 전부 배포 완료)
서학개미(한국인) 미국주식 **종목별 순매수**를 예탁결제원 SEIBro 데이터 endpoint 직접호출로 구현(무인증·서버OK). 상세 스펙·코드맵은 메모리 [[seibro_netbuy]].
- 3f4d173: SEIBro 어댑터(`src/datasource/us/seibro_source.py`·`seibro_symbols.py`) + 텔레그램 pre/post "🇰🇷 한국인 매수 TOP5"(개별/ETF 칸 분리, 티커 병기).
- b25d88e: 미장 종목 가격 포맷 — 장전 `종가(전일%)(프리장%)` / 마감 `장마감 종가(%)(애프터장%)`(us_px 매크로 + postmarket 오버레이). 장전 텔레그램 뉴스 제외→웹 최하단.
- 1b67cb0: Top3·ABCD·섹터/테마 대장 카드에 `🇰🇷 한국인 순매수 전일N억(최근5일M억)` 배지(kr_nb 매크로 + `_attach_kr_netbuy_to_picks`, 장전·장후 둘 다).
- 4a9e188/6d07093: **미국장 장중 리포트(us_intraday)** 신설 — 평일 23:50 KST(개장 직후), `src/market_report/us_intraday.py` + `_overlay_intraday`(현재 장중 시세) + `fetch_us_intraday`. 가격=실시간만("장중 $X (장중%)"). **마감 06:30 조기발행**(`_us_morning_job(require_fresh)` + `_us_data_fresh_sync` yfinance ^GSPC 신선도 게이트) + **07:00 안전망**(중복발행 방지: report_path 존재 체크). **ABCD 3개**(`_collect_us_screening(per_group=3)` 장전·마감·장중). **미국 뉴스 전 모드 텔레그램 제외**(웹 최하단만). 신규 모드 us_intraday(ReportMode/publisher us-mid/render title). `--once usmid`.
- ⚠️ 애프터장은 7시 시점 yfinance 시간외 빈값 가능(괄호만 생략), 전일 서학개미는 결제지연/공휴일이면 '전일 —'. 배지는 SEIBro TOP50 권내만. 장중 리포트는 개장 직후라 값 흔들림('잠정' 라벨).
- **E전략(과매도 반등) + Top3 B가중치**(커밋 ab9fe87, 배포완료): E = 최근 주도주(`patterns.oversold_leader`: 최근60봉내 120일신고가 경신) AND 일봉 RSI(14)≤30 AND 4시간봉 RSI≤30(`kr_4h.fetch_4h_rsi_oversold` KR/US). KR=collect_screen_picks(e_out=)+pipeline 4H게이트, US=_collect_us_screening 캐시OHLCV 전체평가+4H게이트 → snap.e_picks(KR ticker/US symbol). '🩹 E 과매도 반등후보' 섹션(텔레그램 KR pre/post, 웹 KR+US; US텔레그램은 overview-only라 웹만). Top3 비포함. top3 _STRAT_W B 2.0→2.8. 234테스트.
  - ⚠️ **미해결 제안(사용자 응답대기)**: 삼성전기 사례 분석 결과 B 눌림목이 Top3에 잘 안 드는 근본원인 = score의 모멘텀항(w_mom×당일등락률)이 '눌림목 조정일(−)'을 페널티함. B종목 모멘텀 페널티 완화 or '최근 조정폭' 보너스 추가 제안함(미적용).
  - 보류: 사용자 "통신 확인" 의미 확인 요청중.

### 0d. 2026-06-05 세션 최후반 (서학개미 KR제외 + B강조 + 마감16:00 + 운영검증)
- **서학개미 한국장 제외**(565426e): 한국장 리포트(장전/장후) 텔레그램에서 '한국인 매수 TOP5' 제거(미국 데이터라 부적절, 사용자). 미국 리포트 종목카드 서학개미 배지는 유지. `_format_kr_us_netbuy`·`_collect_kr_us_netbuy` 호출만 제거(함수는 보존).
- **B 눌림목 이격 강조**(565426e): report.html에서 B종목 20일선 이격 ±5% 이내면 붉은 볼드(US 스크린 line~561, KR 스크린 line~832).
- **마감후 리포트 16:30→16:00**(816c5ed, 내일부터): scheduler `report_post` cron minute 30→0. ⚠️오늘은 16:30 유지(16:00 이미 지나 오늘 발행 보호) 후 16:42 재시작으로 전환.
- **✅ 라이브 검증**: 오늘 16:30 post 정상 발송(telegram_sent 양 chat). 서학개미 제거 확인(post.html grep=0). 🌙NXT 시간외 실데이터 3종목 검출(`nxt_overtime_gainers found=3`) — NXT 첫 라이브 성공. holdings(16:35) 정상.
- ⚠️**운영 함정(겪음)**: 16:42 systemctl restart가 16:40 대시보드 잡을 중단시킴(대시보드 ~2-3분 소요) → `--once dashboard` 재실행으로 복구(16:47 published picks=46). **교훈: 16:40 대시보드 잡 실행 중(16:40~16:43) 재시작 금지**.
- **#250a KB금융 다중매칭**(해결): 사용자 "그냥 다 표시" → 기존 동작 그대로(Top3 reason은 _strats로 매칭전략 다 표시, 스크린은 그룹별 다 등장). 코드 변경 없음.
- **#245 B 모멘텀 페널티 완화**(해결, 커밋 7679621): top3에서 B & 당일등락<0 & 60일고점대비 낙폭 ≤ `B_PULLBACK_MAX_DD`(25%)면 모멘텀 페널티 면제. 깊은 낙폭(추세 꺾임 의심)은 페널티 유지. ⚠️사용자 우려(추세깨진 종목 추천?) 검증: LG전자(066570) B매칭이나 고점대비 -30.8%·당일-7.6% → 낙폭31%>25% 면제제외(과열방지 OK), 삼성전기 낙폭~22% 면제. 임계값 25% 조정가능. 235테스트.
- **#244 "통신 확인"**: 답 늦어서 한 핑(액션 없음).
- **#258 종가베팅 후보 ABCD 라벨**(커밋 2223cff): `pipeline._inject_candidate_strategies`가 각 candidate_pick(AI선정) 일봉을 스크리너 재평가→`strategies` 라벨. report.html 종가베팅 후보 섹션에 'B/C 시그널'/'ABCD 미해당(AI선정)' 배지. **확인결과: Top3=ABCD 반영O(select_top3), 종가베팅후보=AI선정(ABCD 게이트X)→라벨로 투명화**. (원하면 후보도 ABCD필터 가능, 미적용)
- **#259 B 급반전 제외**(커밋 c3cb2d7): `patterns.gave_back_recent_gain`(최근3일 하락 ≥ 최근10일 상승분×0.5) → `collect_screen_picks`에서 B픽 skip. 검증: 삼성에스디에스(018260) +120%→3일내 -30%(56%반납) 제외O, 삼성전기 유지. 임계값(0.5/3일/10일) 조정가능. ⚠️KR만 적용(US B는 미적용).
- **#261 B 고점대비 낙폭 표시**(커밋 cd55e11): B 시그널 설명란(Top3·스크린·US 포함)에 '· 고점대비 -X%'(60일 고점 대비). strategy_section/top3/_to_dict에 high_dd 필드. 237테스트.
- 237 테스트. **운영교훈 재확인: 평일 16:40~16:43(대시보드)·기타 잡 실행 중 서버 재시작 금지.**

### 0e. 2026-06-05 세션 (미국 리포트 개선 + 백테스트)
- **미국 리포트 개선 4건**(커밋 1143e45): (1) report.html 색상 한국식(up-text=빨강/down-text=파랑·index-card; 캔들차트는 원래 up=red). (2) `us_premarket._build_premarket_top` + `snap.us_premarket_top` + report.html '🚀 프리장 급등 TOP5'(ABCD필터 통과 중 프리장상승률 상위5, 섹터·전략). (3) 미장 전/후 웹에 why_moved('💡왜올랐나/떨어졌나') 표시(텔레그램엔 원래 있었음). (4) `names_db.ensure_names(name_map)`: 네이버 실패 영문명 → Gemini 음역 → us_names_ko.json 캐시(다음번 한국어). 237테스트.
- **#258 종가베팅 ABCD라벨·#259 B급반전제외·#261 B고점대비낙폭** 이전 배포(2223cff/c3cb2d7/cd55e11).
- **백테스트(코스피시총30, 5/4~5/29→6/5, MA손절청산, scripts 임시 /tmp/bt30.py)**: 평균 +14.86% 승률85%(현재 C우위 가중치). **핵심발견: C↓A·B↑ 재가중해도 결과 거의 동일(+14.86→13.99%)** — 대형주에선 A(수렴초입)가 거의 안 떠서(52건중 A 0~1, C 33) 가중치만으론 A/B 비중 못 올림. 수익은 C(추세추종) 주도. → A/B 늘리려면 '급등초입 신호 신설' or '중소형주 유니버스' 필요(가중치만 X). #268('돈못벌었나')은 집계상 +14.86%로 반증, LG CNS·현대무벡스는 늦은 개별케이스.
- **미해결(사용자 응답대기)**: #271 방향(급등초입신호 신설 a / 중소형주 유니버스 b / 유지 c). 프리장TOP5 텔레그램에도 넣을지. 백테스트 상세(보류). #277 검색창=웹정적이라 미도입, /analyze 봇커맨드로 대체 안내함.
- 미국 리포트 스케줄: 장전 19:00 / 장중 23:50 / 마감 06:30(게이트)+07:00(안전망), 전부 ABCD 3개.

### 0c. 2026-06-05 세션 후반 (과열 추천수정 + 보유 KIS연동 + NXT조사)
- **과열 추천 수정**(커밋 652cd87, origin/main 최신): 삼성화재·신세계가 4H BB상단 돌파 과열인데 추천돼 손실 → 수정. (1) 일봉 과열=BB(20,2)상단 종가돌파 단독(기존 이격30%·거래량1.8배 AND게이트 제거→보조). (2) `src/datasource/kr_4h.py` 신규: yfinance 1h→4h 리샘플, 4H과열=(종가>BB상단 돌파)OR(상단 음봉거부). ※실측: 삼성화재·신세계 마지막4H가 'BB상단 돌파 양봉'이라 음봉만으론 못 걸러서 돌파도 OR포함. (3) top3 점수 강등(overheat weight 5.0, 제외 아님)+🔥표시. pipeline에서 Top3후보 거래대금상위12만 4H조회. 226테스트. 서버 배포·검증완료(삼성화재/신세계 과열 True).
- **보유종목 KIS 연동**(사용자 요청): `config/holdings.yaml`(gitignore) 수동3종목(현대모비스·삼성에스디에스·현대무벡스) 제거→`holdings: []`. 로컬+서버 둘 다. 이제 보유=KIS 계좌잔고만(collect_holdings_status는 원래 KIS우선·빈경우만 yaml폴백→이제 폴백도 빔). 16:35 보유리포트는 원래 KIS-only.
- **NXT 시간외 상위상승률**(커밋 c6b8e07, 배포완료): 마감후(post_close) 리포트에 '🌙 시간외(NXT) 상위 상승률'. `adapter.get_nxt_overtime_gainers`: KIS `/ranking/fluctuation` `fid_cond_mrkt_div_code=NX`(넥스트레이드, 실측 지원·UN은 ERR)로 후보 수집 → 각 종목 정규장 종가(J 현재가) 대비 NXT 현재가 변동률 직접계산 → 시간외 상승분(양수)만 top7. pipeline post_close만 수집, telegram+report.html 섹션. 229테스트. ⚠️ **세만틱 검증 미완**: 16:30 첫 라이브 발행에서 실제 시간외 데이터로 확인 필요(장중엔 J현재가=실시간이라 NXT-KRX 스프레드만 보임, 16:30엔 J현재가=종가라 진짜 시간외 상승분). 시간외 거래량 적으면 비어보일 수 있음→그때 기준 조정 검토.

---


> **이 문서가 유일한 최신 인계본이다.** 세션 재시작 시 가장 먼저 읽을 것.
> 이전 핸드오프(06-02·06-03·06-04 멀티세션)는 전부 이 문서로 대체됨.
> 충돌 핸드오프를 만들지 말고, 다음 세션도 이 파일을 갱신해 단일성을 유지할 것.

---

## 0. 지금 상태 (한눈에)

- **origin/main 최신 커밋**: `1143e45`(미국 리포트 개선 4건) + 서버 자동리포트 커밋. 2026-06-05 세션 전부 배포 완료.
- **서버(`lotto-server` = 134.185.109.195) = origin/main과 동기화 + 서비스 재시작 완료.** (로컬수정 `config/screener.yaml` RAM축소판만 autostash 보존)
- **테스트**: `.venv\Scripts\python.exe -m pytest tests/ -q` → **237 passed** (기준선).
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
