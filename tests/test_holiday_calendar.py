"""휴장 달력(holiday_calendar) 순수 함수 테스트.

매년 1회 갱신하는 하드코딩 테이블의 정확성을 결정론적으로 검증한다(사용자 2026-06-19).
"""
from __future__ import annotations

from datetime import date

from src.market_report.holiday_calendar import (
    kr_holiday_name,
    us_holiday_name,
)


class TestUSHolidays:
    def test_juneteenth_2026(self) -> None:
        # 2026-06-19(금) = 준틴스데이 → 미국 증시 휴장
        assert us_holiday_name(date(2026, 6, 19)) is not None
        assert "준틴스" in us_holiday_name(date(2026, 6, 19))

    def test_new_year_2026(self) -> None:
        assert us_holiday_name(date(2026, 1, 1)) is not None

    def test_independence_day_observed_2026(self) -> None:
        # 7/4(토) → 7/3(금) 대체 휴장
        assert us_holiday_name(date(2026, 7, 3)) is not None
        assert us_holiday_name(date(2026, 7, 4)) is None  # 토요일 자체는 테이블에 없음

    def test_normal_trading_day_is_none(self) -> None:
        # 2026-06-18(목)은 정상 거래일
        assert us_holiday_name(date(2026, 6, 18)) is None

    def test_thanksgiving_and_christmas(self) -> None:
        assert us_holiday_name(date(2026, 11, 26)) is not None
        assert us_holiday_name(date(2026, 12, 25)) is not None


class TestKRHolidays:
    def test_new_year_2026(self) -> None:
        assert kr_holiday_name(date(2026, 1, 1)) is not None
        assert "신정" in kr_holiday_name(date(2026, 1, 1))

    def test_seollal_2026(self) -> None:
        # 설날 연휴 2/16~2/18
        for d in (16, 17, 18):
            assert kr_holiday_name(date(2026, 2, d)) is not None

    def test_substitute_holiday_2026(self) -> None:
        # 삼일절(3/1 일) 대체 → 3/2(월)
        assert kr_holiday_name(date(2026, 3, 2)) is not None
        # 광복절(8/15 토) 대체 → 8/17(월)
        assert kr_holiday_name(date(2026, 8, 17)) is not None

    def test_labor_day_2026(self) -> None:
        # 근로자의 날(5/1) — KRX 휴장
        assert kr_holiday_name(date(2026, 5, 1)) is not None

    def test_year_end_2026(self) -> None:
        # 연말 휴장(12/31)
        assert kr_holiday_name(date(2026, 12, 31)) is not None

    def test_normal_trading_day_is_none(self) -> None:
        # 2026-06-19(금)은 한국 정상 거래일(미국만 휴장)
        assert kr_holiday_name(date(2026, 6, 19)) is None


class TestDefaultArg:
    def test_none_uses_today_without_error(self) -> None:
        # 인자 없이 호출 시 오늘 날짜 기준으로 동작(예외 없이 str|None 반환)
        assert kr_holiday_name() in (None,) or isinstance(kr_holiday_name(), str)
        assert us_holiday_name() in (None,) or isinstance(us_holiday_name(), str)
