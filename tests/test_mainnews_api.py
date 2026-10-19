"""네이버 주요뉴스 JSON 파서 + HTTP 세션 리셋/키워드 오탐 방지 (2026-10-20)."""
import httpx

from src.market_report import http as mh
from src.market_report.scrapers.news import _parse_mainnews_api


def test_parse_mainnews_api() -> None:
    data = {"result": [
        {"title": "짧은", "titleFull": "하이닉스 실적 쇼크", "officeId": "009", "articleId": "0001",
         "officeName": "매일경제", "datetime": "20261019234306"},
        {"title": "하이닉스 실적 쇼크", "officeId": "008", "articleId": "0002"},   # 제목 중복
        {"title": "링크 없음", "officeId": "", "articleId": "0003"},
    ]}
    out = _parse_mainnews_api(data, 10)
    assert len(out) == 1
    assert out[0].url == "https://n.news.naver.com/article/009/0001"
    assert out[0].source == "매일경제"


def test_single_word_does_not_trip_hard_stop() -> None:
    r = httpx.Response(200, text="불법 광고 차단 · robots.txt · captchaApi 설정")
    assert mh._is_hard_stop(r) == (False, "")
    r2 = httpx.Response(200, text="비정상적인 트래픽이 감지되었습니다")
    assert mh._is_hard_stop(r2)[0] is True


def test_reset_session_clears_halt() -> None:
    mh._stats.halted = True
    mh._stats.halt_reason = "keyword: x"
    mh.reset_session()
    assert mh.get_stats().halted is False
