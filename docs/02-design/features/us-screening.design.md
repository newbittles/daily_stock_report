# 미국 종목 스크리닝 (us_screening) 설계

> 미국 종목 **자체**에 한국장 A/B/C/D 전략을 적용해 매수 시그널을 산출한다.
> 기존 [us-morning-report.design.md](us-morning-report.design.md)(미국 강세테마→한국 시초 브릿지)와
> **목적이 다른 별개 기능**이다: 이쪽은 미국 종목 그 자체가 추천 대상.
> 상위: [stock-insight-bot.design.md](stock-insight-bot.design.md) — `MarketDataSource` 미국 확장점.

---

## 1. 확정 결정 (사용자 합의 2026-06-03)

| # | 항목 | 결정 |
|---|------|------|
| S1 | 목적 | **미국 종목 자체 매수 시그널** (한국 시초 브릿지와 별개) |
| S2 | 유니버스 | S&P500 ∪ 나스닥100 ∪ 시총상위+거래대금 ∪ 핫종목(상승률·거래량) |
| S3 | 테마/섹터 분류 | **FDR 섹터/산업** (무료·결정론). 외부 크롤링(네이버/트뷰/인베스팅) 비채택 |
| S4 | 전략 | 기존 A/B/C/D를 **그대로** 적용 (engine 순수함수 재사용, 수정 0) |
| S5 | 데이터 | FinanceDataReader + **yfinance 배치 다운로드**(부하 절감) |

---

## 2. 핵심 통찰 — 전략 이식이 아니라 "주변" 작업

`src/screener/engine.py`·`src/patterns`·`src/indicators`는 **순수 OHLCV 함수**다
(`Candle` 리스트만 받음). 따라서 **A/B/C/D 로직은 한 줄도 안 바꾼다.** 실제 작업은:

| 작업 | 이유 |
|------|------|
| ① 미국 **유니버스 수집** | CLAUDE.md §1 전체스캔 금지 → S&P500 등 명시 풀 |
| ② FDR/yf OHLCV → `Candle` 어댑팅 | `date(YYYYMMDD), o/h/l/c/v` |
| ③ **달러 기준 파라미터** 분리 | `config/screener_us.yaml` 신설 (한국 `screener.yaml` 미오염) |
| ④ ETF 제외 (미국식) | 미국은 ETF 다수 → listing 기반 제외 |
| ⑤ enrich 분기 | 기존 `_enrich_picks`는 네이버 테마 의존 → 미국은 FDR 섹터 사용 |

---

## 3. 검증된 데이터 사실 (2026-06-03 실측)

- `fdr.StockListing('S&P500')` → `Symbol, Name, Sector, Industry` (503종목, GICS 영문)
- `fdr.StockListing('NASDAQ')` → `Symbol, Name, IndustryCode, Industry` (3902종목, KRX식 한글 산업)
- ⚠️ **미국 listing엔 시총·거래대금 컬럼 없음** → 1차 필터를 listing만으론 불가 (OHLCV로 산출)
- ⚠️ `NASDAQ100`은 FDR **미지원** → 나스닥100은 정적 심볼셋으로 별도 공급(P2)
- `fdr.DataReader('NVDA', start)` → `Open/High/Low/Close/Volume/Adj Close`, 단건 ~0.3s
- **yfinance 설치됨** → `yf.download([...syms], group_by='ticker')` 배치로 503종목 일괄 수집

---

## 4. 컴포넌트

| 파일 | 신규/수정 | 내용 |
|------|-----------|------|
| `src/datasource/us/universe.py` | **신규** | `get_sp500_universe()` — S&P500 심볼+섹터+산업, 하루 1회 캐시 |
| `src/datasource/us/fdr_source.py` | 수정 | `fetch_us_ohlcv_batch(symbols, days)` (yfinance 배치) + `to_candles()`; 기존 us_morning 함수 유지 |
| `config/screener_us.yaml` | **신규** | C전략만 enabled(P1), 달러 기준 `min_trade_value`/`min_price`, ETF 제외 |
| `src/screener/us_pipeline.py` | **신규** | `run_us_screening(cfg)` — 유니버스→배치 OHLCV→`screen_stock` 재사용→`USStockPick` |
| `scripts/run_us_screening.py` | **신규** | 실행 진입점/스모크 |
| `tests/test_us_screening.py` | **신규** | Candle 변환·유니버스·C전략 매칭 결정론 검증 |

> engine.py·patterns·indicators: **수정 없음**(순수 재사용). 한국 `pipeline.py`도 건드리지 않음(충돌 회피).

---

## 5. 데이터 흐름 (P1)

```
run_us_screening(cfg=screener_us.yaml)
  └ get_sp500_universe()                    # 503 심볼 + 섹터/산업 (캐시)
  └ fetch_us_ohlcv_batch(symbols, days=120) # yfinance 일괄 → {sym: [Candle]}
  └ for sym:
       price/거래대금/등락률 산출 → min_price·min_trade_value·ETF 1차 필터
       screen_stock(strategies, candles, change_pct)   # ★ engine 재사용
  └ USStockPick(매칭 종목 + 섹터 + 근거수치 + 면책)
```

부하: 첫 실행은 yfinance 일괄(수~수십 초). OHLCV 일봉은 하루 1회 캐시 가능(P2 최적화).

---

## 6. 달러 기준 파라미터 (screener_us.yaml 초기값 — 백테스트로 튜닝)

| 항목 | 한국 | 미국 초기값(가정) | 근거 |
|------|------|------|------|
| `min_price` | 1000원 | **5달러** | 페니주 제외 통념 |
| `min_trade_value` | 30억원 | **$50,000,000** | 대형주 일 거래대금 수천만~억달러 |
| C전략 conditions | 동일 | 동일 | 순수 비율 기반이라 통화 무관 |

