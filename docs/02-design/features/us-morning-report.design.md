# 미국장 아침 리포트 (us_morning) 설계

> 미국 증시 마감 후 아침(한국 07:30) 발송. 미국 시장 AI 요약 + 미국 강세테마와 연결된
> **한국 시초 매수 Top3** 추천. 국장(pre/post) 포맷을 차용해 점진적으로 국장+미국장 확장.
> 상위: [stock-insight-bot.design.md](stock-insight-bot.design.md) — MarketDataSource 미국 확장점 실현.

---

## 1. 확정 결정 (사용자 합의 2026-06-02)

| # | 항목 | 결정 |
|---|------|------|
| U1 | 데이터 소스 | **FinanceDataReader** (무료, 미국 지수/종목 확인 완료. KIS 해외 API 불필요) |
| U2 | 발송 시각 | **07:30 KST** (미국장 마감 06:00 후 데이터 확정 + 국장 09:00 전 여유) |
| U3 | 테마 브릿지 | **하이브리드** — 룰 테마매핑표 + A/B/C/D 시그널 필터 + AI 코멘트 |
| U4 | 시초 Top3 점수 | **P4 + 미국모멘텀 가중 + ATR 손절** (국장 Top3 = P4 재사용) |
| U5 | 구성 | 2섹션: ①미국 시장 AI 요약(상승종목/테마) ②시초 매수 Top3(한국) |
| U6 | 추천 성격 | 시초(09:00) 진입 적합 — 종가베팅과 구분(미국 모멘텀 → 갭상승 기대) |

---

## 2. 리포트 구성 (텔레그램 + 웹)

```
🌎 미국 증시 마감 요약 — YYYY-MM-DD
  📊 S&P500 · 나스닥 · 다우 · 필라델피아반도체(SOX)  각 종가(±%)
  🤖 AI 요약: "미국장 왜 이렇게 움직였나" (왜 올랐나 톤, 뉴스·테마 근거)
  🔥 강세 테마/섹터: 반도체 · AI · 2차전지 …
  📈 주요 상승 종목: 엔비디아·테슬라 등 (빅테크7 + 시총상위)
        ↓ (미국 강세테마 → 한국 동일테마 갭상승 기대)
🏆 오늘 시초 매수 Top 3 (한국)
  - 미국 강세테마 ∩ 종목풀 ∩ A/B/C/D 시그널 충족
  - 시초 진입가(전일 종가 참고) + ✂️ATR 손절가(-X%)
  - 추천이유: "미국 반도체 강세 → 삼성전자(C·정배열) 시초 주목"
  ※ 참고용 · 매수 추천 아님 · 판단 책임 본인
```

---

## 3. 데이터 흐름

```
07:30 us_morning 트리거 (scheduler 신규)
  └ ★신규 USMarketSource (FDR)
       ├ 지수: US500, IXIC, DJI, SOX
       ├ 빅테크7 + 시총상위 등락 → 주요 상승종목
       └ 섹터 ETF 등락(SOXX·QQQ·XLE·XBI 등) → 미국 강세테마 추출
  └ AI 요약 (gemini) — 미국장 why_moved + 강세테마 해설
  └ ★신규 ThemeBridge: 미국 강세테마 → 한국 테마/종목 매핑(룰표)
  └ 기존 collect_screen_picks (한국 종목풀 + A/B/C/D) 재사용
  └ ★신규 select_morning_top3:
       P4 점수 + us_boost(미국 강세테마 일치 가중) + ATR 손절 → Top3
  └ 템플릿 렌더(post_close 차용) → 텔레그램 + 웹 게시
```

---

## 4. 컴포넌트

