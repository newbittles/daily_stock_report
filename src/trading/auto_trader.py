"""모의 자동매매 루프 — 종가베팅 Top3 매수 + 일봉 20/60MA 청산.

CLI: python -m src.trading.auto_trader {buy|sell} [--send]
모의(paper) 전용. dry-run 기본(--send 명시 시에만 실제 주문)."""
from __future__ import annotations

import argparse
import asyncio
import logging
import random
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # cp949 콘솔 보호

from src.datasource.kis.token import KisTokenError  # noqa: E402
from src.indicators.core import average_true_range  # noqa: E402
from src.trading.ma_exit import decide_exit  # noqa: E402
from src.trading.positions import PositionStore  # noqa: E402
from src.trading.risk_exit import (  # noqa: E402
    combine_exits,
    decide_risk_exit,
    hard_stop_price,
    should_buy_pyramid,
)
from src.trading.sizing import calc_qty, split_sell_qty  # noqa: E402
from src.trading.top3_bridge import load_top3  # noqa: E402

logger = logging.getLogger(__name__)


def _atr_from_candles(candles: list, period: int = 14) -> float | None:
    """일봉 캔들 → ATR(14). 캔들에 고가/저가가 없으면(테스트 더미 등) None."""
    highs = [getattr(c, "high", None) for c in candles]
    lows = [getattr(c, "low", None) for c in candles]
    closes = [getattr(c, "close", None) for c in candles]
    if not candles or any(h is None for h in highs) or any(lo is None for lo in lows):
        return None
    return average_true_range(highs, lows, closes, period)


async def _emit(notify, msg: str) -> None:
    """콘솔 출력 + (있으면) 텔레그램 알림. 알림 실패는 무시(best-effort)."""
    print(msg)
    if notify is not None:
        try:
            await notify(msg)
        except Exception as exc:  # 알림 실패가 매매를 막지 않도록
            logger.warning("notify_failed error=%s", exc)


async def _confirm_or_fallback(
    order, ticker: str, odno: str, today: str, fallback_qty: int, fallback_price: float,
    *, notify, tag: str, label: str, name: str,
) -> tuple[int, float, bool]:
    """체결조회로 (체결수량, 평균가, 확인됨여부) 산출 — 접수(rt_cd=0)≠체결 보정.

    confirmed=True면 숫자를 신뢰(filled 0=미체결 포함 → 호출측이 미기록 처리).
    조회 실패/미발견(best-effort)이면 confirmed=False + 접수 기준 폴백(현재 동작 유지).
    """
    if not odno:
        return fallback_qty, fallback_price, False
    try:
        fill = await order.confirm_fill(ticker, odno, today=today.replace("-", ""))
    except Exception as exc:  # 체결조회 실패가 매매를 막지 않도록(조용한 실패 금지 → 알림)
        logger.warning("confirm_fill_failed ticker=%s odno=%s error=%s", ticker, odno, exc)
        await _emit(notify, f"⚠️ {label} 체결확인 실패 {name}({ticker}) odno={odno} — 접수가 기준 기록(점검요)")
        return fallback_qty, fallback_price, False
    if fill is None:
        await _emit(notify, f"⚠️ {label} 체결내역 미발견 {name}({ticker}) odno={odno} — 접수가 기준 기록")
        return fallback_qty, fallback_price, False
    return fill["filled_qty"], (fill["avg_price"] or fallback_price), True