> ⚠️ 초기값은 **가정**. P3에서 미국 데이터 백테스트로 보정. (CLAUDE.md §2 면책)

---

## 7. 단계 (Phase)

1. **P1 (완료)**: C전략 + S&P500 + FDR 섹터 + yfinance 배치 + 테스트. 동작 검증.
2. **P2 (완료)**: A전략 추가 + **나스닥 전체 거래대금 2단계 필터** 유니버스(중소형 급등주) + rate limit 청크화. (나스닥100 정적셋 대신 거래대금 상위 채택)
3. **P3**: B·D 전략 + 달러/미국 변동성 기준 파라미터 백테스트 튜닝 + OHLCV 일별 캐시.
4. **P4**: 리포트/발송 통합 (UI 담당 작업과 합류 — 별 워크트리 조율).

---

## 8. 미해결 / 결정 필요

| # | 항목 | 비고 |
|---|------|------|
| Q1 | ~~나스닥100 심볼 출처~~ | **해소(P2):** 나스닥100 대신 나스닥 전체에서 거래대금 상위 추출(중소형 포함이 사용자 의도) |
| Q2 | ~~핫종목 정의~~ | **해소(P2):** 나스닥 전체 가벼운 시세→당일 거래대금 상위 N(2단계 필터) |
| Q3 | OHLCV 캐시 위치 | `data/us_ohlcv_cache/` 일자별 — P3 (현재 combined 27s, rate limit 청크화로 운영 가능) |
| Q4 | 미국 휴장/서머타임 | 발송 통합(P4) 시 가드 |
| Q5 | 클래스주 심볼 정규화 | FDR `BRKB`·`BFB` → yfinance `BRK-B`·`BF-B` (P1 실측 2종목 실패) — P2 매핑표 |
| Q6 | 거래대금 달러 표시 | engine 메시지가 원화 '억' 포맷 → 미국 `$42.8B`가 '140억'으로 표기. **필터 비교($50M)는 정확**, 표시만 P4 리포트단에서 달러 변환 |

---

## 9. P1 검증 결과 (2026-06-03 실측)

- 전체 S&P500 499종목(BRKB·BFB 심볼 실패 제외) **18.6초** 스크리닝 → **63종목 포착**
- 포착 종목이 2026 상반기 강세 섹터와 일치: 반도체(MU·AVGO·AMD·AMAT·KLAC·LRCX), 빅테크(AAPL·ORCL·IBM·CSCO), 보안SW(PANW·CRWD)
- 섹터 분류·근거 수치·면책 모두 정상. yfinance 배치로 부하 문제 해소 확인
- 테스트 `tests/test_us_screening.py` 12개 통과. engine·indicators·patterns·한국 pipeline **수정 0**

## 10. P2 검증 결과 (2026-06-03 실측)

- **A전략 추가**(`screener_us.yaml`): A·C 동시 동작, 서로 다른 진입점 포착(A 66 / C 63)
- **유니버스 2단계 필터**: 나스닥 전체 → 거래대금 상위 253종목(후보 2,445, 126s 캐시) → S&P500과 합쳐 combined 754
- **combined end-to-end**: 154종목 포착(27s) = S&P500 105 + 나스닥 중소형 49. LEGN(+42%)·AEHR(+21%)·ACMR(+12%)·USAR·TENB·CZR 등 S&P500 외 중소형 급등주 포착 확인
- **rate limit 대응**(전역 §7): turnover·OHLCV batch 모두 청크(350/200)+랜덤딜레이(1.5~3.5s)+백오프(연속 2회 중단). 3,902종목 한방→429 발생을 청크로 회피
- Industry 한글 분류 정상. 테스트 16개 통과(신규 4). engine·한국 pipeline **수정 0** 유지

## 11. P3 백테스트 결과 (2026-06-03 실측)

**인프라:** `src/backtest/us_engine.py`(순수 — candles+진입fn+MA손절→거래·성과, 6테스트) + `scripts/backtest_us.py`(러너) + OHLCV 일별 캐시(`fetch_us_ohlcv_batch(use_cache)`, 30s→0.5s).

**성과(nasdaq hot 80종목 × 300일, 진입→MA 2일이탈 청산):**

| 전략 | 진입 | 승률 | 평균 | 중앙 | 최악 | 보유 |
|------|------|------|------|------|------|------|
| **C** | 468 | 54.3% | **+79.3%** | +2.1% | -42.7% | 49일 |
| **B** | 327 | 53.2% | +14.5% | +0.8% | **-23.2%** | 18일 |
| A | 629 | 41.7% | +10.4% | **-1.4%** | -41.6% | 17일 |
| D | 416 | 44.2% | +53.5% | **-1.7%** | -40.6% | 39일 |

**결론:**
- **C 최고**(추세추종·장기보유), **B 견고**(최악손실 -23%로 제한적). 둘 다 중앙값 양(+).
- **A·D는 중앙값 음수**(절반 이상 손실, 평균은 소수 대박 의존). 미국 강세장에서 C 대비 열위.
- **D `downtrend_lookback` 스윕(20→3): 승률·중앙·최악 거의 불변**(진입수만 416→330). → lookback 튜닝 무효, D 약세는 강세장 부적합이 원인.

**⚠️ 백테스트 한계(단정 경계):** ① 생존편향(현 거래대금 상위 = 살아남은 강세주) ② 300일=2026 강세장 편중(하락장 미포함) ③ A 청산을 MA20 2일로 단순화(실제 일목+MACD와 상이 → A 과소평가 가능) ④ 거래비용·슬리피지 무시. → 더 견고한 검증(장기간·정확청산) 전까지 A/D 비활성은 보류.
