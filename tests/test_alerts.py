"""L2/L3 tests for WatchlistMonitor alert logic."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.alerts.monitor import WatchlistMonitor
from src.datasource.base import Quote
from src.storage.repos import AlertHistoryRepo, WatchlistRepo


@pytest.fixture
def monitor_setup(db_conn):
    datasource = MagicMock()
    notifier = MagicMock()
    notifier.send = AsyncMock()

    wl_repo = WatchlistRepo(db_conn)
    alert_repo = AlertHistoryRepo(db_conn)

    mon = WatchlistMonitor(
        datasource=datasource,
        notifier=notifier,
        watchlist_repo=wl_repo,
        alert_repo=alert_repo,
        allowed_chat_ids=["123456789"],
    )
    return mon, datasource, notifier, wl_repo


async def test_alert_triggered_above_threshold(monitor_setup):
    mon, ds, notifier, wl_repo = monitor_setup
    wl_repo.add("005930", "삼성전자", {"change_pct": 3.0})

    ds.get_quote = AsyncMock(
        return_value=Quote(
            ticker="005930",
            name="삼성전자",
            price=75000.0,
            change_pct=5.2,   # above 3% threshold
            volume=10_000_000,
            timestamp="20260526",
        )
    )

    await mon.run_once()

    notifier.send.assert_called_once()
    msg = notifier.send.call_args[0][1]
    assert "005930" in msg or "삼성전자" in msg
    assert "5.20%" in msg or "5.2" in msg


async def test_no_alert_below_threshold(monitor_setup):
    mon, ds, notifier, wl_repo = monitor_setup
    wl_repo.add("005930", "삼성전자", {"change_pct": 10.0})

    ds.get_quote = AsyncMock(
        return_value=Quote(
            ticker="005930",
            name="삼성전자",
            price=75000.0,
            change_pct=2.0,   # below 10% threshold
            volume=10_000_000,
            timestamp="20260526",
        )
    )

    await mon.run_once()
    notifier.send.assert_not_called()


async def test_empty_watchlist_no_error(monitor_setup):
    mon, ds, notifier, _ = monitor_setup
    await mon.run_once()
    ds.get_quote.assert_not_called()
    notifier.send.assert_not_called()


async def test_alert_uses_default_threshold(monitor_setup):
    """When no conditions set, default change_pct threshold is 5.0."""
    mon, ds, notifier, wl_repo = monitor_setup
    wl_repo.add("000660", "SK하이닉스")  # no explicit conditions

    ds.get_quote = AsyncMock(
        return_value=Quote(
            ticker="000660",
            name="SK하이닉스",
            price=200000.0,
            change_pct=6.0,   # above default 5%
            volume=5_000_000,
            timestamp="20260526",
        )
    )

    await mon.run_once()
    notifier.send.assert_called_once()


async def test_datasource_error_does_not_crash(monitor_setup):
    """A failing quote fetch for one ticker should not stop processing others."""
    mon, ds, notifier, wl_repo = monitor_setup
    wl_repo.add("005930", "삼성전자")
    wl_repo.add("000660", "SK하이닉스")

    call_count = 0

    async def flaky_quote(ticker: str) -> Quote:
        nonlocal call_count
        call_count += 1
        if ticker == "005930":
            raise RuntimeError("simulated network error")
        return Quote(ticker, "SK하이닉스", 200000.0, 8.0, 5_000_000, "20260526")

    ds.get_quote = flaky_quote

    await mon.run_once()  # should not raise

    assert call_count == 2
    notifier.send.assert_called_once()  # SK하이닉스 alert should still fire