async def buy_top3(
    picks, adapter, order, store, *, send: bool, today: str, notify=None, live: bool = False
) -> None:
    tag = "실전" if live else "모의"
    await _emit(notify, f"=== auto-buy {today} · {len(picks)}종목 후보 "
                        f"({'LIVE' if send else 'dry-run'}{'·실전계좌' if live else ''}) ===")
    # 토큰 1회 선발급(공유) — 종목마다 재발급 연타로 KIS 분당 토큰발급 제한 403에 걸리던 회귀 방지
    # (2026-06-17). 선발급 실패 시 매수 전체 중단(연타 금지, 전역 §7 Hard Stop).
    try:
        await order.ensure_token()
    except Exception as exc:  # noqa: BLE001
        await _emit(notify, f"🛑 {tag}매수 중단(Hard Stop) — KIS 토큰 발급 실패: {exc}")
        return
    for p in picks:
        ticker, name = p["ticker"], p.get("name", "")
        try:
            # 피라미딩: 재추천이면 보유 중이라도 추가매수. 단 같은 날 중복매수만 금지.
            if not should_buy_pyramid(store.is_held(ticker), store.last_entry_date(ticker), today):
                print(f"  skip {ticker} {name} — 오늘 이미 매수(중복방지)")
                continue
            price = await adapter.get_price_safe(ticker)  # quote 500 장애 시 일봉 폴백(#484/#492)
            if price <= 0:
                print(f"  skip {ticker} {name} — 현재가 조회 실패(quote·일봉 모두)")
                continue
            qty = calc_qty(price)
            if qty < 1:
                print(f"  skip {ticker} {name} — 현재가 {price} 1주도 예산초과")
                continue
            psbl = await order.inquire_psbl_order(ticker, price=0, ord_dvsn="01")
            max_qty = int(psbl.get("output", {}).get("nrcvb_buy_qty", "0") or "0")
            qty = min(qty, max_qty) if max_qty else qty
            if qty < 1:
                print(f"  skip {ticker} {name} — 매수가능수량 0")
                continue
            # 진입 하드스톱 산정용 ATR(14). 캔들 없으면 −8% 퍼센트 손절로 폴백.
            strategy = ",".join(p.get("strategies", []) or [])  # 전략별 손절 선택용(2026-06-07)
            strat_list = p.get("strategies", []) or None
            candles = await adapter.get_ohlcv(ticker, days=40)
            atr = _atr_from_candles(candles)
            held = store.is_held(ticker)
            if not send:
                kind = "추가매수" if held else "신규매수"
                print(f"  BUY/{kind}(dry-run) {ticker} {name} x{qty} (현재가 {price}, 시장가)")
                continue
            res = await order.order_cash("buy", ticker, qty, price=0, ord_dvsn="01")
            odno = (res.get("output") or {}).get("ODNO", "")
            # 체결확인: 접수≠체결 → 실제 체결수량·평균가로 기록(미체결이면 미기록)
            fqty, entry, confirmed = await _confirm_or_fallback(
                order, ticker, odno, today, qty, float(price),
                notify=notify, tag=tag, label=f"{tag}매수", name=name,
            )
            if confirmed and fqty <= 0:
                await _emit(notify, f"⚠️ {tag}매수 미체결 {name}({ticker}) odno={odno} — 포지션 미기록")
                continue
            qty = fqty
            stop = hard_stop_price(entry, atr, strat_list)  # 실제 체결가 기준 하드스톱
            if held:  # 피라미딩 — 가중평균. 손절가는 새 평단 기준 재산정은 매도루프가 보정.
                store.add_to_position(ticker, qty, entry, today, initial_stop=stop)
                kind = "추가매수(피라미딩)"
            else:
                store.open_position(ticker, name, today, entry, qty, strategy=strategy,
                                    initial_stop=stop, highest=entry)
                kind = "신규매수"
            ck = " ✅체결" if confirmed else " ⚠접수가"
            await _emit(notify, f"🟢 {tag}{kind}{ck} {name}({ticker}) x{qty} @{entry:,.0f} "
                                f"odno={odno} {res.get('msg1', '')}")
        except KisTokenError as exc:  # 토큰 발급 거부 = 연타 금지, 매수 전체 중단(전역 §7 Hard Stop)
            logger.error("buy_token_hardstop ticker=%s error=%s", ticker, exc)
            await _emit(notify, f"🛑 {tag}매수 중단(Hard Stop) — KIS 토큰 발급 거부: {exc}")
            break
        except Exception as exc:  # 조용한 실패 금지 — 알리고 다음 종목 계속(사용자 2026-06-07)
            logger.exception("buy_failed ticker=%s error=%s", ticker, exc)
            await _emit(notify, f"⚠️ {tag}매수 실패 {name}({ticker}) — {exc}")


