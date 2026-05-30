# 한국 주식 매매 전략 봇 — Handover

> 다른 프로젝트에서 재사용할 수 있도록 핵심 자산·검증 결과·KIS API 노하우를 정리.
> 최종 갱신: 2026-05-31

---

## 1. 프로젝트 개요

KIS(한국투자증권) Open API 기반 한국주식 매매전략 백테스트·실시간 스크리닝 봇.
- **데이터**: KIS REST API (시세·일봉·잔고·체결·종목상태)
- **전략 검증**: 사용자 실매매 사례 역산 → 공통 패턴 추출 → 조건식 정의 → 다종목 백테스트
- **출력**: 텔레그램(요약+차트) + GitHub Pages 웹 리포트
- **GitHub**: newbittles/daily_stock_report

---

## 2. KIS Open API 핵심 노하우 (재사용 가치 최고)

### 2.1 인증 (`src/datasource/kis/token.py`)
- 도메인: 실전 `https://openapi.koreainvestment.com:9443` / 모의 `https://openapivts.koreainvestment.com:29443`
- 토큰: `POST /oauth2/tokenP` (grant_type=client_credentials), 24h 유효 → 만료 10분전 자동 갱신 + 파일 캐시
- 계좌번호: 종합계좌 8자리(CANO) + 상품코드 2자리. `50190660` → 자동으로 (50190660, 01) 분리

### 2.2 검증된 TR_ID (공식 GitHub koreainvestment/open-trading-api 확인)
| 기능 | URL | TR_ID |
|------|-----|-------|
| 현재가 | `/uapi/domestic-stock/v1/quotations/inquire-price` | FHKST01010100 |
| 일봉 | `.../inquire-daily-itemchartprice` | FHKST03010100 |
| 분봉 | `.../inquire-time-itemchartprice` | FHKST03010200 |
| 거래량순위 | `.../quotations/volume-rank` | FHPST01710000 |
| 등락률순위 | `.../ranking/fluctuation` | FHPST01700000 |
| 잔고 | `.../trading/inquire-balance` | TTTC8434R(실)/VTTC8434R(모) |
| 체결내역 | `.../trading/inquire-daily-ccld` | TTTC0081R(실)/VTSC9215R(모) |
| 종목기본정보 | `.../quotations/search-stock-info` | CTPF1002R |

### 2.3 ⭐ 종목 제외 필터 — 가장 중요한 발견
`inquire-price` output 한 번으로 종목 상태 7종 판별 (삼성전자=정상 실측):
```
mang_issu_cls_code = 'Y'/'N'      관리종목
temp_stop_yn       = 'Y'/'N'      거래정지
mrkt_warn_cls_code = '00'/'01'/'02'/'03'  정상/투자주의/경고/위험
invt_caful_yn      = 'Y'/'N'      투자유의
short_over_yn      = 'Y'/'N'      단기과열
marg_rate          = '60.00' 등   증거금율(100이면 증거금100%종목)
iscd_stat_cls_code = '55'(정상)   51관리/52위험/53경고/54주의/57증거금/58정지/59과열
```

**🔴 결정적 교훈**: 증거금100%·단기과열은 **대세상승주에 자주 붙는다**.
- 대우건설(+654%), SK네트웍스(→30만원), 성호전자(→30만원), 대한광통신(+265%), 고려아연 = **전부 증거금100%**
- → 키움식 "증거금100%·단기과열 제외"를 그대로 쓰면 **대박주를 구조적으로 놓침**.
- **권장**: 고위험만 제외(관리/거래정지/투자경고/투자위험). 증거금100%·단기과열·투자주의는 허용.
- 구현: `KisAdapter.get_exclusion_status(ticker, strict=False)` — 기본 고위험만, strict=True면 전부.

