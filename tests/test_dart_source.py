"""DART 공시 조회 — 키없음/None-[] 시맨틱 검증(환각 방지 핵심). 네트워크 없이.

⚠️ 핵심 계약: 조회 성공+0건=[]('없음'), 매핑없음/실패=None('확인 불가'). 둘을 섞으면
'없었는데 있다'/'있었는데 없다' 환각이 나므로 호출측이 None vs []를 구분해야 한다.
"""
from __future__ import annotations

import asyncio

from src.config.settings import Settings
from src.datasource.dart import fetch_recent_disclosures


def test_dart_no_key_returns_none() -> None:
    """키 없으면 None(='확인 불가') — 절대 []('없음')로 오인하지 않음."""
    assert asyncio.run(fetch_recent_disclosures("005930", key="")) is None


def test_material_disclosure_filter() -> None:
    """주요공시(수주·증자·실적·M&A·투자)만 유지, 루틴(지분/임원/지배구조/대기업집단)은 제외(allowlist, 사용자 2026-06-09)."""
    from src.datasource.dart.source import _is_material

    # 주요공시(유지)
    assert _is_material("단일판매ㆍ공급계약체결")   # 수주
    assert _is_material("유상증자결정")
    assert _is_material("영업(잠정)실적(공정공시)")
    assert _is_material("주요사항보고서(자기주식취득결정)")
    assert _is_material("타법인주식및출자증권취득결정")
    # 루틴(제외)
    assert not _is_material("최대주주등소유주식변동신고서")
    assert not _is_material("임원ㆍ주요주주특정증권등소유상황보고서")
    assert not _is_material("주식등의대량보유상황보고서(약식)")
    assert not _is_material("대규모기업집단현황공시[연1회(동일인용)]")
    assert not _is_material("기업지배구조보고서공시")


def test_settings_has_dart_field() -> None:
    """pydantic 필드 존재(미설정 기본 빈문자열) — .env에 키 넣어도 forbid 크래시 없음."""
    assert "dart_api_key" in Settings.model_fields
    assert Settings.model_fields["dart_api_key"].default == ""
