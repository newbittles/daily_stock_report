"""한국장 개장일 판정 — KIS 일봉 최신 거래일 == 오늘 여부.

별도 휴일 목록을 유지하지 않는다(자가유지). KIS는 휴장일(주말·공휴일·임시공휴일·
선거일)에는 당일 일봉을 생성하지 않으므로, 최신 일봉의 거래일이 오늘이 아니면
오늘은 휴장으로 판정한다. (미국장 us_morning 신선도 스킵과 동일한 사상)

판정 불가(KIS 오류·빈 응답) 시 True(fail-open) — '거래일 오발송 차단'보다
'정상 거래일 미발송'을 더 큰 사고로 보고 발송을 막지 않는다.

근거: get_ohlcv(inquire-daily-itemchartprice)는 장중에도 당일 형성봉을 포함한다
(screener가 14:40 pre_close에 candles[-1]로 당일 등락률을 계산하는 것과 동일).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


async def is_kr_market_open_today(adapter: Any | None = None, *, probe_ticker: str = "005930") -> bool:
    """오늘 한국장 개장 여부. 휴장이면 False → 호출측에서 리포트 발송 스킵.

    adapter: 재사용할 KisAdapter. None이면 일시 생성·종료.
    probe_ticker: 일봉 조회 기준 종목 (기본 삼성전자 — 항상 거래되는 대형주).
    """
    today = datetime.now().strftime("%Y%m%d")
    own = adapter is None
    try:
        if own:
            from src.config.settings import get_settings
            from src.datasource.kis.adapter import KisAdapter
            s = get_settings()
            adapter = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)

        candles = await adapter.get_ohlcv(probe_ticker, days=5)
        if not candles:
            logger.warning("kr_calendar_probe_empty — fail-open(발송 진행)")
            return True
        latest = candles[-1].date
        is_open = latest == today
        logger.info("kr_calendar latest=%s today=%s open=%s", latest, today, is_open)
        return is_open
    except Exception as exc:  # noqa: BLE001
        logger.warning("kr_calendar_check_failed error=%s — fail-open(발송 진행)", exc)
        return True
    finally:
        if own and adapter is not None:
            try:
                await adapter.close()
            except Exception:  # noqa: BLE001
                pass
