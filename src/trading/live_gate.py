"""실전(real) 자동매매 런타임 게이트 — 기본 OFF.

코드는 항상 배포돼 있지만, 이 플래그가 명시적으로 ON('on')일 때만 실전 주문이 허용된다.
사용자가 "지시한 순간부터" 적용 = 이 플래그를 켜는 순간. 파일 기반이라 재배포/재시작 불필요.
플래그 파일은 런타임 상태(gitignore된 data/)에 둔다.
"""
from __future__ import annotations

from pathlib import Path

DEFAULT_FLAG = Path("data/live_trading.flag")


def is_live_enabled(flag: Path | str = DEFAULT_FLAG) -> bool:
    """플래그 파일이 존재하고 내용이 정확히 'on'이면 True. 그 외(없음·오타·기타)는 OFF."""
    p = Path(flag)
    if not p.exists():
        return False
    try:
        return p.read_text(encoding="utf-8").strip().lower() == "on"
    except OSError:
        return False


def enable_live(flag: Path | str = DEFAULT_FLAG) -> None:
    """실전 게이트 ON — 사용자의 명시적 활성화 명령에서만 호출."""
    p = Path(flag)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("on", encoding="utf-8")


def disable_live(flag: Path | str = DEFAULT_FLAG) -> None:
    """실전 게이트 OFF."""
    p = Path(flag)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("off", encoding="utf-8")
