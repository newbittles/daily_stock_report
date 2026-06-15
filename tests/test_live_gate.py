"""실전매매 런타임 게이트 — 기본 OFF, 사용자가 명시적으로 켠 순간부터만 ON."""
from __future__ import annotations

from src.trading.live_gate import disable_live, enable_live, is_live_enabled


def test_gate_default_off(tmp_path):
    flag = tmp_path / "live.flag"
    assert is_live_enabled(flag) is False  # 파일 없음 = OFF (안전 기본값)


def test_gate_enable_disable(tmp_path):
    flag = tmp_path / "live.flag"
    enable_live(flag)
    assert is_live_enabled(flag) is True
    disable_live(flag)
    assert is_live_enabled(flag) is False  # 끄면 다시 OFF


def test_gate_garbage_content_is_off(tmp_path):
    flag = tmp_path / "live.flag"
    flag.write_text("maybe", encoding="utf-8")
    assert is_live_enabled(flag) is False  # 'on'이 아니면 OFF (오작동 방지)
