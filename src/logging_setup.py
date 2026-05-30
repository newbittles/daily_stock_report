"""파일 로깅 설정 — 콘솔 + 회전 파일 핸들러.

logs/app.log: 전체 로그
logs/telegram.log: 텔레그램 발송 전용 (성공/실패 추적)

setup_logging()을 진입점(main.py, 스크립트)에서 1회 호출.
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"

_FMT = "%(asctime)s %(levelname)s %(name)s %(message)s"

# 텔레그램 발송 전용 로거 이름
TELEGRAM_LOGGER = "telegram_send"


def setup_logging(level: int = logging.INFO) -> None:
    """루트 로거 + 텔레그램 전용 로거에 파일 핸들러 부착."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(_FMT)

    root = logging.getLogger()
    root.setLevel(level)

    # 중복 부착 방지
    existing = {type(h).__name__ + getattr(h, "baseFilename", "") for h in root.handlers}

    # 콘솔
    if "StreamHandler" not in {type(h).__name__ for h in root.handlers}:
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(console)

    # 전체 파일 로그 (5MB × 3)
    app_log = str(LOG_DIR / "app.log")
    if "RotatingFileHandler" + app_log not in existing:
        fh = RotatingFileHandler(app_log, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)

    # 텔레그램 전용 로거 (별도 파일)
    tg_logger = logging.getLogger(TELEGRAM_LOGGER)
    tg_log = str(LOG_DIR / "telegram.log")
    tg_attached = any(
        getattr(h, "baseFilename", "") == tg_log for h in tg_logger.handlers
    )
    if not tg_attached:
        tg_fh = RotatingFileHandler(tg_log, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        tg_fh.setFormatter(fmt)
        tg_logger.addHandler(tg_fh)
        tg_logger.setLevel(logging.INFO)
        # 루트로도 전파 (콘솔에도 보이게)
        tg_logger.propagate = True


def get_telegram_logger() -> logging.Logger:
    return logging.getLogger(TELEGRAM_LOGGER)
