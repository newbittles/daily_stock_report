"""코인 시세 리포트 — 순수 파서·김프 계산·포맷·스케줄 등록 테스트 (외부호출 0)."""
from __future__ import annotations

from datetime import datetime

from src.datasource.coin.sources import (
    COIN_UNIVERSE,
    _parse_fng,
    _parse_gecko_global,
    _parse_gecko_markets,
    _parse_upbit,
)
from src.market_report.coin_report import (
    build_coin_rows,
    format_coin_telegram,
    kimchi_premium,
    render_coin_html,
)


def test_coin_universe_shape():
    """유니버스 5개 고정: USDT 최상단 + BTC·ETH·XRP·SOL (DOGE 제외, 사용자 2026-06-14)."""
    syms = [c["sym"] for c in COIN_UNIVERSE]
    assert syms == ["USDT", "BTC", "ETH", "XRP", "SOL"]
    assert "DOGE" not in syms   # 사용자 요청으로 도지코인 제외(2026-06-14)
    # 스테이블 — 지표(이격·RSI)는 표시하되 ABCDE/E 전략만 제외(평탄차트 오탐, 2026-06-08)
    assert COIN_UNIVERSE[0].get("strategies") is False
    for c in COIN_UNIVERSE:
        assert c["upbit"].startswith("KRW-")
        assert c["gecko"]


def test_parse_upbit():
    payload = [
        {"market": "KRW-BTC", "trade_price": 150000000.0,
         "signed_change_rate": 0.0123, "acc_trade_price_24h": 5.2e11},
        {"market": "KRW-ETH", "trade_price": 5000000.0,
         "signed_change_rate": -0.005, "acc_trade_price_24h": 1.1e11},
    ]
    out = _parse_upbit(payload)
    assert out["KRW-BTC"]["krw"] == 150000000.0
    assert abs(out["KRW-BTC"]["change_pct"] - 1.23) < 1e-9
    assert out["KRW-ETH"]["change_pct"] == -0.5
    assert out["KRW-BTC"]["value_24h"] == 5.2e11


def test_parse_gecko_markets():
    payload = [
        {"id": "bitcoin", "current_price": 103000.5, "price_change_percentage_24h": 1.7,
         "market_cap": 2.0e12, "market_cap_rank": 1},
        {"id": "ethereum", "current_price": 3400.0, "price_change_percentage_24h": None,
         "market_cap": 4.1e11, "market_cap_rank": 2},
    ]
    out = _parse_gecko_markets(payload)
    assert out["bitcoin"]["usd"] == 103000.5
    assert out["bitcoin"]["change_pct"] == 1.7
    assert out["ethereum"]["change_pct"] is None  # 결측 허용
    assert out["bitcoin"]["rank"] == 1


def test_parse_gecko_global():
    payload = {"data": {"market_cap_percentage": {"btc": 58.31, "eth": 12.0},
                        "market_cap_change_percentage_24h_usd": -1.42}}
    out = _parse_gecko_global(payload)
    assert abs(out["btc_dominance"] - 58.31) < 1e-9
    assert abs(out["mcap_change_24h"] - (-1.42)) < 1e-9


def test_parse_fng():
    payload = {"data": [{"value": "25", "value_classification": "Extreme Fear"}]}
    out = _parse_fng(payload)
    assert out["score"] == 25
    assert out["rating_ko"] == "극단적 공포"
    # 결측/이상 payload → None
    assert _parse_fng({}) is None
    assert _parse_fng({"data": []}) is None


def test_kimchi_premium():
    # 업비트 1.5억, 글로벌 $100,000, 환율 1,450 → (1.5e8/1.45e8 - 1)*100 ≈ +3.448%
    kp = kimchi_premium(150_000_000, 100_000, 1450.0)
    assert kp is not None and abs(kp - 3.4483) < 0.01
    # 환율/달러가 0 또는 None → None (0나눗셈 방어)
    assert kimchi_premium(150_000_000, 0, 1450.0) is None
    assert kimchi_premium(150_000_000, 100_000, 0) is None
    assert kimchi_premium(None, 100_000, 1450.0) is None


def _sample_rows():
    universe = [
        {"sym": "BTC", "name_ko": "비트코인", "upbit": "KRW-BTC", "gecko": "bitcoin"},
        {"sym": "ETH", "name_ko": "이더리움", "upbit": "KRW-ETH", "gecko": "ethereum"},
    ]
    upbit = {"KRW-BTC": {"krw": 150_000_000.0, "change_pct": 1.23, "value_24h": 5.2e11}}
    gecko = {"bitcoin": {"usd": 100_000.0, "change_pct": 1.7, "mcap": 2.0e12, "rank": 1},
             "ethereum": {"usd": 3400.0, "change_pct": -0.2, "mcap": 4.1e11, "rank": 2}}
    return build_coin_rows(universe, upbit, gecko, fx=1450.0)


