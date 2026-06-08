"""종목 → 시장 라벨 매핑 (#471) — 정규화·조회·캐시 폴백."""
from __future__ import annotations

import json

import pytest

from src.datasource import market_map as mm


@pytest.fixture(autouse=True)
def _reset_maps():
    """전역 싱글턴(_MAPS) 격리 — 다른 테스트의 label_any 출력 오염 방지."""
    yield
    mm._MAPS = None

MAPS = {
    "kr": {"000660": "코스피", "247540": "코스닥"},
    "us": {"NVDA": "나스닥", "BRKB": "NYSE"},
}


def test_norm_key_absorbs_separator_variants() -> None:
    assert mm.norm_key("BRK-B") == "BRKB"
    assert mm.norm_key("brk.b") == "BRKB"
    assert mm.norm_key(" NVDA ") == "NVDA"
    assert mm.norm_key("") == ""


def test_label_from_maps_kr_us_branching() -> None:
    assert mm.label_from_maps("000660", MAPS) == "코스피"   # 하이닉스 → 코스피
    assert mm.label_from_maps("247540", MAPS) == "코스닥"
    assert mm.label_from_maps("NVDA", MAPS) == "나스닥"
    assert mm.label_from_maps("BRK-B", MAPS) == "NYSE"      # 표기차 흡수
    assert mm.label_from_maps("999999", MAPS) == ""          # 미발견 → 생략
    assert mm.label_from_maps("", MAPS) == ""


def test_ensure_maps_uses_today_cache(tmp_path, monkeypatch) -> None:
    from datetime import date
    p = tmp_path / "market_map.json"
    p.write_text(json.dumps({"date": date.today().isoformat(), **MAPS}), encoding="utf-8")

    def _boom():  # 캐시 히트면 빌드(네트워크) 안 탐
        raise AssertionError("should not build")

    monkeypatch.setattr(mm, "_build_maps", _boom)
    maps = mm.ensure_maps(cache_path=p)
    assert maps["kr"]["000660"] == "코스피"
    assert mm.label_any("NVDA") == "나스닥"  # 전역 싱글턴 채워짐


def test_ensure_maps_stale_fallback_on_build_failure(tmp_path, monkeypatch) -> None:
    p = tmp_path / "market_map.json"
    p.write_text(json.dumps({"date": "2020-01-01", **MAPS}), encoding="utf-8")  # 옛날 캐시

    def _boom():
        raise RuntimeError("fdr down")

    monkeypatch.setattr(mm, "_build_maps", _boom)
    maps = mm.ensure_maps(cache_path=p)
    assert maps["kr"]["000660"] == "코스피"  # stale이라도 사용


def test_ensure_maps_empty_when_no_cache_and_build_fails(tmp_path, monkeypatch) -> None:
    def _boom():
        raise RuntimeError("fdr down")

    monkeypatch.setattr(mm, "_build_maps", _boom)
    maps = mm.ensure_maps(cache_path=tmp_path / "none.json")
    assert maps == {"kr": {}, "us": {}}
    assert mm.label_any("000660") == ""  # 라벨만 생략, 크래시 없음


def test_telegram_helpers_append_market_label() -> None:
    """#471 — 텔레그램 종목 표기에 시장 라벨 병기 (KR 링크 접미·US 심볼 병기)."""
    mm._MAPS = MAPS
    from src.market_report.telegram_notify import _naver_link, _us_name
    assert _naver_link("SK하이닉스", "000660").endswith("(코스피)")
    assert _us_name("엔비디아", "NVDA") == "엔비디아(NVDA·나스닥)"
    mm._MAPS = None
    assert _us_name("엔비디아", "NVDA") == "엔비디아(NVDA)"  # 맵 없으면 기존 표기


def test_ensure_maps_builds_and_caches(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(mm, "_build_maps", lambda: MAPS)
    p = tmp_path / "market_map.json"
    maps = mm.ensure_maps(cache_path=p)
    assert maps["us"]["NVDA"] == "나스닥"
    saved = json.loads(p.read_text(encoding="utf-8"))
    assert saved["kr"]["000660"] == "코스피"
    assert "date" in saved
