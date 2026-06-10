"""리포트 일관성 자동 점검 (사용자 2026-06-10).

KR/US 리포트 간 '있어야 할 섹션이 빠졌는지(드리프트)'를 매일 1회 점검해 텔레그램으로 '확인 요청'
알림만 보낸다(자동 코드수정 X — 무인 코드수정은 리포트를 깨뜨릴 위험). 발견 시 사람이 수정 적용.

핵심: **시점별 의도된 차이를 매트릭스로 기억**해 오탐 방지.
  - kr_premarket(프리장): 한국지수 카드·AI요약·강세테마 미표시(의도) / 미국야간·프리장테마·NXT 표시
  - us_*(미국): 종목 미국전용 — KR 지수 섹션('주요 지수' 헤더·KOSPI 카드)이 새면 analyze 라우팅 버그
  - pre/post(마감전후): E·F·G 참고섹션은 항상 노출(없으면 '없음'), AI 시장요약 항상

오탐을 피하려고 REQUIRED는 '데이터 유무와 무관하게 구조적으로 항상 렌더되는'(mode-gated) 마커만 넣는다.
강세테마·Top3·핫종목·보유종목 등 데이터 의존 섹션은 비는 날이 정상이라 제외.
"""
from __future__ import annotations

import logging
import re

from src.market_report.render import REPORTS_DIR

logger = logging.getLogger(__name__)

# mode → 파일 슬러그 (render.py와 동일)
_SLUG = {
    "pre_close": "pre", "post_close": "post", "us_morning": "us", "midday": "midday",
    "us_premarket": "us-pre", "us_afterhours": "us-after", "us_intraday": "us-mid",
    "kr_premarket": "kr-pre", "kr_open": "kr-open",
}

# mode → 한글 라벨 (텔레그램 Markdown에서 'pre_close'의 '_'가 이탤릭으로 깨지는 것 방지 + 가독성)
_LABEL = {
    "pre_close": "마감전", "post_close": "마감후", "midday": "장중", "kr_open": "장초",
    "kr_premarket": "프리장", "us_morning": "미국마감", "us_afterhours": "미국애프터",
    "us_premarket": "미국프리장", "us_intraday": "미국장중",
}

_DISCLAIMER = "책임은"  # 모든 리포트 공통 면책

# 구조적으로 항상 있어야 하는 섹션/문구 마커(빠지면 드리프트/버그). 데이터 의존 섹션은 넣지 않는다.
REQUIRED: dict[str, list[str]] = {
    "pre_close":     ["E 투매 바닥 반등", "F. 60일선 지지", "삼각수렴 임박", "AI 시장 요약"],
    "post_close":    ["E 투매 바닥 반등", "F. 60일선 지지", "삼각수렴 임박", "AI 시장 요약"],
    "midday":        ["AI 시장 요약", "주요 지수"],
    "kr_open":       ["AI 시장 요약", "주요 지수"],
    "kr_premarket":  [],  # 대부분 데이터 의존/의도적 미표시 → forbidden + 면책으로만 점검
    "us_morning":    ["E 투매 바닥 반등", "F. 60일선 지지", "삼각수렴 임박"],
    "us_afterhours": ["E 투매 바닥 반등", "F. 60일선 지지", "삼각수렴 임박"],
    "us_premarket":  ["E 투매 바닥 반등", "F. 60일선 지지", "삼각수렴 임박"],
    "us_intraday":   ["E 투매 바닥 반등", "F. 60일선 지지", "삼각수렴 임박"],
}

# 절대 나오면 안 되는 마커(라우팅/의도 위반). US에 '코스피'는 시사점 텍스트로 합법 출현 → 쓰지 않음.
FORBIDDEN: dict[str, list[str]] = {
    "kr_premarket":  ["KOSPI", "KOSDAQ"],          # 프리장은 한국지수 카드 미표시(의도)
    "us_morning":    ["주요 지수", "KOSPI"],         # US에 KR 지수 섹션이 새면 analyze 라우팅 버그
    "us_afterhours": ["주요 지수", "KOSPI"],
    "us_premarket":  ["주요 지수", "KOSPI"],
    "us_intraday":   ["주요 지수", "KOSPI"],
}