### 2.4 KIS API로 불가능한 것 (확인된 제약)
- **관심종목 쓰기(등록)**: 조회만 가능(HHKCM113004C7/C6), 등록 API 없음 → 텔레그램에 복붙용 코드리스트로 대체
- **일봉 100건 제한**: 200봉+ 필요시 기간 분할 다회 호출 (`get_ohlcv` end_date + days로 구현)
- **수급(외인/기관) 실시간**: pykrx/KIS 모두 인증 또는 마감후만. 지수 시계열은 FinanceDataReader(KS11/KQ11) 우회

---

## 3. 검증된 매매 전략 (사용자 실매매 역산)

### 전략 A — 수렴 후 대세상승 시작 (추세 초입)
**진입** (`is_convergence_breakout`, A3, 15사례 14/15=93% 포착):
```
① 단기이평(5/10/20) 수렴 ≤6%  OR  (직전5일 수렴이력 + 당일거래량 1.5배↑ 돌파)
② 종가가 5/10/20 모두 위 (수렴대 상승전환)
③ 종가 > 120일선 (장기추세 위, 이격 상한 없음)
④ MACD 약한필수(하락 중이면 제외) + 상태 알림(0선돌파/GC/0선위/상승)
※ 신고가·120선우상향·주봉정배열 모두 제거 (바닥탈출 대박 죽이지 않으려)
```
**청산** (`scripts/backtest_A.py`):
- 손절: 종가 20일선 2일연속 이탈 → 익일 시가
- 익절1: MACD 시그널아래 + 20선 이탈 → 익일 시가
- 익절2: 일목 구름 하향이탈 → 당일 종가 100%

**다종목 백테스트(12종목, 25/7~26/5)**: 59진입 승률41% 평균+15.9%. 손익비 전략(대박 의존). LG이노텍+443%, 대우건설+654%.

**핵심 교훈**:
- "정배열 완성"이 아니라 "수렴 후 막 상승전환"이 진입점 (정배열은 상승 후 완성됨)
- 60>120 장기정배열은 12%로 부적합 (당일엔 미완성)
- MACD는 골든크로스(47%)가 아니라 **방향(상승중 93%)**으로 봐야 유효 — 단 변별력 약해 "약한필수+알림"으로
- 신고가·120선우상향 필터는 박스권(NAVER) 거르지만 바닥탈출 대박(대우건설)도 거름 → 손익비 위해 제거

### 전략 B — 주도주 눌림목 (급등 후 조정)
**진입** (`is_ma20_pullback`):
```
① 가격급등: 10일 내 저점→고점 +15% (거래량 무관 — 대형주도)
② 20일선 우상향 (60일선 기울기, 박스권 제외)
③ 신고가 경신 (직전 60일 고점, 박스권 제외)
④ 종가 ≥ 20일선 (손절선)
⑤ 종가 ≤ 5일선 + 5일고점대비 -2%↓ (단기 눌림)
⑥ 20일선 이격 ≤45%
```
**청산**: 2일 연속 종가 20일선 이탈
**백테스트(9종목)**: 34진입 승률62% 평균+38%. 대한광통신+265%, 성호전자+104%.

**핵심 교훈**:
- 급등 정의는 가격(+15%)이지 거래량 아님 (대형주는 +49% 급등해도 거래량 1.5배)
- 눌림깊이 상한·극단급등 상한 보강은 부작용 커서 비활성 (대박도 죽임)
- 박스권 대형주(삼성SDS)는 구조적 한계 (신고가+60선기울기로 부분완화)
- "음봉3연속"보다 "20일선 위 + 단기눌림"이 본질

### 전략 C — 52주 신고가 돌파 (미정교화)
신고가 근접 + 거래량 돌파 + 정배열. 아직 사용자 사례 역산 안 함.

---

## 4. 재사용 가능한 순수 모듈 (외부 의존 없음)

### `src/indicators/core.py` — 지표 (numpy 없이 표준 라이브러리)
`moving_average, ema, rsi, macd, bollinger_bands, cci, ichimoku`

