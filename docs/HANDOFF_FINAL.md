# HANDOFF_FINAL — stock_report 단일 최종 인계 (2026-06-07)

## 0a. 2026-06-07 세션 (P1 자동매매 — 전략별 손절 + 배포 진행중)
- **ABCDE 전략별 손절 반영**(커밋 8c1f906, 서버 pull 완료): 사용자 결정 = ①전략별 최신 손절(screener.yaml) ②단계청산 유지 ③다중매칭 넓은쪽 우선 ④CORRECTION 선제50% 유지 ⑤v1 사이징 그대로(Top3×100만, 익절X).
  - `ma_exit.decide_exit(closes, strategies=)`: **tight**(A/B만 매칭)=20MA 2일이탈 **전량** / **wide**(C/D 포함 or 전략정보 없음 폴백)=기존 20MA 50%→60MA 전량. PULLBACK 보호·CORRECTION 선제50%는 양쪽 유지.
  - 배선: top3_bridge가 picks `strategies`(["A","C"]) 보존 → positions DB `strategy` 컬럼(CSV, 구스키마 ALTER 마이그레이션) → auto_trader 매수 시 저장·매도 시 적용.
  - **D 손절 모순 해소**: yaml 주석(구름하단/20일선, 05-31)은 구버전 — opinion(60일선 2일이탈, 06-01 b1a1d3c)이 최신. 주석 정정함. E·급등초입은 Top3 비포함이라 자동매매 무관.
  - 259 테스트(254+5). **새 기준선 259**.
- **P1 남은 절차**: ①사용자 서버 `.env` `KIS_PAPER_*` 3키 입력(06-07 현재 미입력) → ②월요일(06-09) 장중 dry-run `venv/bin/python -m src.trading.auto_trader buy` → ③정상 시 cron 등록(평일 15:20 buy --send / 15:50 sell --send) → ④첫 체결 KIS 모의계좌 확인.

