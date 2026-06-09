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


def test_settings_has_dart_field() -> None:
    """pydantic 필드 존재(미설정 기본 빈문자열) — .env에 키 넣어도 forbid 크래시 없음."""
    assert "dart_api_key" in Settings.model_fields
    assert Settings.model_fields["dart_api_key"].default == ""