def test_build_coin_rows():
    rows = _sample_rows()
    assert len(rows) == 2
    btc = rows[0]
    assert btc["sym"] == "BTC" and btc["krw"] == 150_000_000.0 and btc["usd"] == 100_000.0
    assert btc["kimchi"] is not None and abs(btc["kimchi"] - 3.4483) < 0.01
    # ETH는 업비트 데이터 없음 → krw/kimchi None이어도 행은 유지(글로벌만 표시)
    eth = rows[1]
    assert eth["krw"] is None and eth["kimchi"] is None and eth["usd"] == 3400.0


def _usdt_row(with_analysis: bool = False):
    r = {"sym": "USDT", "name_ko": "테더", "krw": 1517.0, "krw_change": -0.2,
         "value_24h": 1e11, "usd": 0.9996, "usd_change": 0.0,
         "mcap": 1e11, "rank": 3, "kimchi": -2.6}
    if with_analysis:  # 전략 제외 분석(테더, 2026-06-08): strats 항상 []
        r["analysis"] = {
            "daily": {"phase_emoji": "🟢", "phase_name": "정상", "g20": 1.9, "g60": 2.2,
                      "rsi": 63.0, "macd": "양·골든", "strats": []},
            "h4": {"phase_emoji": "🟡", "phase_name": "단기눌림", "g20": 0.3, "g60": 1.9,
                   "rsi": 64.0, "macd": "양·데드", "strats": []},
            "e_bottom": False,
        }
    return r


def test_format_telegram_usdt_in_header():
    """테더는 번호 목록이 아니라 헤더(환율) 바로 아래 분리 + 신호등 표시(사용자 2026-06-08)."""
    rows = [_usdt_row(with_analysis=True)] + _sample_rows()
    text = format_coin_telegram(rows, fng=None, glob=None, fx=1450.0,
                                now=datetime(2026, 6, 8, 17, 0))
    assert "₮ 테더(USDT) 1,517원 (-0.2%) · 김프 -2.6% · 일봉 🟢정상 · 4시간봉 🟡단기눌림" in text
    assert "1. 비트코인" in text and "2. 이더리움" in text   # 번호는 BTC부터
    assert "테더(USDT)" not in text[text.index("1. 비트코인"):]  # 목록엔 테더 없음
    assert text.index("테더") < text.index("1. 비트코인")        # 헤더 쪽에 위치
    # 테더엔 전략 표기 없음(분석 제외 — '없음'도 표기 안 함, 오해 방지)
    assert "전략" not in text[:text.index("1. 비트코인")]


def test_format_coin_telegram():
    rows = _sample_rows()
    fng = {"score": 25, "rating_ko": "극단적 공포"}
    glob = {"btc_dominance": 58.3, "mcap_change_24h": -1.4}
    text = format_coin_telegram(rows, fng=fng, glob=glob, fx=1450.0,
                                now=datetime(2026, 6, 7, 17, 0))
    assert "비트코인" in text and "BTC" in text
    assert "김프" in text and "+3.4" in text
    assert "공포탐욕" in text and "25" in text
    assert "도미넌스" in text and "58.3" in text
    assert "150,000,000" in text          # 천단위 콤마
    assert "참고용" in text               # 면책 문구(프로젝트 규칙 2)
    # 결측 섹션은 생략돼도 죽지 않음
    t2 = format_coin_telegram(rows, fng=None, glob=None, fx=0.0,
                              now=datetime(2026, 6, 7, 17, 0))
    assert "비트코인" in t2


