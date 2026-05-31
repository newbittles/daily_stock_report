"""전략 스크린 대시보드 생성 (A/B/C·일자별 + 차트 시그널).

사용법: python scripts/build_dashboard.py [days_back] [end_date]
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.datasource.kis.adapter import KisAdapter
from src.market_report.screen_dashboard import build_dashboard


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    end = sys.argv[2] if len(sys.argv) > 2 else None
    s = get_settings()
    a = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)
    path = await build_dashboard(a, days_back=days, end_date=end)
    print(f"\n대시보드 생성 완료: {path}")


if __name__ == "__main__":
    asyncio.run(main())