### `src/patterns/core.py` — 패턴 판정 (Candle 입력 → PatternResult)
- `is_ma_alignment` 정배열 / `is_ma20_pullback` B전략 / `is_convergence_breakout` A전략
- `is_consecutive_bearish` 음봉연속 / `is_breakout` 돌파 / `is_near_high` 신고가
- `is_macd_golden_cross` / `is_weekly_ma_alignment` 주봉 / `resample_weekly` 일봉→주봉

### `src/screener/` — YAML 조건검색 엔진
`config/screener.yaml`에서 코드 수정 없이 전략 추가/편집. engine.py가 조건키→패턴함수 매핑.

---

## 5. 검증 도구 (스크립트)

| 스크립트 | 용도 |
|---------|------|
| `scripts/scan_signals.py` | 종목+기간 → 신호일 + 매수/손절 추적 (--compare 보강비교) |
| `scripts/backtest_A.py` / `_multi.py` | A 전략 백테스트 (단일/다종목) |
| `scripts/backtest_pullback.py` | B 전략 백테스트 |
| `scripts/verify_A.py` | A 사례 포착률 검증 (A1/A2/A3 버전) |
| `scripts/analyze_A.py` / `analyze_buypoints.py` | 매수사례 지표 역산 (공통패턴 추출) |
| `scripts/diag_A.py` | 특정 종목·시점 미포착 사유 진단 |
| `scripts/simulate_daily.py` / `_A.py` | 일별 추천 시뮬 + 텔레그램 발송 |
| `scripts/compare_surge_cap.py` | 보강조건 ON/OFF 전종목 비교 |

**검증 방법론** (핵심 자산):
1. 사용자가 "이때 샀다/살 자리" 종목+날짜 제공
2. `analyze_*`로 그 시점 지표 역산 → 공통 패턴 추출 (정배열%, 수렴%, MACD, 거래량 등)
3. 데이터가 사용자 직관과 다르면 **정직하게 교정** (예: "정배열"이라 했지만 실제론 수렴)
4. 조건식 정의 → `verify_*`로 포착률 → `backtest_*`로 승률·손익비
5. 필터 추가 시 항상 "가짜 거르기 vs 대박 죽이기" 트레이드오프 비교

---

## 6. 작업 원칙 (이 프로젝트에서 확립)

- **종목코드·TR_ID 등 임의 매핑은 추측 금지** — 반드시 검증 (할루시네이션 1회 발생: 성호전자 080470≠043260)
- **필터는 손익비로 판단** — 가짜 완벽 제거하려다 대박 죽이면 손해. 손절로 관리가 우월.
- **사용자 직관도 데이터로 검증** — 맞으면 확인, 틀리면 정직하게 교정 (MACD GC 약함→방향 강함)
- **모의투자(paper) 우선** — 실거래 전 검증

---

## 7. 환경

| 항목 | 값 |
|------|-----|
| 실행 | `.venv` (64-bit Python), `python -m src.market_report` 등 |
| KIS | `.env`: KIS_APP_KEY/SECRET/ACCOUNT_NO/ENV (gitignore) |
| 의존성 | httpx, pandas, mplfinance, ta, finance-datareader, pykrx, jinja2, python-telegram-bot, pyyaml |
| 테스트 | pytest (79개 통과) |
| ⚠️ 키움 OCX | 레거시. `opcommapi.dll` 락 문제로 폐기 → KIS REST 전환 |

---

## 8. 미완 / 다음 작업

- [ ] A 전략 OR경로 정교화 (수렴 31% 종목까지 잡는 부작용 — lookback 조이기)
- [ ] A를 screener.yaml에 정식 반영 + engine 조건키 추가
- [ ] C 전략 사용자 사례 역산
- [ ] 봇 상시 운영 (`/screen`, `/holdings`) + 스케줄러 손절 알림 연결
- [ ] 우선주/ETF/스팩 정밀 분류 (search-stock-info CTPF1002R, 현재 이름기반)
- [ ] 웹 리포트 Plotly 인터랙티브 차트 (텔레그램은 PNG만 가능)