def test_render_coin_html():
    rows = _sample_rows()
    rows[0]["analysis"] = _sample_analysis()
    fng = {"score": 25, "rating_ko": "극단적 공포"}
    glob = {"btc_dominance": 58.3, "mcap_change_24h": -1.4}
    html = render_coin_html(rows, fng=fng, glob=glob, fx=1450.0,
                            now=datetime(2026, 6, 7, 17, 0))
    assert "<html" in html.lower()
    assert "비트코인" in html and "김치프리미엄" in html
    assert "2026-06-07" in html
    # 모바일 가시성(사용자 2026-06-07): 광폭 테이블 금지 → 카드 레이아웃 + viewport
    assert "<table" not in html
    assert 'class="card"' in html
    assert "viewport" in html
    # 테더는 카드 목록이 아니라 헤더 아래 분리 바 + 지표 상세(전략 제외, 사용자 2026-06-08)
    rows_t = [_usdt_row(with_analysis=True)] + rows
    html_t = render_coin_html(rows_t, fng=fng, glob=glob, fx=1450.0,
                              now=datetime(2026, 6, 7, 17, 0))
    assert 'class="tether"' in html_t
    assert html_t.count('class="card"') == 2   # 카드 2개(BTC·ETH)만 — 테더 카드 없음
    tether_part = html_t[html_t.index('class="tether"'):html_t.index('class="cards"')]
    assert "RSI 63" in tether_part and "MACD 양·골든" in tether_part  # 지표 상세 표시
    assert "전략" not in tether_part                                   # 전략 항목은 제외
    # 일봉/4시간봉 각각 신호등·이격·RSI·MACD·전략 표기
    assert "일봉: 🟢정상" in html and "RSI 58" in html and "MACD 양·골든" in html
    assert "4시간봉: 🟡단기눌림" in html and "MACD 음·데드" in html
    assert "B·E" in html and "시장동반" in html
    # 전략 미매칭 코인(ETH)은 '전략 없음' 명시(사용자 2026-06-07)
    rows[1]["analysis"] = {
        "daily": {"phase_emoji": "🟠", "phase_name": "조정", "g20": -2.0, "g60": 1.0,
                  "rsi": 45.0, "macd": "음·데드", "strats": []},
        "h4": {"phase_emoji": "🟢", "phase_name": "정상", "g20": 0.5, "g60": 1.0,
               "rsi": 50.0, "macd": "양·골든", "strats": []},
        "e_bottom": False,
    }
    html2 = render_coin_html(rows, fng=fng, glob=glob, fx=1450.0,
                             now=datetime(2026, 6, 7, 17, 0))
    assert "전략 없음" in html2


def test_parse_upbit_candles():
    """업비트 캔들 응답은 최신순 → 과거→현재로 뒤집고, 소수 거래량(코인 단위) 보존."""
    from src.datasource.coin.sources import _parse_upbit_candles
    payload = [
        {"candle_date_time_kst": "2026-06-07T09:00:00", "opening_price": 2.0,
         "high_price": 3.0, "low_price": 1.0, "trade_price": 2.5,
         "candle_acc_trade_volume": 10.5},
        {"candle_date_time_kst": "2026-06-06T09:00:00", "opening_price": 1.8,
         "high_price": 2.2, "low_price": 1.5, "trade_price": 2.0,
         "candle_acc_trade_volume": 8.0},
    ]
    out = _parse_upbit_candles(payload)
    assert [c.close for c in out] == [2.0, 2.5]      # 과거 → 현재
    assert out[0].date == "20260606"
    assert out[-1].volume == 10.5                     # float 보존 (int 캐스팅 금지)


def test_compute_gaps():
    from src.market_report.coin_report import compute_gaps
    flat = [100.0] * 130
    g = compute_gaps(flat)
    for k in (5, 20, 60, 120):
        assert abs(g[k]) < 1e-9
    assert g["rsi"] is not None
    # 데이터 부족 → 있는 것만 (120 None)
    g2 = compute_gaps([100.0] * 30)
    assert g2[120] is None and g2[20] is not None


def test_coin_phase():
    from src.market_report.coin_report import coin_phase
    base = {5: 1.0, 20: 2.0, 60: 3.0, 120: 10.0, "rsi": 55.0, "g5_prev": 1.0}
    assert coin_phase(base)[1] == "정상"
    assert coin_phase({**base, "rsi": 25.0})[1] == "바닥권"          # RSI≤30
    assert coin_phase({**base, 60: -8.0})[1] == "바닥권"             # 60일 -7%↓
    # 코인 과열 임계(주식보다 큼): 120≥30 or 60≥20, RSI≥70
    assert coin_phase({**base, 120: 35.0, "rsi": 75.0})[1] == "과열"
    assert coin_phase({**base, 120: 35.0, "rsi": 60.0})[1] == "정상"  # RSI 미달 → 과열 아님
    assert coin_phase({**base, 20: -2.0})[1] == "조정"
    assert coin_phase({**base, 5: -1.0})[1] == "단기눌림"
    assert coin_phase({**base, 60: -1.0, 120: 5.0})[1] == "하락전환"
    assert coin_phase({5: None, 20: None, 60: None, 120: None})[1] == "판단불가"


def _mk_candle(close, open_=None, high=None, low=None, vol=1.0, date="20260601"):
    from src.datasource.base import Candle
    o = open_ if open_ is not None else close
    return Candle(date=date, open=o, high=high or max(o, close),
                  low=low or min(o, close), volume=vol, close=close)


