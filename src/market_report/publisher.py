"""GitHub Pages 자동 퍼블리셔 — docs/ 변경분을 git push.

Pipeline 끝에서 호출되어 새 리포트와 차트를 GitHub Pages에 자동 게시.
실패해도 파이프라인 전체는 멈추지 않음 (텔레그램 발송 등은 계속 진행).
"""
from __future__ import annotations

import logging
import subprocess
from datetime import datetime
from pathlib import Path

from src.market_report.models import MarketSnapshot

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
GITHUB_PAGES_BASE = "https://newbittles.github.io/daily_stock_report"


def report_url(snap: MarketSnapshot) -> str:
    """배포된 리포트의 절대 URL."""
    date = snap.generated_at.strftime("%Y-%m-%d")
    suffix = {"pre_close": "pre", "post_close": "post", "us_morning": "us"}.get(snap.mode, "post")
    return f"{GITHUB_PAGES_BASE}/reports/{date}-{suffix}.html"


def _run_git(*args: str, timeout: int = 60) -> tuple[bool, str]:
    """git 명령 실행. (성공여부, stderr 또는 stdout) 반환."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return False, result.stderr.strip() or result.stdout.strip()
        return True, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, f"git {args[0]} timeout"
    except Exception as exc:
        return False, str(exc)


def publish(snap: MarketSnapshot) -> bool:
    """docs/ 변경분 git push. 성공 여부 반환.

    동작:
      1. git add docs/
      2. 변경 없으면 → 푸시 스킵, True 반환
      3. commit -m "📊 {date} {mode} report"
      4. push
    """
    date = snap.generated_at.strftime("%Y-%m-%d")
    time = snap.generated_at.strftime("%H:%M")
    mode_label = {"pre_close": "마감 전", "post_close": "마감 후",
                  "us_morning": "미국 아침"}.get(snap.mode, "마감 후")

    # 1. add
    ok, msg = _run_git("add", "docs/")
    if not ok:
        logger.error("publish_add_failed error=%s", msg)
        return False

    # 2. staged 변경 확인
    ok, msg = _run_git("diff", "--cached", "--quiet")
    # --quiet는 변경 있으면 exit 1, 없으면 exit 0
    if ok:
        logger.info("publish_no_changes — 변경 없음, 스킵")
        return True

    # 3. commit
    commit_msg = (
        f"📊 {date} {mode_label} 리포트 ({time})\n\n"
        f"자동 생성: market_report pipeline"
    )
    ok, msg = _run_git("commit", "-m", commit_msg)
    if not ok:
        logger.error("publish_commit_failed error=%s", msg)
        return False

    # 4. push
    ok, msg = _run_git("push", "origin", "main", timeout=120)
    if not ok:
        logger.error("publish_push_failed error=%s", msg)
        return False

    logger.info("published mode=%s url=%s", snap.mode, report_url(snap))
    return True