# 의도된 시점별 차이(알림 본문에 근거로 첨부 — 사용자가 '왜 다른지' 바로 알도록)
INTENTIONAL = {
    "kr_premarket": "프리장(의도): 한국지수·AI요약·강세테마 없음 / 미국야간·프리장테마·NXT상승하락 있음",
    "us_afterhours": "미국 애프터(의도): 미국 마감 구조 재사용(us_morning과 동일 섹션)",
}


def audit_html(mode: str, html: str) -> list[str]:
    """단일 리포트 HTML을 매트릭스로 점검 → 위반 사유 리스트(순수 함수, I/O 없음)."""
    issues: list[str] = []
    for mark in REQUIRED.get(mode, []):
        if mark not in html:
            issues.append(f"필수 섹션 누락: '{mark}'")
    for mark in FORBIDDEN.get(mode, []):
        if mark in html:
            issues.append(f"금지 마커 출현(라우팅/의도 위반): '{mark}'")
    if _DISCLAIMER not in html:
        issues.append("면책 문구 누락")
    return issues


def _latest_report(slug: str) -> tuple[str, str] | None:
    """슬러그의 최신 발행 리포트 (파일명, HTML). 정확한 'YYYY-MM-DD-<slug>.html'만 매칭."""
    pat = re.compile(rf"^\d{{4}}-\d{{2}}-\d{{2}}-{re.escape(slug)}\.html$")
    files = sorted((p for p in REPORTS_DIR.glob("*.html") if pat.match(p.name)), reverse=True)
    if not files:
        return None
    return files[0].name, files[0].read_text(encoding="utf-8")


def audit_reports() -> list[str]:
    """모든 모드 최신 리포트 점검 → 발견 사항 리스트(모드별 위반)."""
    findings: list[str] = []
    for mode, slug in _SLUG.items():
        got = _latest_report(slug)
        if got is None:
            continue  # 아직 발행 안 된 모드는 점검 생략(오탐 방지)
        fname, html = got
        issues = audit_html(mode, html)
        if issues:
            findings.append(f"📄 {_LABEL.get(mode, mode)} ({fname})\n  - " + "\n  - ".join(issues))
    return findings


def format_alert(findings: list[str]) -> str:
    """텔레그램 알림 본문 — 발견 사항 + 의도된 차이 안내."""
    date = __import__("datetime").datetime.now().strftime("%m-%d %H:%M")
    if not findings:
        return f"🔍 *리포트 일관성 점검* — {date}\n✅ 이상 없음 (모든 리포트 섹션 정상)"
    lines = [f"🔍 *리포트 일관성 점검* — {date}", f"⚠️ {len(findings)}개 리포트에서 드리프트 의심:", ""]
    lines += findings
    lines += ["", "_자동 수정은 하지 않습니다(코드 안정성). 위 항목 확인 후 알려주시면 수정 적용하겠습니다._"]
    return "\n".join(lines)


async def run_report_audit(*, always_notify: bool = True) -> list[str]:
    """점검 실행 + (always_notify면 결과 무관, 아니면 발견 시만) 텔레그램 알림. 발견 사항 반환."""
    findings = audit_reports()
    logger.info("report_audit_done findings=%d", len(findings))
    if not (always_notify or findings):
        return findings
    try:
        from telegram import Bot

        from src.config.settings import get_settings
        from src.market_report.telegram_notify import TelegramNotifier
        settings = get_settings()
        chat_ids = settings.allowed_chat_ids()
        if not chat_ids:
            return findings
        notifier = TelegramNotifier(bot=Bot(token=settings.telegram_bot_token))
        text = format_alert(findings)
        for cid in chat_ids:
            await notifier.send(str(cid), text)
        logger.info("report_audit_alert_sent findings=%d chats=%d", len(findings), len(chat_ids))
    except Exception as exc:  # noqa: BLE001
        logger.warning("report_audit_alert_failed error=%s", exc)
    return findings
