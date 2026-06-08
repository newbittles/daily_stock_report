"""스케줄러 잡 등록 검증 — 미국 캐시 워밍 잡(#499) 포함 여부."""
from __future__ import annotations


def test_warm_us_cache_jobs_registered() -> None:
    """캐시 워밍 잡 2개(화~토 06:00 / 월 18:30)가 등록돼야 한다(#499)."""
    from src.market_report.scheduler import build_scheduler
    jobs = {j.id: j for j in build_scheduler().get_jobs()}
    assert "warm_us_cache_am" in jobs
    assert "warm_us_cache_pm" in jobs


def test_warm_us_cache_before_us_reports() -> None:
    """워밍(06:00)이 미국 아침 리포트(06:30)보다 먼저여야 캐시 히트 가능."""
    from src.market_report.scheduler import build_scheduler
    jobs = {j.id: str(j.trigger) for j in build_scheduler().get_jobs()}
    assert "hour='6'" in jobs["warm_us_cache_am"] and "minute='0'" in jobs["warm_us_cache_am"]
    assert "hour='18'" in jobs["warm_us_cache_pm"] and "minute='30'" in jobs["warm_us_cache_pm"]


def test_warm_us_cache_importable() -> None:
    """워밍 함수가 pipeline에 존재·import 가능(잡이 호출)."""
    from src.market_report.pipeline import warm_us_cache
    assert callable(warm_us_cache)
