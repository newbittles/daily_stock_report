from __future__ import annotations

from src.storage.repos import AnalysisCacheRepo, SignalLogRepo, SignalRecord, TradeHistoryRepo, TradeRecord, WatchlistRepo


def test_watchlist_add_and_get(db_conn):
    repo = WatchlistRepo(db_conn)
    assert repo.get_all() == []

    repo.add("005930", "삼성전자")
    items = repo.get_all()
    assert len(items) == 1
    assert items[0].ticker == "005930"
    assert items[0].name == "삼성전자"
    assert items[0].conditions == {}


def test_watchlist_exists(db_conn):
    repo = WatchlistRepo(db_conn)
    assert not repo.exists("005930")
    repo.add("005930", "삼성전자")
    assert repo.exists("005930")


def test_watchlist_remove(db_conn):
    repo = WatchlistRepo(db_conn)
    repo.add("005930", "삼성전자")
    assert repo.remove("005930")
    assert not repo.exists("005930")


def test_watchlist_remove_nonexistent(db_conn):
    repo = WatchlistRepo(db_conn)
    assert not repo.remove("999999")  # returns False, no exception


def test_watchlist_replace_on_duplicate(db_conn):
    repo = WatchlistRepo(db_conn)
    repo.add("005930", "삼성전자", {"change_pct": 3.0})
    repo.add("005930", "삼성전자 수정", {"change_pct": 5.0})  # INSERT OR REPLACE
    items = repo.get_all()
    assert len(items) == 1
    assert items[0].name == "삼성전자 수정"
    assert items[0].conditions["change_pct"] == 5.0


def test_signal_log_insert(db_conn):
    repo = SignalLogRepo(db_conn)
    rec = SignalRecord(
        ticker="005930",
        signal_type="buy",
        pattern="pullback",
        score=0.78,
        reasons=["MA20 위", "RSI 48"],
    )
    row_id = repo.insert(rec)
    assert row_id == 1


def test_analysis_cache_hit(db_conn):
    repo = AnalysisCacheRepo(db_conn)
    repo.set("test_key", {"result": "ok"}, ttl_sec=3600)
    cached = repo.get("test_key")
    assert cached is not None
    assert cached["result"] == "ok"


def test_analysis_cache_miss(db_conn):
    repo = AnalysisCacheRepo(db_conn)
    assert repo.get("nonexistent") is None


def test_trade_history_insert_and_get(db_conn):
    repo = TradeHistoryRepo(db_conn)
    assert repo.get_recent() == []

    rec = TradeRecord(ticker="005930", name="삼성전자", trade_type="BUY",
                      price=75000.0, quantity=10, trade_date="20260520")
    repo.insert(rec)
    records = repo.get_recent()
    assert len(records) == 1
    assert records[0].ticker == "005930"
    assert records[0].trade_type == "BUY"
    assert records[0].price == 75000.0
    assert records[0].quantity == 10


def test_trade_history_upsert_deduplication(db_conn):
    repo = TradeHistoryRepo(db_conn)
    rec = TradeRecord(ticker="005930", name="삼성전자", trade_type="BUY",
                      price=75000.0, quantity=10, trade_date="20260520", order_no="ORD001")
    repo.upsert(rec)
    repo.upsert(rec)  # 동일 order_no → 무시
    assert len(repo.get_recent()) == 1


def test_trade_history_get_sells(db_conn):
    repo = TradeHistoryRepo(db_conn)
    repo.insert(TradeRecord(ticker="005930", name="삼성전자", trade_type="BUY",
                             price=75000.0, quantity=10, trade_date="20260520"))
    repo.insert(TradeRecord(ticker="005930", name="삼성전자", trade_type="SELL",
                             price=82000.0, quantity=10, trade_date="20260525"))
    repo.insert(TradeRecord(ticker="000660", name="SK하이닉스", trade_type="SELL",
                             price=180000.0, quantity=5, trade_date="20260524"))

    sells = repo.get_sells()
    assert len(sells) == 2
    assert all(r.trade_type == "SELL" for r in sells)


def test_analysis_cache_purge(db_conn):
    repo = AnalysisCacheRepo(db_conn)
    # Insert with 0 TTL (already expired)
    db_conn.execute(
        "INSERT INTO analysis_cache (cache_key, payload, ttl_sec) VALUES (?, ?, ?)",
        ("expired_key", '{"x":1}', 0),
    )
    db_conn.commit()
    count = repo.purge_expired()
    assert count >= 1
    assert repo.get("expired_key") is None