async def run_sell(adapter, order, store, *, send: bool, notify=None, live: bool = False) -> None:
    tag = "실전" if live else "모의"
    open_pos = store.get_open()
    await _emit(notify, f"=== auto-sell · 보유 {len(open_pos)}종목 "
                        f"({'LIVE' if send else 'dry-run'}{'·실전계좌' if live else ''}) ===")
    summary: list[str] = []  # 📋 일일 포지션 현황(사용자 2026-06-07) — HOLD 포함 전 종목
    if send and open_pos:  # 매도 주문 전 토큰 1회 선발급(연타 방지) — dry-run/무포지션은 불필요
        try:
            await order.ensure_token()
        except Exception as exc:  # noqa: BLE001
            await _emit(notify, f"🛑 {tag}매도 중단(Hard Stop) — KIS 토큰 발급 실패: {exc}")
            return
    for pos in open_pos:
        try:
            candles = await adapter.get_ohlcv(pos.ticker, days=80)
            closes = [c.close for c in candles]
            strategies = [s for s in pos.strategy.split(",") if s] if pos.strategy else None
            cur = closes[-1] if closes else None
            # ① MA 추세청산(기존)
            ma_action, ma_reason = decide_exit(closes, strategies=strategies)
            # ② 리스크 레이어(하드스톱·트레일링·+1R) — 보유 최고가 갱신 후 판정
            risk_action, risk_reason = "HOLD", ""
            if cur is not None and pos.initial_stop > 0:
                cur_high = max((getattr(c, "high", c.close) for c in candles), default=cur)
                highest = max(pos.highest or pos.entry_price, cur_high)
                if highest != pos.highest:
                    store.update_risk_state(pos.ticker, highest=highest)
                atr = _atr_from_candles(candles)
                risk_action, risk_reason, _ = decide_risk_exit(
                    pos.entry_price, pos.initial_stop, highest, atr, cur,
                    strategies=strategies, partial_taken=pos.partial_taken,
                )
            # ③ '먼저 닿는 것' 결합 (더 강한 매도 우선)
            action, reason = combine_exits((ma_action, ma_reason), (risk_action, risk_reason))
            print(f"  {pos.ticker} {pos.name} qty={pos.qty} stage={pos.stage} → {action} {reason}")
            line = f"{pos.name}({pos.ticker}) {pos.strategy or '-'} · 진입 {pos.entry_price:,.0f}"
            if cur is not None and pos.entry_price:
                pnl = (cur / pos.entry_price - 1) * 100
                line += f" → 현재 {cur:,.0f} ({pnl:+.1f}%)"
            summary.append(line + f" · {action}" + (f" {reason}" if reason else ""))
            if action == "SELL_HALF" and pos.stage < 2 and not pos.partial_taken:
                sell_qty, _planned_rmn = split_sell_qty(pos.qty)
                if not send:
                    print(f"    SELL_HALF(dry-run) x{sell_qty} (잔여 {_planned_rmn}) — {reason}")
                    continue
                res = await order.order_cash("sell", pos.ticker, sell_qty, price=0, ord_dvsn="01")
                odno = (res.get("output") or {}).get("ODNO", "")
                fqty, _, confirmed = await _confirm_or_fallback(
                    order, pos.ticker, odno, "", sell_qty, float(cur or pos.entry_price),
                    notify=notify, tag=tag, label=f"{tag}매도", name=pos.name)
                if confirmed and fqty <= 0:  # 미체결 — store 미변경, 다음 회차 재시도
                    await _emit(notify, f"⚠️ {tag}매도(50%) 미체결 {pos.name}({pos.ticker}) — 다음 회차 재시도")
                    continue
                sold = fqty if confirmed else sell_qty
                remaining = pos.qty - sold
                if remaining > 0:
                    store.update_qty_stage(pos.ticker, remaining, 2)
                    store.update_risk_state(pos.ticker, partial_taken=True)
                else:
                    store.close(pos.ticker)
                await _emit(notify, f"🔴 {tag}매도(50%) {pos.name}({pos.ticker}) x{sold} "
                                    f"{reason} · 잔여 {remaining}")
            elif action == "SELL_ALL":
                if not send:
                    print(f"    SELL_ALL(dry-run) x{pos.qty} — {reason}")
                    continue
                res = await order.order_cash("sell", pos.ticker, pos.qty, price=0, ord_dvsn="01")
                odno = (res.get("output") or {}).get("ODNO", "")
                fqty, _, confirmed = await _confirm_or_fallback(
                    order, pos.ticker, odno, "", pos.qty, float(cur or pos.entry_price),
                    notify=notify, tag=tag, label=f"{tag}매도", name=pos.name)
                if confirmed and fqty <= 0:  # 미체결 — 보유 유지, 다음 회차 재시도
                    await _emit(notify, f"⚠️ {tag}매도(전량) 미체결 {pos.name}({pos.ticker}) — 다음 회차 재시도")
                    continue
                sold = fqty if confirmed else pos.qty
                remaining = pos.qty - sold
                if remaining > 0:  # 부분체결 — 잔여 보유 유지(stage 보존)
                    store.update_qty_stage(pos.ticker, remaining, pos.stage)
                    await _emit(notify, f"🔴 {tag}매도(부분 {sold}/{pos.qty}) {pos.name}({pos.ticker}) "
                                        f"{reason} · 잔여 {remaining}")
                else:
                    store.close(pos.ticker)
                    await _emit(notify, f"🔴 {tag}매도(전량) {pos.name}({pos.ticker}) x{sold} {reason}")
        except KisTokenError as exc:  # 토큰 발급 거부 = 연타 금지, 매도 전체 중단(전역 §7 Hard Stop)
            logger.error("sell_token_hardstop ticker=%s error=%s", pos.ticker, exc)
            await _emit(notify, f"🛑 {tag}매도 중단(Hard Stop) — KIS 토큰 발급 거부: {exc}")
            break
        except Exception as exc:  # 조용한 실패 금지 — 알리고 다음 종목 계속(사용자 2026-06-07)
            logger.exception("sell_failed ticker=%s error=%s", pos.ticker, exc)
            await _emit(notify, f"⚠️ {tag}매도 점검 실패 {pos.name}({pos.ticker}) — {exc}")
            summary.append(f"{pos.name}({pos.ticker}) {pos.strategy or '-'} · ⚠️점검실패 {exc}")
    if open_pos:
        await _emit(notify, f"📋 {tag} 포지션 현황 ({len(open_pos)}종목)\n" + "\n".join(summary))


