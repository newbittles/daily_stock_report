"""모의 자동매매 루프 — 종가베팅 Top3 매수 + 일봉 20/60MA 청산.

CLI: python -m src.trading.auto_trader {buy|sell} [--send]
모의(paper) 전용. dry-run 기본(--send 명시 시에만 실제 주문)."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # cp949 콘솔 보호

from src.trading.ma_exit import exit_decision  # noqa: E402
from src.trading.positions import PositionStore  # noqa: E402
from src.trading.sizing import calc_qty, split_sell_qty  # noqa: E402
from src.trading.top3_bridge import load_top3  # noqa: E402

logger = logging.getLogger(__name__)


async def buy_top3(picks, adapter, order, store, *, send: bool, today: str) -> None:
    print(f"=== auto-buy {today} · {len(picks)}종목 후보 ({'LIVE' if send else 'dry-run'}) ===")
    for p in picks:
        ticker, name = p["ticker"], p.get("name", "")
        if store.is_held(ticker):
            print(f"  skip {ticker} {name} — 이미 보유")
            continue
        quote = await adapter.get_quote(ticker)
        qty = calc_qty(quote.price)
        if qty < 1:
            print(f"  skip {ticker} {name} — 현재가 {quote.price} 1주도 예산초과")
            continue
        psbl = await order.inquire_psbl_order(ticker, price=0, ord_dvsn="01")
        max_qty = int(psbl.get("output", {}).get("nrcvb_buy_qty", "0") or "0")
        qty = min(qty, max_qty) if max_qty else qty
        if qty < 1:
            print(f"  skip {ticker} {name} — 매수가능수량 0")
            continue
        print(f"  BUY {ticker} {name} x{qty} (현재가 {quote.price}, 시장가)")
        if not send:
            continue
        res = await order.order_cash("buy", ticker, qty, price=0, ord_dvsn="01")
        print(f"    → odno={res.get('output', {}).get('ODNO')} msg={res.get('msg1')}")
        store.open_position(ticker, name, today, float(quote.price), qty)


async def run_sell(adapter, order, store, *, send: bool) -> None:
    open_pos = store.get_open()
    print(f"=== auto-sell · 보유 {len(open_pos)}종목 ({'LIVE' if send else 'dry-run'}) ===")
    for pos in open_pos:
        candles = await adapter.get_ohlcv(pos.ticker, days=80)
        closes = [c.close for c in candles]
        decision = exit_decision(closes)
        print(f"  {pos.ticker} {pos.name} qty={pos.qty} stage={pos.stage} → {decision}")
        if decision == "HOLD":
            continue
        if decision == "SELL_HALF" and pos.stage < 2:
            sell_qty, remaining = split_sell_qty(pos.qty)
            print(f"    SELL_HALF x{sell_qty} (잔여 {remaining})")
            if send:
                await order.order_cash("sell", pos.ticker, sell_qty, price=0, ord_dvsn="01")
                if remaining > 0:
                    store.update_qty_stage(pos.ticker, remaining, 2)
                else:
                    store.close(pos.ticker)
        elif decision == "SELL_ALL":
            print(f"    SELL_ALL x{pos.qty}")
            if send:
                await order.order_cash("sell", pos.ticker, pos.qty, price=0, ord_dvsn="01")
                store.close(pos.ticker)


def _build_clients():
    from src.config.settings import get_settings
    from src.datasource.kis.adapter import KisAdapter
    from src.trading.kis_order import KisOrderClient
    s = get_settings()
    if s.kis_env != "paper":
        print(f"[중단] KIS_ENV={s.kis_env} — auto_trader v1은 모의(paper) 전용.")
        raise SystemExit(1)
    adapter = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, env="paper")
    order = KisOrderClient(s.kis_app_key, s.kis_app_secret, s.kis_account_no, env="paper")
    return adapter, order


async def _main(action: str, send: bool) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    store = PositionStore()
    adapter, order = _build_clients()
    if action == "buy":
        picks = load_top3(today)
        if not picks:
            print(f"[중단] 오늘({today}) top3 JSON 없음 — pre 리포트 먼저 실행 필요(구픽 매매 금지).")
            return 1
        await buy_top3(picks, adapter, order, store, send=send, today=today)
    else:
        await run_sell(adapter, order, store, send=send)
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="모의 자동매매(auto_trader v1)")
    ap.add_argument("action", choices=["buy", "sell"])
    ap.add_argument("--send", action="store_true", help="실제 모의주문 전송(미지정 시 dry-run)")
    a = ap.parse_args()
    raise SystemExit(asyncio.run(_main(a.action, a.send)))


if __name__ == "__main__":
    main()
