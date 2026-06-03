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

from src.trading.ma_exit import decide_exit  # noqa: E402
from src.trading.positions import PositionStore  # noqa: E402
from src.trading.sizing import calc_qty, split_sell_qty  # noqa: E402
from src.trading.top3_bridge import load_top3  # noqa: E402

logger = logging.getLogger(__name__)


async def _emit(notify, msg: str) -> None:
    """콘솔 출력 + (있으면) 텔레그램 알림. 알림 실패는 무시(best-effort)."""
    print(msg)
    if notify is not None:
        try:
            await notify(msg)
        except Exception as exc:  # 알림 실패가 매매를 막지 않도록
            logger.warning("notify_failed error=%s", exc)


async def buy_top3(picks, adapter, order, store, *, send: bool, today: str, notify=None) -> None:
    await _emit(notify, f"=== auto-buy {today} · {len(picks)}종목 후보 ({'LIVE' if send else 'dry-run'}) ===")
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
        if not send:
            print(f"  BUY(dry-run) {ticker} {name} x{qty} (현재가 {quote.price}, 시장가)")
            continue
        res = await order.order_cash("buy", ticker, qty, price=0, ord_dvsn="01")
        store.open_position(ticker, name, today, float(quote.price), qty)
        await _emit(notify, f"🟢 모의매수 {name}({ticker}) x{qty} @{quote.price:,.0f} "
                            f"odno={res.get('output', {}).get('ODNO')} {res.get('msg1', '')}")


async def run_sell(adapter, order, store, *, send: bool, notify=None) -> None:
    open_pos = store.get_open()
    await _emit(notify, f"=== auto-sell · 보유 {len(open_pos)}종목 ({'LIVE' if send else 'dry-run'}) ===")
    for pos in open_pos:
        candles = await adapter.get_ohlcv(pos.ticker, days=80)
        closes = [c.close for c in candles]
        action, reason = decide_exit(closes)
        print(f"  {pos.ticker} {pos.name} qty={pos.qty} stage={pos.stage} → {action} {reason}")
        if action == "SELL_HALF" and pos.stage < 2:
            sell_qty, remaining = split_sell_qty(pos.qty)
            if not send:
                print(f"    SELL_HALF(dry-run) x{sell_qty} (잔여 {remaining}) — {reason}")
                continue
            await order.order_cash("sell", pos.ticker, sell_qty, price=0, ord_dvsn="01")
            if remaining > 0:
                store.update_qty_stage(pos.ticker, remaining, 2)
            else:
                store.close(pos.ticker)
            await _emit(notify, f"🔴 모의매도(50%) {pos.name}({pos.ticker}) x{sell_qty} "
                                f"{reason} · 잔여 {remaining}")
        elif action == "SELL_ALL":
            if not send:
                print(f"    SELL_ALL(dry-run) x{pos.qty} — {reason}")
                continue
            await order.order_cash("sell", pos.ticker, pos.qty, price=0, ord_dvsn="01")
            store.close(pos.ticker)
            await _emit(notify, f"🔴 모의매도(전량) {pos.name}({pos.ticker}) x{pos.qty} {reason}")


def _build_clients():
    """데이터(시세·일봉)는 실전 키로 조회(모의 도메인 OHLCV 500 회피), 주문은 모의 키로 전송.

    주문 클라이언트는 env='paper' 하드코딩 → 실수로도 실전 주문 불가(안전).
    """
    from src.config.settings import get_settings
    from src.datasource.kis.adapter import KisAdapter
    from src.trading.kis_order import KisOrderClient
    s = get_settings()
    if not (s.kis_paper_app_key and s.kis_paper_app_secret and s.kis_paper_account_no):
        print("[중단] 모의 키 미설정 — .env에 KIS_PAPER_APP_KEY/SECRET/ACCOUNT_NO 필요.")
        raise SystemExit(1)
    adapter = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, env=s.kis_env)  # 데이터(real)
    order = KisOrderClient(
        s.kis_paper_app_key, s.kis_paper_app_secret, s.kis_paper_account_no, env="paper"
    )  # 주문(paper 고정)
    return adapter, order


def _build_notify():
    """텔레그램 알림 클로저 (allowed_chat_ids로 전송). 실패는 best-effort."""
    try:
        from telegram import Bot
        from src.config.settings import get_settings
        s = get_settings()
        if not s.telegram_bot_token:
            return None
        bot = Bot(token=s.telegram_bot_token)
        chat_ids = s.allowed_chat_ids()

        async def _notify(msg: str) -> None:
            for cid in chat_ids:
                await bot.send_message(chat_id=cid, text=msg)

        return _notify
    except Exception as exc:
        logger.warning("notify_setup_failed error=%s", exc)
        return None


async def _main(action: str, send: bool) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    store = PositionStore()
    adapter, order = _build_clients()
    notify = _build_notify() if send else None  # dry-run은 알림 안 보냄
    if action == "buy":
        picks = load_top3(today)
        if not picks:
            print(f"[중단] 오늘({today}) top3 JSON 없음 — pre 리포트 먼저 실행 필요(구픽 매매 금지).")
            return 1
        await buy_top3(picks, adapter, order, store, send=send, today=today, notify=notify)
    else:
        await run_sell(adapter, order, store, send=send, notify=notify)
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="모의 자동매매(auto_trader v1)")
    ap.add_argument("action", choices=["buy", "sell"])
    ap.add_argument("--send", action="store_true", help="실제 모의주문 전송(미지정 시 dry-run)")
    a = ap.parse_args()
    raise SystemExit(asyncio.run(_main(a.action, a.send)))


if __name__ == "__main__":
    main()
