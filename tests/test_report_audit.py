"""리포트 일관성 자동 점검 — 매트릭스 로직 검증 (사용자 2026-06-10)."""
from __future__ import annotations

from src.market_report.report_audit import audit_html, format_alert


def test_flags_missing_required_section() -> None:
    """마감후에 F 섹션이 빠지면 누락으로 플래그(오늘 F 사고 재발 방지)."""
    html = "E 투매 바닥 반등 ... 삼각수렴 임박 ... AI 시장 요약 ... 판단·책임은 본인"
    issues = audit_html("post_close", html)
    assert any("F. 60일선 지지" in i for i in issues)


def test_passes_when_all_required_present() -> None:
    html = ("E 투매 바닥 반등 F. 60일선 지지 삼각수렴 임박 수급 주도 AI 시장 요약 "
            "책임은 본인에게 있습니다")
    assert audit_html("post_close", html) == []


def test_flags_us_routing_leak() -> None:
    """미국 리포트에 KR 지수 섹션('주요 지수' 헤더)이 새면 라우팅 버그로 플래그."""
    html = "강세 섹터 E 투매 바닥 반등 주요 지수 ... 책임은 본인"
    issues = audit_html("us_morning", html)
    assert any("주요 지수" in i for i in issues)


def test_us_allows_codespi_in_commentary() -> None:
    """미국 리포트에 '코스피'(시사점 텍스트)는 합법 — 오탐 내면 안 됨."""
    html = ("강세 섹터 E 투매 바닥 반등 F. 60일선 지지 삼각수렴 임박 "
            "한국장 시사점: 코스피 주목 ... 책임은 본인")
    assert audit_html("us_morning", html) == []


def test_kr_premarket_forbids_kospi_card() -> None:
    """프리장은 한국지수 카드 미표시(의도) — KOSPI 카드가 있으면 플래그."""
    html = "프리장 소속 테마 ... KOSPI 7,800 ... 판단·책임은 본인"
    assert any("KOSPI" in i for i in audit_html("kr_premarket", html))


def test_flags_missing_disclaimer() -> None:
    html = "E 투매 바닥 반등 F. 60일선 지지 삼각수렴 임박 AI 시장 요약"
    assert any("면책" in i for i in audit_html("pre_close", html))


def test_format_alert_clear_and_findings() -> None:
    assert "이상 없음" in format_alert([])
    out = format_alert(["📄 마감후 (x.html)\n  - 필수 섹션 누락: 'F. 60일선 지지'"])
    assert "드리프트" in out and "F. 60일선 지지" in out
    assert "자동 수정은 하지 않습니다" in out
