"""Settings 로드 — KRX 키 등 .env 추가 키가 크래시 없이 수용되는지(회귀 방지).

배경: Settings에 extra 설정이 없으면 pydantic 기본 'forbid'라, .env에 KRX_ID/KRX_PW를
넣었을 때 ValidationError(extra_forbidden)로 get_settings() 전체가 터졌다(2026-06-04).
→ krx_id/krx_pw를 1급 필드로 추가해 해결. 본 테스트가 재발을 막는다.
"""
from __future__ import annotations

from src.config.settings import Settings


def test_settings_accepts_krx_keys() -> None:
    """KRX_ID/KRX_PW가 주어져도 extra_forbidden 없이 로드되고 값이 보존된다."""
    s = Settings(
        _env_file=None,  # 실제 .env 무시(결정론)
        telegram_bot_token="t",
        telegram_allowed_chat_ids="1,2",
        gemini_api_key="g",
        krx_id="clvXXXX",
        krx_pw="pw!@34",
    )
    assert s.krx_id == "clvXXXX"
    assert s.krx_pw == "pw!@34"


def test_settings_krx_optional_defaults_empty() -> None:
    """KRX 키 미설정 시 빈 문자열 기본값(백필 비활성)."""
    s = Settings(
        _env_file=None,
        telegram_bot_token="t",
        telegram_allowed_chat_ids="1",
        gemini_api_key="g",
    )
    assert s.krx_id == ""
    assert s.krx_pw == ""


def test_owner_chat_ids_explicit() -> None:
    """TELEGRAM_OWNER_CHAT_IDS 지정 시 그 집합이 오너(보유종목·자동매수 수신)."""
    s = Settings(
        _env_file=None, telegram_bot_token="t",
        telegram_allowed_chat_ids="111,222,333", gemini_api_key="g",
        telegram_owner_chat_ids="111",
    )
    assert s.owner_chat_ids() == {111}


def test_owner_chat_ids_defaults_to_first_allowed() -> None:
    """미지정 시 allowed의 첫 계정이 오너(맨 첫번째 텔레그램 계정, 사용자 2026-06-14)."""
    s = Settings(
        _env_file=None, telegram_bot_token="t",
        telegram_allowed_chat_ids="999,222,333", gemini_api_key="g",
    )
    assert s.owner_chat_ids() == {999}


def test_owner_web_suffix_default_and_token() -> None:
    s = Settings(_env_file=None, telegram_bot_token="t",
                 telegram_allowed_chat_ids="1", gemini_api_key="g")
    assert s.owner_web_suffix() == "owner"
    s2 = Settings(_env_file=None, telegram_bot_token="t", telegram_allowed_chat_ids="1",
                  gemini_api_key="g", owner_web_token="x9q2")
    assert s2.owner_web_suffix() == "x9q2"
