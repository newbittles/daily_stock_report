"""네이버 모바일 JSON 파서 — 2026-11-05 데스크톱 sise 페이지(Next.js 전환) 대체."""
from src.market_report.scrapers.naver import parse_mobile_index, parse_mobile_stocks
from src.market_report.scrapers.theme import parse_mobile_themes


def test_parse_mobile_index() -> None:
    q = parse_mobile_index("KOSPI", {"closePrice": "5,383.80", "compareToPreviousClosePrice": "-300.00",
                                     "fluctuationsRatio": "-5.28"})
    assert q is not None and q.value == 5383.8 and q.change == -300.0 and q.change_pct == -5.28


def test_parse_mobile_index_empty_is_none() -> None:
    """값이 없으면 0.0 지수를 만들지 않는다(11-05 '코스피 0.0' 사고)."""
    assert parse_mobile_index("KOSPI", {}) is None


def test_parse_mobile_stocks_filters_and_units() -> None:
    data = {"stocks": [
        {"itemCode": "700028", "stockName": "레버리지 ETN", "stockEndType": "etn",
         "closePrice": "88,715", "compareToPreviousClosePrice": "-27,000", "fluctuationsRatio": "-23.33"},
        {"itemCode": "0091C0", "stockName": "신규ETF", "stockEndType": "stock", "closePrice": "1,000"},
        {"itemCode": "353200", "stockName": "대덕전자", "stockEndType": "stock", "closePrice": "80,700",
         "compareToPreviousClosePrice": "-18,800", "fluctuationsRatio": "-18.89",
         "accumulatedTradingVolume": "3,052,670", "accumulatedTradingValue": "253,821",
         "marketValue": "39,879"},
    ]}
    r = parse_mobile_stocks(data, 10, stocks_only=True)
    assert [s.ticker for s in r] == ["353200"]
    s = r[0]
    assert (s.rank, s.price, s.change, s.change_pct) == (1, 80700.0, -18800.0, -18.89)
    assert (s.volume, s.trade_value, s.market_cap) == (3052670, 253821.0, 39879.0)
    assert len(parse_mobile_stocks(data, 10)) == 2  # ETF·ETN 포함(영문 코드는 제외)


def test_parse_mobile_themes() -> None:
    data = {"groups": [{"no": 49, "name": "인터넷 대표주", "changeRate": "9.50"},
                       {"no": 1, "name": "", "changeRate": "1.0"}]}
    assert parse_mobile_themes(data) == [(49, "인터넷 대표주", 9.5)]