### ✅ 코인 시세 리포팅 — 구현·배포 완료(06-07 심야, 커밋 aefe79d+9db9668)
- **매일 17:00 KST 주말 포함**(scheduler `report_coin`, day_of_week 미지정='*'). `--once coin`. 라이브 발송 2회 검증(텔레그램 2챗+웹 발행).
- **구성**: `src/datasource/coin/sources.py`(업비트 ticker/일봉200/4H봉120 + CoinGecko markets/global + F&G alternative.me, 순수파서+§7안전+F&G일캐시) + `src/market_report/coin_report.py`(김프·이격·국면·전략·포맷·러너) + `publisher.publish_docs()`(신규, 기존 publish 불변). 웹=docs/reports/<date>-coin.html(주식 index 미통합 v1). AI요약 없음. 환율=기존 `fdr_source.fetch_usd_krw` 재사용.
- **분석(사용자 추가요청)**: 코인별 일봉 이격(20/60)+RSI+국면 신호등(coin_phase — 주식 골격, **코인 과열임계 120≥30/60≥20 별도**) + 4H RSI/20MA이격 + **ABCDE 전략 평가**(주식 스크리너 엔진 무수정 재사용, E=oversold_leader+4H RSI≤30+시장게이트 코인F&G≤25=🔥시장동반바닥).
- ⚠️ 함정: 업비트 캔들 응답 **최신순**(파서가 reverse), 캔들 volume **float 유지**(코인 소수 거래량, int 캐스팅 시 E 거래량조건 왜곡).
- **자동매매 알림 보강**(같은 커밋): 매수/매도 예외 → ⚠️텔레그램 알림+다음 종목 계속, top3 JSON 없음 중단도 알림, 매도 잡 끝 📋포지션 현황(전략·진입가·평가손익·HOLD 포함). dry-run은 여전히 무알림.
- 테스트 기준선 **278**.
- ⚠️ 운영주의: 생성된 coin.html을 로컬 dry-run으로 재생성한 채 push하면 서버 발행본과 autostash 충돌 가능(06-07 겪음, origin본 채택으로 해소). 리포트 산출물은 커밋 전 충돌 시 origin 채택.

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
- **급등 초입 신호 추가**(커밋 5337689 패턴 + 844c0c3 배선): `patterns.is_surge_start`(20일 신고가 돌파+거래량 2배+당일 +4%+20선이격≤22%). ⚠️핵심: KOSPI 5월 +23.7% vs 현재 Top3 +16.44%(지각 진입)라 사용자 불만 → 급등초입 백테스트(코스피30,5월) 단독 +23.84%(승률71%,7건)로 지수·Top3 둘 다 이김. 단 Top3에 섞으면 희석(+15.73%) → '🚀 급등 초입' **별도 섹션**(Top3 비포함, E와 동일방식). collect_screen_picks(surge_out=)·_collect_us_screening·telegram pre/post·report.html·snap.surge_picks. 239테스트. ⚠️소표본·상승장 백테스트(하락장 미검증, 면책표기).
- **미해결(사용자 응답대기)**: 프리장TOP5 텔레그램에도 넣을지. 백테스트 상세(보류). 급등초입 조건 실데이터 튜닝(거래량배수·당일강세·이격상한)·하락장 검증. #277 검색창=웹정적이라 미도입, /analyze 봇커맨드로 대체 안내함.
- **전략 스크린 중복제거**(커밋 a508ebd): 종목당 1줄·종합점수순·매칭전략 묶어표기(KB금융 A·C·D). select_top3(return_all=True)+screen_ranked. ⚠️_strats 누적유실 버그수정(대표픽 교체 시 set 리셋→승계). 미장장전 21:50 2차(5ed3d2b). 240테스트.
- **색상 최종**(커밋 05e522d): 등락률 퍼센트 텍스트=상승 초록(--green)/하락 빨강(--red). 캔들차트 봉은 상승=빨강/하락=파랑 그대로(사용자 확정, 의도적 혼용). report.html .up-text/.down-text/index-card.
- **버그수정 us_intraday 코스피요약**(커밋 9e4e342): 장중 모드가 analyzer else(KR post_close 프롬프트)+_market_context KR분기로 빠져 미국 장중 리포트에 코스피 요약 들어감 → 미국 컨텍스트+_us_morning_prompt 라우팅. 240테스트. 교훈: 새 mode 추가 시 analyzer 2곳(_market_context·analyze 프롬프트선택) 반드시 포함.
- 미국 리포트 스케줄: 장전 19:00+21:50(2차) / 장중 개장직후(섬머22:40/일반23:40 DST) / 마감 06:30(게이트)+07:00(안전망), 전부 ABCD 3개.
- **E/급등초입 보조정보**(커밋 f44b95e, #305~307): 두 섹션에 시총·거래량(만주)·거래대금·테마·서학개미(US만) 추가. KR=collect_screen_picks(volume·trade_value)+_inject_marcap(marcap·turnover)+테마fill 확장. US=_collect_us_screening(_extra: marcap/turnover/volume/theme)+_attach_kr_netbuy 풀 확장. 표시=telegram _pick_detail_line + report.html sec_detail 매크로.
- **미국 종목별 AI버튼**(커밋 2a25bda, #309): analyzer.summarize_us_stocks(symbol 배치 '왜 움직였나' 한국어) → us_report_runner+run_full us_morning에서 analyze 직후 호출. report.html us_top3·스크린·섹터/테마대장·E·급등초입에 🤖버튼. KR summarize_stocks도 E/급등초입 포함 확장. 245테스트.
- **SOXL 대장서머리 추가**(커밋 073f810): fdr_source.US_BIGTECH에 SOXL(반도체 3X). FDR 조회 검증.
- **숫자 콤마**(커밋 9cd3827, #315): 표시코드(template/telegram/messages)는 이미 천단위 콤마. AI 생성텍스트만 갭 → summarize_stocks/us_stocks 프롬프트에 '천단위 콤마' 지시 추가.
- **E 투매바닥(capitulation) 재설계 + 지수 2단계등급**(커밋 2b9a9e5, #328/#330/#334~339): 사용자가 찾던 '진짜 바닥' 신호. ▶oversold_leader 재설계: 최근 look(3)일내 RSI≤30 + 50일선이격≤-12% + 거래량≥2x(투매) + 당일 반등 양봉(칼날 회피). 기존 '주도주 신고가' 조건 폐기(4월 바닥 놓친 원인). ▶2단계 등급(하드게이트 X, 사용자 #334 보조도구 의견 반영): _market_rsi(US=나스닥IXIC·KR=코스피KS11, **절대 교차 안 함** #339) + _tag_market_bottom → 지수 RSI<35면 market_bottom=True '🔥시장 동반 바닥'(강), 아니면 '개별 바닥(시장 양호)'. ▶배지=telegram/report.html. ▶백테스트(2025): 4월 진짜바닥 7회 포착(시장동반 4회=+14~40%대박, 시장회복후 추격 3회=손실=개별등급으로 강등됨), 3월 가짜·INTC/PLTR/SMR 0회(노이즈 적음). 248테스트.
- **대장주 섹션 + 전략/E바닥 배지**(커밋 76d9b50, #345 / #312 잠재버그 수정): us_bigtech가 그동안 렌더 안 돼 SOXL(#312) 안 보였음 → "🇺🇸 대장주(빅테크·주요ETF)" 섹션 신설. _tag_bigtech_strategies(캐시OHLCV로 A/B/C/D/E/급등초입 평가)+E바닥 시장동반등급. report.html 전략시그널·🔥시장동반/🩹투매바닥 배지. 249테스트.
- **KR·US 전략 조건 표시 통일(#414, 24c84d9)**: 공용 Jinja 매크로 cond_badges(t)로 KR·US 둘 다 20일선 이격·🔥과열(BB돌파·거래량)·5<10 크로스·끝물 표시(한국 스타일로 통일). _to_dict(US픽)에 overheat/vol_x/endstage 추가(계산만, 픽/순위 불변). ⚠️KR 출력 byte-identical 검증(스냅샷 diff 0) — '결과 절대 안바뀜' 준수. 매크로 |default 방어. 마감전 14:50/마감후 16:00(#412). 253테스트.
- **팝업·순서·과열게이트 묶음(#452~459, 806d545·2453c67·0c49356·04e681e)**: ①과열(BB)은 C전략만 표시·문구 '단기과열주의'(A전략 정상돌파 오인 버그수정, 3곳). ②AI요약 수급 아래로(#453, #447 번복). ③US 스크리닝 ABCD그룹→종합점수순(us_screen_ranked, KR과 동일). ④E전략 KR/US 동일 확인(oversold_leader 기본값+4H게이트+지수RSI/F&G, 미장전용 없음). ⑤전략배지 클릭→설명팝업(strat_badge+showStrat+STRAT_INFO, KR·US). ⑥조건플래그(과열/끝물/눌림/조정/신호등) 클릭→로직설명팝업(showFlag+FLAG_INFO). KR순서 최종: 지수→수급→AI요약→관전포인트→테마→종목(웹·텔레그램 동일). 254테스트.
- **순서·텔레그램·자금흐름 보강(#441~450, feb4af3·a95c459)**: ①자금흐름 미매핑 종목 AI 티커·한국어명 보강+ISIN캐시(seibro_enrich, Gemini, data/seibro_enrich.json). ②KR 웹·텔레그램 순서 통일: 지수→AI요약→수급→내일관전포인트→테마→종목(AI를 지수 바로 아래). ③텔레그램 강세/주도 테마 통합(🚀주도). ④US 텔레그램 지수 줄별 신호등 병기+금/WTI 줄바꿈. 웹 블록이동은 문자열앵커+if/endif·for 균형검증. 254테스트.
- **웹 리포트 대정리 묶음(#419~436, 커밋 05b07ca·91f81a1·3d8c948·213450a·64b9d8e·6a7da2e)**: ①바닥 3단계 신호등(🔵바닥권<🔵🔵강한바닥(주봉RSI≤31/주봉CCI≤-200)<🔵🔵🔵역대급대바닥(월봉RSI≤31), 코스닥 주봉제외; _index_ma_gaps에 rsi_w/rsi_m/cci/cci_w 800일). ②텔레그램 신호등 지수옆 병기·이격숫자 제거(웹만). ③추천/대장픽 한국인 순매도TOP50 표시(#431). ④US 대장주 제거→섹터대장 일원화(섹터>종목)+1주일 상승률, ETF NASA/DRAM 매핑(#429/433/436). ⑤US AI요약 시장종합 강화(프롬프트+텔레그램 theme_commentary, #435). ⑥KR 테마 통합(주도 O/X 배지). ⑦KR 섹션 재배치(시장전반→종목, #428). KR 출력 byte검증 도구로 안전이동. 254테스트.
- **한국장 프리(08:05)·장초(09:15) 리포트(#404, 커밋 e9733cd)**: NXT 08:00 개장 활용. kr_morning.run_kr_morning(mode) 공용 러너. 프리=NXT 프리장 상승률+전일 종가베팅·Top3 시초등락+AI분위기, 장초=정규장 시초 상승률+동일. 종가베팅 영속화 top3_bridge.persist_candidates(pre_close 14:50)+top3_status.find_prev_candidates. ReportMode +kr_premarket/+kr_open, render/publisher suffix(kr-pre/kr-open)·title, telegram _format_kr_morning_summary, report.html 전일종가베팅·시초상승률 섹션. 스케줄 평일 08:05/09:15(13잡 확인). ⚠️Top3 시초는 월요일부터(기존 top3파일), 종가베팅은 화요일부터(월14:50 첫 저장). NXT 모닝 라이브 평일검증 예정. 253테스트.
- **자금흐름/신호등 보강 묶음(#392/#394/#396/#397/#398/#393, 커밋 2b3db03·0be5668·505e547)**: 바닥권 아이콘 🔵(#397). 거래량 연속↑ 정보표식(#392). SEIBro엔 티커 없음→seibro_symbols ETF/종목 ISIN 14개 확정매핑+names_ko 한국어명(#394a/b). 자금흐름 행 네이버링크(#396). 뉴스 헤드라인 한국어 번역 translate_us_news(1배치 Gemini, #394c). 한국인 순매수 '일평균 중심(전주대비%)' 표시(#398). 🏦 기관·외인 연속 순매수/매도 Top — collect_supply_streaks(시총상위40 FDR+get_stock_investor_daily, post_close, #393). 250테스트.
- **한국인 순매수 총액(#377, 4f1da4d)**: 미국리포트에 SEIBro TOP50 순매수 5일합 + 전주 일평균 대비%(코스피→나스닥 자금이동 추산). _collect_kr_us_netbuy에서 이번주/전주(lookback_range end-shift) 합산 → snap.kr_us_netbuy_total. 웹 자금흐름 헤더 + telegram US요약.
- **🔼 상승전환 신호등 상태(#379, 4b15bb3)**: 눌림/조정→5일선 음→양 회복+20일선 위=상승전환(진짜). 백테스트 미국 69%(가짜46%, 20일선회복이 가름). _index_ma_gaps g5_prev + _market_phase. 신호등 7단계(🩹바닥/🟢정상/🟡눌림/🔼상승전환/🟠조정/🔴과열/🔻하락전환). 250테스트.
- **시장 국면 신호등 + 고점/바닥 신호 검증**(커밋 9acc3db, #360~376): 리포트 헤더에 🚦지수 상태 신호등(코스피·코스닥·나스닥·S&P). _market_phase: 🩹바닥권(RSI≤30 OR 60일이격≤-7%, **검증된 실전신호**) > 🔴과열(이격임계+RSI≥70, **정보용**·타이밍 신뢰낮음) > 🔻하락전환(60일<0) > 🟠조정(20일<0) > 🟡눌림(5일<0) > 🟢정상. _index_ma_gaps에 rsi 포함. ⚠️**핵심 백테스트 결론(2024+)**: 고점은 단일지표 신뢰낮음(이격과열 나스닥8%/S&P0%/코스피50%, MACD데드크로스 14~48%, RSI다이버전스 0~29% — 대부분 거짓, 강세장이라 계속 상승). 바닥은 잘 잡힘(RSI≤30·60일이격≤-7% → 20일후 +5~10%·승률60~100%). → 비대칭 설계. MACD크로스는 후행·약해 트리거 제외. 2주 이력 검증: 과열→눌림→조정 전환 자연스러움. 사용자 "가짜 많으면 제거" 합의. 250테스트.
- **지수 이평선 이격도**(커밋 aec0b37, #357): 텔레그램에 지수 5/10/20/60/120일선 이격% 매일 표시(고점 판단). _index_ma_gaps(FDR)+snap.ma_gaps(US=나스닥/S&P·KR=코스피/코스닥)+telegram _format_ma_gaps. 연구(2024+ 10%+조정 직전 고점 이격): 나스닥 120일+12.7%/60일+8.6%, 코스피 120일 최대+50%(26/2 과열). ⚠️현재 코스피 120일+42% 과열경계. ⏭️미완 제안(#360): 시장 국면 자동라벨(🟢정상/🟡눌림/🔴과열/🔻하락전환) — 사용자 응답대기. 임계 초안: 과열=120일이격(나스닥+12·코스피+40), 하락전환=60일이격<0+역배열. 웹표시는 추후.
- **공포탐욕지수(F&G) 결합**(커밋 81cd7cd, #331 Phase2 완료): `src/datasource/us/fear_greed.py`(CNN production.dataviz.cnn.io/index/fearandgreed/graphdata, User-Agent 필요·일1회캐시·§7). 백테스트(2025): F&G≤25 매수 나스닥 20일+4%, ≤15 +9%(승률87%), 최저(4/8 score3)→20일+16%/60일+34% — extreme fear=바닥권 확인. E market_bottom = 지수RSI<35 **OR** F&G≤25. snap.fear_greed(US collect_us_snapshot+KR 파이프라인). 표시: report.html 헤더 공포탐욕 배지+E배지+telegram F&G라인. 249테스트. 라이브검증 F&G=42(fear).
- **한국인 자금흐름(US)**(커밋 94e67f9+f3d0898, #318): 미국 리포트에 '🇰🇷 한국인 자금흐름' = 순매수 TOP5(개별/ETF) + 순매도 TOP3(자금유출). ⚠️핵심 실측발견: SEIBro D_TYPE=4(순매수)는 상위 매수자만(전부 양수)이라 net-sell 없음 → **D_TYPE=2(매도결제금액 상위)에 net_buy 음수(순매도) 종목 포함** 확인 → fetch_us_net_sell(매도상위 중 net<0, 가장 음수순 top3). seibro _BODY_TMPL d_type 파라미터화. **_collect_kr_us_netbuy 기존 미배선**이던 것 us_report_runner+us_morning에 배선(매수TOP5도 이제 US 표시). report.html US전용 섹션. ⚠️교훈: 백그라운드 체인 명령이 테스트 실패에도 무조건 배포함(2 fail 후 배포됨) — 테스트 게이트 필요. fake_fetch 목 d_type 인자 누락이 원인(런타임은 정상), f3d0898로 수정 247통과.
- **한국장 AI 수급 요약**(커밋 2e2e9b8, #313/#316): 수급칸 아래 '🔎 수급 요약'. flows_history.load_flows_series(저장 30일중 10일)+compute_flow_stats(시장·투자자별 streak 부호포함·전일·전주(5일전)·5일합, 순수). analyzer.summarize_flows(결정론 팩트→AI narrate, 연속·전일/전주대비 필수, 실패시 폴백). pipeline KR pre/post. telegram _format_flows_summary+report.html summary-box. 247테스트. ⚠️연속/전주대비는 market_flows.json 누적분만큼(거래일 지날수록 풍부, 현재 로컬은 sparse·서버는 누적됨).

### 0f. 2026-06-06 세션 (리팩토링 — 리포트 출력 불변)
사용자(/goal): 자는 동안 리포트 구조·내용·결과 **절대 불변** 전제로 한국/미국 중복코드 공통화. 백업+골든검증.
- **백업**: 브랜치 backup/pre-refactor-2026-06-06 + 태그 backup-refactor-2026-06-06.
- **통합1 US 러너**(커밋 6b1c0fe): us_premarket/us_intraday가 overlay·extra_steps만 다르고 동일 → us_report_runner.run_us_report로 통합. 두 모듈은 얇은 래퍼(_build_premarket_top은 장전 특화 유지). 호출순서·예외·로그라벨 동일 재현. test_us_runner.py 와이어링 검증.
- **통합2 오버레이**(커밋 f5945ee): _overlay_premarket/_overlay_intraday → _overlay_live_quote(fetch·플래그키만 파라미터). _overlay_postmarket은 로직 달라 별도 유지. test_overlay_intraday_shares_logic 추가.
- **검증**: 244테스트 + 전 모듈 import + 6개 모드 render 스모크 + 동작불변(by construction). 골든=기존 render/format 테스트(test_us_intraday·us_morning_report)가 출력 고정.
- **안 건드린 것(의도적)**: analyzer 프롬프트(=AI출력 바뀜), scheduler 잡 래퍼·run_full(핫패스 고위험·저가치), KR/US 데이터수집(KIS vs FDR — 진짜 중복 아님). → '절대 불변' 우선, 과리팩토링 회피.
- ✅ **서버 배포 완료**(사용자 "푸시해" 후): 06:30 publisher 자동 pull로 이미 반영+06:30 us_morning 정상발행, 이후 재시작·라이브 스모크(run_us_premarket no-send: mode=us_premarket top3=3 groups=4 에러0) 통과. 즉시 롤백=backup 브랜치.

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

- **origin/main 최신 커밋**: `04e681e`(팝업·순서·과열게이트 #452~459) + 서버 자동리포트 커밋. 2026-06-05~06 세션 전부 배포 완료(리팩토링 포함 서버 라이브).
- **서버(`lotto-server` = 134.185.109.195) = origin/main과 동기화 + 서비스 재시작 완료.** (로컬수정 `config/screener.yaml` RAM축소판만 autostash 보존)
- **테스트**: `.venv\Scripts\python.exe -m pytest tests/ -q` → **240 passed** (기준선).
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
**✅ 손절 전략 확정·구현 완료(06-07)**: ABCDE 전략별 손절(§0a, 커밋 8c1f906). 동시보유 상한·익절 = v1 그대로(추가 안 함, 사용자 확정). 60분/ATR 손절은 폐기(전략별 채택).

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
