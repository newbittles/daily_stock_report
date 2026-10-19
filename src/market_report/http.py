"""안전한 HTTP fetch 헬퍼.

전역 CLAUDE.md §7 적용:
- 랜덤 딜레이 (요청 간, 배치 간)
- 최대 재시도 3회 + 지수 백오프
- HARD STOP: HTTP 429, 401/403, CAPTCHA → 즉시 중단
- 세션 상태 추적

모든 스크래퍼는 이 모듈의 `fetch()`를 사용한다.
"""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

# 딜레이 (초)
DELAY_REQUEST = (1.0, 3.0)   # 요청 사이
DELAY_BATCH = (5.0, 10.0)    # 10개 요청마다
DELAY_SESSION = (30.0, 60.0)  # 50개 요청마다

MAX_RETRY = 3

HARD_STOP_CODES = {429, 423, 503}
# 🔴 단어 하나짜리('차단'·'robot'·'captcha')는 넣지 않는다 — 정상 페이지 본문·설정 JSON에 흔해
# 오탐이 난다(2026-07-06 종목페이지 '차단' 오탐 → 상주 프로세스 세션이 석 달간 정지, 2026-10-20 발견).
# 사람에게 보여 주는 봇감지 '문장'만 쓴다(전역 rules/external-services.md §0-1).
HARD_STOP_KEYWORDS = (
    "unusual traffic", "unusual activity", "are you a robot",
    "automated queries", "비정상적인 트래픽", "보안문자를 입력",
)


class HardStop(Exception):
    """Defender·rate limit·CAPTCHA 등 자동 재시도 금지 신호."""


@dataclass
class SessionStats:
    success: int = 0
    failure: int = 0
    last_success_url: str = ""
    halted: bool = False
    halt_reason: str = ""


_stats = SessionStats()


def get_stats() -> SessionStats:
    return _stats


def reset_session() -> None:
    """새 리포트 런 시작 시 세션 상태 초기화.

    상주 스케줄러(run_forever)에서 한 번 걸린 HARD STOP이 프로세스 수명 내내 남아 이후 모든
    리포트의 스크래핑이 막히던 문제(2026-10-20) 방지. 같은 런 안에서는 정지가 그대로 유지된다
    (자동 재시도 아님 — 다음 정규 스케줄에서만 새로 시도)."""
    global _stats
    if _stats.halted:
        logger.warning("http_session_reset — 이전 런 HARD STOP 해제 (reason=%s)", _stats.halt_reason)
    _stats = SessionStats()


def _is_hard_stop(resp: httpx.Response) -> tuple[bool, str]:
    if resp.status_code in HARD_STOP_CODES:
        return True, f"HTTP {resp.status_code}"
    try:
        content_lower = resp.text.lower()
    except Exception:
        return False, ""
    for kw in HARD_STOP_KEYWORDS:
        if kw in content_lower:
            return True, f"keyword: {kw}"
    return False, ""


async def _delay(low: float, high: float) -> None:
    await asyncio.sleep(random.uniform(low, high))


async def fetch(
    url: str,
    *,
    encoding: str = "utf-8",
    timeout: float = 10.0,
    extra_headers: dict[str, str] | None = None,
) -> str:
    """안전 fetch — 재시도·딜레이·HARD STOP 포함.

    Returns: 디코딩된 HTML 문자열. 실패 시 HardStop 또는 httpx 예외 raise.
    """
    if _stats.halted:
        raise HardStop(f"세션 중단됨: {_stats.halt_reason}")

    headers = {**DEFAULT_HEADERS, **(extra_headers or {})}

    last_exc: Exception | None = None
    for attempt in range(MAX_RETRY):
        if attempt > 0:
            wait = random.uniform(5 * (2 ** (attempt - 1)), 10 * (2 ** (attempt - 1)))
            logger.info("http_retry url=%s attempt=%d wait=%.1fs", url, attempt, wait)
            await asyncio.sleep(wait)
        else:
            await _delay(*DELAY_REQUEST)

        try:
            async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True) as client:
                resp = await client.get(url)

            # HARD STOP 검사
            stop, reason = _is_hard_stop(resp)
            if stop:
                _stats.halted = True
                _stats.halt_reason = reason
                logger.error("http_hard_stop url=%s reason=%s", url, reason)
                raise HardStop(f"{url}: {reason}")

            resp.raise_for_status()
            html = resp.content.decode(encoding, errors="ignore")
            _stats.success += 1
            _stats.last_success_url = url
            return html

        except HardStop:
            raise
        except Exception as exc:
            last_exc = exc
            _stats.failure += 1
            logger.warning("http_error url=%s attempt=%d error=%s", url, attempt, exc)

    raise RuntimeError(f"fetch failed after {MAX_RETRY} attempts: {url}") from last_exc


async def batch_pause_if_needed(count: int) -> None:
    """N번째 요청마다 휴식."""
    if count > 0 and count % 10 == 0:
        await _delay(*DELAY_BATCH)
    if count > 0 and count % 50 == 0:
        logger.info("session_pause count=%d — 휴식 중", count)
        await _delay(*DELAY_SESSION)