| 파일 | 신규/수정 | 내용 |
|------|-----------|------|
| `src/datasource/us/fdr_source.py` | 신규 | FDR 기반 미국 지수/종목/섹터 수집 (지연 일봉 — 마감 데이터) |
| `src/market_report/theme_bridge.py` | 신규 | 미국 섹터/테마 → 한국 테마·종목 매핑표 + 매칭 로직 |
| `src/market_report/morning_top3.py` | 신규 | `select_morning_top3` — P4 + us_boost + ATR. `top3.py`·`strategy_section`의 점수/ATR 재사용 |
| `src/market_report/analyzer.py` | 수정 | `_us_morning_prompt` + 미국장 요약(why_moved·강세테마) |
| `src/market_report/models.py` | 수정 | `ReportMode += "us_morning"`, 미국 지수/종목 필드 |
| `src/market_report/pipeline.py` | 수정 | us_morning 분기 — 미국 수집 → 브릿지 → 시초 Top3 |
| `src/market_report/scheduler.py` | 수정 | 07:30 `report_us_morning` 잡 (평일, 미국 휴장일 가드) |
| `src/market_report/templates/report.html` | 수정 | 미국 요약 섹션 + 시초 Top3 (post 템플릿 분기) |
| `src/market_report/telegram_notify.py` | 수정 | `_format_us_morning_summary` |

---

## 5. ThemeBridge — 미국 강세테마 → 한국 (하이브리드, U3)

룰 매핑표(1차, 결정론) + A/B/C/D 필터 + AI 코멘트(2차, 설명):

```python
US_TO_KR_THEME = {
    "반도체":   ["반도체", "HBM", "반도체장비"],      # SOX·엔비디아↑ → 한국 반도체
    "AI/빅테크": ["AI", "소프트웨어", "데이터센터"],
    "2차전지":  ["2차전지", "전기차"],                # 테슬라↑
    "방산":     ["방산", "우주항공"],
    "바이오":   ["제약/바이오"],
    "에너지":   ["정유", "신재생에너지"],
}
```
- 미국 강세 섹터 식별 → 매핑된 한국 테마 → 종목풀에서 해당 테마 + A/B/C/D 충족 종목에 **us_boost 가중**
- AI는 "미국 X 강세 → 한국 Y 주목" 연결 코멘트만 생성(점수 결정은 룰+시그널)

## 6. 시초 Top3 점수 (U4)

```
score = P4_score(strat·mom·liq·align·nh·end)         # 기존 top3.py 재사용
      + W_us · us_theme_match                          # 미국 강세테마 일치 가중(신규)
손절 = 현재가(전일종가) - 1.5×ATR                       # 국장과 동일 ATR 손절
```
- `W_us` 가중치는 백테스트로 튜닝(초기값 가정 → input 기록)
- 시초 진입가 = 전일 종가(미국 모멘텀 반영한 갭상승은 당일 시초 확인)

---

## 7. 미해결 / 사용자 결정 필요

| # | 항목 | 비고 |
|---|------|------|
| Q1 | 미국 강세테마 판정 기준 | 섹터 ETF 등락 임계치(예 +1.5%↑)? 빅테크 개별? |
| Q2 | `W_us` 가중치 | 미국 연동을 얼마나 강하게? 백테스트 필요(미국테마 적중 시 국장 시초 수익률) |
| Q3 | 미국 종목 뉴스 소스 | FDR엔 뉴스 없음 — 지수/종목 등락 기반 AI 추론(1단계) vs 뉴스 크롤(2단계) |
| Q4 | 미국 휴장일 처리 | 휴장 다음 아침은 발송 스킵 or 직전 거래일 |
| Q5 | 서머타임 마감시각 변동 | 06:00(서머)/06:00 겨울? 발송 07:30 고정이면 무관 |

## 8. Phase

1. **P1**: USMarketSource(FDR) — 지수4 + 빅테크 + 섹터ETF 수집 + AI 미국요약 (미국 단독 리포트)
2. **P2**: ThemeBridge + select_morning_top3 (미국→한국 시초 Top3)
3. **P3**: 백테스트로 W_us 튜닝 + 미국테마 적중→국장 시초 수익률 검증
4. **P4**: 뉴스 소스 보강, 국장+미국장 통합 관점