async def sync_account_to_store(order, adapter, store, today: str, *, notify=None) -> int:
    """[scope-B] 실전계좌 보유 전체를 store에 부트스트랩 — 봇이 안 산 종목도 손절/익절 관리.

    잔고조회(output1)의 평단(pchs_avg_pric)을 진입가로, ATR/−8%로 초기 하드스톱 산정.
    이미 추적 중인 종목은 그대로 둔다(봇이 쌓은 리스크상태 보존). 반환: 신규 편입 수.
    """
    bal = await order.inquire_balance()
    added = 0
    for it in bal.get("output1", []) or []:
        ticker = (it.get("pdno") or "").strip()
        qty = int(float(it.get("hldg_qty", "0") or 0))
        if not ticker or qty <= 0 or store.is_held(ticker):
            continue
        name = it.get("prdt_name", ticker)
        avg = float(it.get("pchs_avg_pric", "0") or 0)
        if avg <= 0:
            continue
        try:
            candles = await adapter.get_ohlcv(ticker, days=40)
            atr = _atr_from_candles(candles)
        except Exception:  # 시세 실패해도 퍼센트 손절로 편입(조용한 실패 금지)
            atr = None
        store.open_position(ticker, name, today, avg, qty, strategy="",
                            initial_stop=hard_stop_price(avg, atr, None), highest=avg)
        added += 1
    if added:
        await _emit(notify, f"🔁 실전계좌 보유 {added}종목 손절/익절 관리 편입")
    return added


def _build_clients(live: bool = False):
    """데이터(시세·일봉)는 실전 키로 조회(모의 도메인 OHLCV 500 회피).

    주문 클라이언트: live=False → 모의(paper) 키, live=True → 실전(real) 키·실전 env.
    live=True는 호출 전에 반드시 게이트(is_live_enabled)로 한 번 더 걸러진다(main).
    """
    from src.config.settings import get_settings
    from src.datasource.kis.adapter import KisAdapter
    from src.trading.kis_order import KisOrderClient
    s = get_settings()
    adapter = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, env=s.kis_env)  # 데이터(real)
    if live:
        if not (s.kis_app_key and s.kis_app_secret and s.kis_account_no):
            print("[중단] 실전 키 미설정 — .env에 KIS_APP_KEY/SECRET/ACCOUNT_NO 필요.")
            raise SystemExit(1)
        order = KisOrderClient(s.kis_app_key, s.kis_app_secret, s.kis_account_no, env="real")
    else:
        if not (s.kis_paper_app_key and s.kis_paper_app_secret and s.kis_paper_account_no):
            print("[중단] 모의 키 미설정 — .env에 KIS_PAPER_APP_KEY/SECRET/ACCOUNT_NO 필요.")
            raise SystemExit(1)
        order = KisOrderClient(
            s.kis_paper_app_key, s.kis_paper_app_secret, s.kis_paper_account_no, env="paper"
        )
    return adapter, order


