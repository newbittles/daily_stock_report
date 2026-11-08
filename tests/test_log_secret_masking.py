import logging

import src  # noqa: F401  — 패키지 import 시 httpx 로그 단계가 올라가야 한다


def test_httpx_info_suppressed_so_bot_token_not_logged() -> None:
    for name in ("httpx", "httpcore"):
        assert not logging.getLogger(name).isEnabledFor(logging.INFO)
