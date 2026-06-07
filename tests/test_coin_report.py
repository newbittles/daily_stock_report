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
    """유니버스 ~10개, 각 항목에 sym/name_ko/upbit/gecko 키. BTC·ETH 포함."""
    assert 8 <= len(COIN_UNIVERSE) <= 12
    syms = [c["sym"] for c in COIN_UNIVERSE]
    assert "BTC" in syms and "ETH" in syms
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
    fng = {"score": 25, "rating_ko": "극단적 공포"}
    glob = {"btc_dominance": 58.3, "mcap_change_24h": -1.4}
    html = render_coin_html(rows, fng=fng, glob=glob, fx=1450.0,
                            now=datetime(2026, 6, 7, 17, 0))
    assert "<html" in html.lower()
    assert "비트코인" in html and "김치프리미엄" in html
    assert "2026-06-07" in html


def test_scheduler_registers_coin_job():
    """report_coin 잡이 매일(주말 포함) 17:00로 등록 — 일요일에도 next run이 잡혀야 함."""
    from src.market_report.scheduler import build_scheduler
    sch = build_scheduler()
    job = next((j for j in sch.get_jobs() if j.id == "report_coin"), None)
    assert job is not None
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["hour"] == "17" and fields["minute"] == "0"
    assert fields["day_of_week"] == "*"   # 주말 포함