def _build_notify():
    """텔레그램 알림 클로저 — 자동매수 결과는 오너(본인)에게만 발송(사용자 2026-06-14). best-effort."""
    try:
        from telegram import Bot
        from src.config.settings import get_settings
        s = get_settings()
        if not s.telegram_bot_token:
            return None
        bot = Bot(token=s.telegram_bot_token)
        # 자동매수 결과는 다른 유저에게 노출 X → 오너 계정에만(없으면 첫 allowed)
        chat_ids = sorted(s.owner_chat_ids())

        async def _notify(msg: str) -> None:
            for cid in chat_ids:
                await bot.send_message(chat_id=cid, text=msg)

        return _notify
    except Exception as exc:
        logger.warning("notify_setup_failed error=%s", exc)
        return None


LIVE_DB = Path("data/real_positions.db")


async def _main(action: str, send: bool, live: bool) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    tag = "실전" if live else "모의"
    store = PositionStore(LIVE_DB) if live else PositionStore()
    adapter, order = _build_clients(live)
    notify = _build_notify() if send else None  # dry-run은 알림 안 보냄
    if action == "buy":
        picks = load_top3(today)
        if not picks:  # 조용한 실패 금지 — cron 무소식 방지(사용자 2026-06-07)
            await _emit(notify, f"⚠️ {tag}매수 중단 — 오늘({today}) top3 JSON 없음. "
                                f"pre 리포트 먼저 실행 필요(구픽 매매 금지).")
            return 1
        await buy_top3(picks, adapter, order, store, send=send, today=today,
                       notify=notify, live=live)
    else:
        if live:  # scope-B: 실전계좌 보유 전체를 손절/익절 관리 대상으로 편입
            await sync_account_to_store(order, adapter, store, today, notify=notify)
        await run_sell(adapter, order, store, send=send, notify=notify, live=live)
    return 0


def main() -> None:
    from src.trading.live_gate import disable_live, enable_live, is_live_enabled
    ap = argparse.ArgumentParser(description="자동매매(auto_trader v2 — 모의/실전 게이트)")
    ap.add_argument("action", choices=["buy", "sell", "live-on", "live-off", "status"])
    ap.add_argument("--send", action="store_true", help="실제 주문 전송(미지정 시 dry-run)")
    ap.add_argument("--live", action="store_true",
                    help="실전계좌 사용(게이트 ON일 때만 실제 실전, OFF면 안전하게 모의로 실행)")
    a = ap.parse_args()

    # 게이트 운영 명령(실전 활성/비활성/상태) — 사용자의 명시적 '지시'
    if a.action == "live-on":
        enable_live()
        print("🔴 실전 게이트 ON — 이후 '--live --send' 실행부터 실전 주문 전송.")
        return
    if a.action == "live-off":
        disable_live()
        print("🟢 실전 게이트 OFF — 실전 주문 차단(모의만).")
        return
    if a.action == "status":
        print(f"실전 게이트: {'ON 🔴' if is_live_enabled() else 'OFF 🟢'}")
        return

    # 트리플 잠금: --live(의도) + 게이트 ON(사용자 명령) 둘 다여야 실전. 아니면 모의로 안전 실행.
    live = bool(a.live and is_live_enabled())
    if a.live and not live:
        print("[실전 게이트 OFF] --live 지정됐지만 게이트가 꺼져 있어 모의(paper)로 실행합니다. "
              "실전 활성화: python -m src.trading.auto_trader live-on")
    raise SystemExit(asyncio.run(_main(a.action, a.send, live)))


if __name__ == "__main__":
    main()
