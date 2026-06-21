"""휴장일 안내·스킵 스케줄러 연동 테스트 (사용자 2026-06-19).

휴장일이면 장전 슬롯에서 '휴장 안내' 1회 발송 + 정규 리포트 스킵, 장초/2차/장중은
조용히 스킵. 개장일이면 정상 발송 경로로 진행하는지 검증.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import src.market_report.holiday_calendar as cal
import src.market_report.scheduler as sch
import src.market_report.telegram_notify as tn


class TestUSPremarketHolidayGate:
    async def test_holiday_notice_slot_sends_and_skips(self, monkeypatch) -> None:
        monkeypatch.setattr(cal, "us_holiday_name", lambda d=None: "준틴스데이(Juneteenth)")
        notice = AsyncMock(return_value=True)
        monkeypatch.setattr(tn, "send_holiday_notice", notice)
        run = AsyncMock()
        monkeypatch.setattr("src.market_report.us_premarket.run_us_premarket", run)

        await sch._us_premarket_job(notice=True)

        notice.assert_awaited_once_with("us", "준틴스데이(Juneteenth)")
        run.assert_not_awaited()  # 정규 리포트 스킵

    async def test_holiday_second_slot_silent(self, monkeypatch) -> None:
        monkeypatch.setattr(cal, "us_holiday_name", lambda d=None: "준틴스데이(Juneteenth)")
        notice = AsyncMock()
        monkeypatch.setattr(tn, "send_holiday_notice", notice)
        run = AsyncMock()
        monkeypatch.setattr("src.market_report.us_premarket.run_us_premarket", run)

        await sch._us_premarket_job(notice=False)

        notice.assert_not_awaited()  # 2차 슬롯은 안내 중복 없음
        run.assert_not_awaited()

    async def test_open_day_runs_report(self, monkeypatch) -> None:
        monkeypatch.setattr(cal, "us_holiday_name", lambda d=None: None)
        notice = AsyncMock()
        monkeypatch.setattr(tn, "send_holiday_notice", notice)
        run = AsyncMock(return_value=None)
        monkeypatch.setattr("src.market_report.us_premarket.run_us_premarket", run)

        await sch._us_premarket_job(notice=True)

        notice.assert_not_awaited()
        run.assert_awaited_once()


class TestUSIntradayHolidayGate:
    async def test_holiday_skips(self, monkeypatch) -> None:
        monkeypatch.setattr(cal, "us_holiday_name", lambda d=None: "추수감사절(Thanksgiving)")
        run = AsyncMock()
        monkeypatch.setattr("src.market_report.us_intraday.run_us_intraday", run)

        await sch._us_intraday_job(summer=None)  # DST 게이트 우회

        run.assert_not_awaited()


class TestKRMorningHolidayGate:
    async def test_premarket_sends_notice_and_skips(self, monkeypatch) -> None:
        monkeypatch.setattr(cal, "kr_holiday_name", lambda d=None: "설날")
        notice = AsyncMock(return_value=True)
        monkeypatch.setattr(tn, "send_holiday_notice", notice)
        run = AsyncMock()
        monkeypatch.setattr("src.market_report.kr_morning.run_kr_morning", run)

        await sch._kr_morning_job("kr_premarket")

        notice.assert_awaited_once_with("kr", "설날")
        run.assert_not_awaited()

    async def test_open_slot_silent_skip(self, monkeypatch) -> None:
        monkeypatch.setattr(cal, "kr_holiday_name", lambda d=None: "설날")
        notice = AsyncMock()
        monkeypatch.setattr(tn, "send_holiday_notice", notice)
        run = AsyncMock()
        monkeypatch.setattr("src.market_report.kr_morning.run_kr_morning", run)

        await sch._kr_morning_job("kr_open")

        notice.assert_not_awaited()  # 장초는 안내 없음
        run.assert_not_awaited()


class TestMiddayHolidayGate:
    async def test_holiday_skips(self, monkeypatch) -> None:
        monkeypatch.setattr(cal, "kr_holiday_name", lambda d=None: "어린이날")
        run = AsyncMock()
        monkeypatch.setattr("src.market_report.midday.run_midday", run)

        await sch._midday_job()

        run.assert_not_awaited()