def test_analyze_coin_insufficient_data():
    from src.market_report.coin_report import analyze_coin
    assert analyze_coin([_mk_candle(100.0)] * 10, [], strategies=[], fng_score=None) is None


def test_analyze_coin_e_strategy():
    """투매 바닥(E): 급락+RSI≤30+거래량 2x+반등양봉 + 4H RSI≤30 → 일봉 'E' 배지, F&G≤25면 시장동반."""
    from src.market_report.coin_report import analyze_coin
    daily = [_mk_candle(100.0) for _ in range(60)]
    px = 100.0
    for _ in range(25):                                  # 연속 급락 → RSI·50MA 이격 추락
        px *= 0.97
        daily.append(_mk_candle(px, open_=px * 1.01))
    daily.append(_mk_candle(px * 1.04, open_=px * 1.005, vol=5.0))  # 반등 양봉 + 투매 거래량
    h4 = [_mk_candle(100.0 - i * 0.8) for i in range(40)]           # 4H 연속 하락 → RSI 바닥
    res = analyze_coin(daily, h4, strategies=[], fng_score=20.0)
    assert res is not None
    assert "E" in res["daily"]["strats"]                 # E는 일봉 전략(4H RSI는 게이트)
    assert res["e_bottom"] is True                       # F&G 20 ≤ 25 → 시장 동반 바닥
    assert res["h4"]["rsi"] is not None and res["h4"]["rsi"] <= 30
    assert res["daily"]["macd"] is not None              # MACD 라벨(양·골든/음·데드 등)
    assert res["h4"]["phase_name"]                       # 4H에도 신호등


def _sample_analysis():
    return {
        "daily": {"phase_emoji": "🟢", "phase_name": "정상", "g20": 3.1, "g60": 8.0,
                  "rsi": 58.0, "macd": "양·골든", "strats": ["B", "E"]},
        "h4": {"phase_emoji": "🟡", "phase_name": "단기눌림", "g20": -0.8, "g60": 1.5,
               "rsi": 41.0, "macd": "음·데드", "strats": ["C"]},
        "e_bottom": True,
    }


def test_format_telegram_with_analysis():
    """텔레그램은 신호등만(사용자 2026-06-07 정보과다 축소) — 상세(이격·RSI·MACD·전략)는 웹 전용."""
    rows = _sample_rows()
    rows[0]["analysis"] = _sample_analysis()
    text = format_coin_telegram(rows, fng=None, glob=None, fx=1450.0,
                                now=datetime(2026, 6, 7, 17, 0))
    assert "1. 비트코인" in text and "2. 이더리움" in text   # 번호 매김
    # 신호등은 한 줄, 전략은 한 줄 아래로 분리(사용자 2026-06-14)
    assert "ㄴ일봉 🟢정상 · 4시간봉 🟡단기눌림" in text
    assert "ㄴ전략 B·E 🔥시장동반바닥" in text
    # 상세 지표는 텔레그램에서 제외(웹 전용)
    assert "MACD" not in text
    assert "20일 +3.1%" not in text
    assert "RSI" not in text
    # 분석 없는 코인(ETH)은 시세 줄만 있고 ㄴ줄 없음 — 죽지 않아야 함
    eth_idx = text.index("2. 이더리움")
    assert "ㄴ일봉" not in text[eth_idx:]


def test_format_telegram_no_strategy_shows_none():
    """전략 미매칭이어도 '전략 없음' 명시(누락으로 오인 방지, 사용자 2026-06-07)."""
    rows = _sample_rows()
    a = _sample_analysis()
    a["daily"]["strats"] = []
    a["e_bottom"] = False
    rows[0]["analysis"] = a
    text = format_coin_telegram(rows, fng=None, glob=None, fx=1450.0,
                                now=datetime(2026, 6, 7, 17, 0))
    # 전략은 한 줄 아래로 분리(사용자 2026-06-14) — 미매칭이어도 '없음' 명시
    assert "ㄴ일봉 🟢정상 · 4시간봉 🟡단기눌림" in text
    assert "ㄴ전략 없음" in text


def test_scheduler_registers_coin_job():
    """report_coin 잡이 매일(주말 포함) 17:00로 등록 — 일요일에도 next run이 잡혀야 함."""
    from src.market_report.scheduler import build_scheduler
    sch = build_scheduler()
    job = next((j for j in sch.get_jobs() if j.id == "report_coin"), None)
    assert job is not None
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["hour"] == "17" and fields["minute"] == "0"
    assert fields["day_of_week"] == "*"   # 주말 포함
