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
    suffix = {"pre_close": "pre", "post_close": "post", "us_morning": "us",
              "midday": "midday", "us_premarket": "us-pre",
              "us_intraday": "us-mid", "kr_premarket": "kr-pre", "kr_open": "kr-open"}.get(snap.mode, "post")
    return f"{GITHUB_PAGES_BASE}/reports/{date}-{suffix}.html"


def _run_git(*args: str, timeout: int = 60) -> tuple[bool, str]:
    """git 명령 실행. (성공여부, stderr 또는 stdout) 반환."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",   # git 출력(한글 커밋/파일명)을 OS 기본(cp949) 아닌 UTF-8로 디코드
            errors="replace",   # 디코드 불가 바이트도 예외 없이 처리 (Windows 발송 중단 방지)
            timeout=timeout,
        )
        if result.returncode != 0:
            return False, result.stderr.strip() or result.stdout.strip()
        return True, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, f"git {args[0]} timeout"
    except Exception as exc:
        return False, str(exc)


def publish_docs(commit_msg: str) -> bool:
    """docs/ 변경분 git push — 스냅샷 없는 독립 리포트용(코인 등). publish()와 동일 4단계.

    기존 publish(snap)는 주식 스냅샷 전용으로 불변 유지(2026-06-07)."""
    ok, msg = _run_git("add", "-A", "docs/")
    if not ok:
        logger.error("publish_docs_add_failed error=%s", msg)
        return False
    ok, _ = _run_git("diff", "--cached", "--quiet")  # 변경 있으면 exit 1
    if ok:
        logger.info("publish_docs_no_changes — 변경 없음, 스킵")
        return True
    ok, msg = _run_git("commit", "-m", f"{commit_msg}\n\n자동 생성: coin_report")
    if not ok:
        logger.error("publish_docs_commit_failed error=%s", msg)
        return False
    ok, msg = _run_git("pull", "--rebase", "--autostash", "origin", "main", timeout=120)
    if not ok:
        logger.error("publish_docs_pull_rebase_failed error=%s", msg)
        _run_git("rebase", "--abort")
        return False
    ok, msg = _run_git("push", "origin", "main", timeout=120)
    if not ok:
        logger.error("publish_docs_push_failed error=%s", msg)
        return False
    logger.info("publish_docs_done msg=%s", commit_msg)
    return True


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
                  "us_morning": "미국 아침", "us_premarket": "미국 장전",
                  "us_intraday": "미국 장중", "midday": "장중", "kr_premarket": "한국 프리", "kr_open": "한국 장초"}.get(snap.mode, "마감 후")

    # 1. add
    ok, msg = _run_git("add", "-A", "docs/")  # -A: 삭제(오래된 차트)도 스테이징
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

    # 4. 원격 동기화 (rebase) — 서버 자동발행과 개발 머신 푸시가 같은 main을 공유하므로,
    #    push 전에 origin/main에 리베이스하지 않으면 non-fast-forward로 거부되어 로컬
    #    리포트 커밋이 쌓이며 분기가 누적된다(2026-06 서버 ahead/behind 52 원인).
    #    --autostash: 서버의 미커밋 로컬설정(config/screener.yaml 등)을 자동 보존.
    ok, msg = _run_git("pull", "--rebase", "--autostash", "origin", "main", timeout=120)
    if not ok:
        logger.error("publish_pull_rebase_failed error=%s", msg)
        _run_git("rebase", "--abort")  # 충돌 시 리베이스 상태 정리(다음 발행을 막지 않도록)
        return False

    # 5. push
    ok, msg = _run_git("push", "origin", "main", timeout=120)
    if not ok:
        logger.error("publish_push_failed error=%s", msg)
        return False

    logger.info("published mode=%s url=%s", snap.mode, report_url(snap))
    return True
