"""kr_premarket 드라이런 — 발송·퍼블리시 없이 텔레그램 포맷만 출력(#469 검증용)."""
import asyncio
import logging
import sys

sys.path.insert(0, ".")
logging.basicConfig(level=logging.WARNING)


async def main() -> None:
    from src.market_report.kr_morning import run_kr_morning
    from src.market_report.telegram_notify import _format_kr_morning_summary

    snap = await run_kr_morning("kr_premarket", do_telegram=False, do_publish=False, force=True)
    if snap is None:
        print("휴장일 — 스킵")
        return
    print(_format_kr_morning_summary(snap))


asyncio.run(main())
