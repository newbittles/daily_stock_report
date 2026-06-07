"""로그 파일 내용을 텔레그램(allowed_chat_ids)으로 발송 — cron 단발 검증용.

사용: python scripts/notify_log.py <로그경로> [제목]
auto_trader dry-run처럼 알림 없는 잡의 출력을 사람이 원격 확인할 때 사용(2026-06-07).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def main() -> int:
    from telegram import Bot

    from src.config.settings import get_settings
    if len(sys.argv) < 2:
        print("usage: notify_log.py <logfile> [title]")
        return 1
    path = Path(sys.argv[1])
    title = sys.argv[2] if len(sys.argv) > 2 else "📄 로그"
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    text = text[-3500:]  # 텔레그램 4096자 제한 여유
    s = get_settings()
    bot = Bot(token=s.telegram_bot_token)
    for cid in s.allowed_chat_ids():
        await bot.send_message(chat_id=cid, text=f"{title}\n{text or '(빈 로그)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
