"""KIS 모의(VTS) 주문 배관 스모크 — hashkey→매수가능→매수→잔고→매도.

dry-run 기본(미전송 프리뷰). 실제 모의주문 전송은 --send 명시 시에만.
실행: python scripts/smoke_paper_order.py --ticker 005930 --qty 1            # dry-run
      python scripts/smoke_paper_order.py --ticker 005930 --qty 1 --send     # 실제 모의주문
"""
from __future__ import annotations

import argparse
import asyncio
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows cp949 콘솔에서 한글/… 출력 시 UnicodeEncodeError 방지 (프로젝트 기존 publisher 이슈와 동일)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.config.settings import get_settings  # noqa: E402
from src.trading.kis_order import KisOrderClient  # noqa: E402


async def run(ticker: str, qty: int, price: int, send: bool) -> int:
    s = get_settings()
    # ── 안전 가드 ──
    if s.kis_env != "paper":
        print(f"[중단] KIS_ENV={s.kis_env} — 본 스모크는 모의(paper) 전용입니다. 실전 거부.")
        return 1
    if qty > 10:
        print(f"[중단] qty={qty} — 모의여도 10주 초과 금지(fat-finger 방지).")
        return 1

    client = KisOrderClient(s.kis_app_key, s.kis_app_secret, s.kis_account_no, env="paper")
    ord_dvsn = "00" if price > 0 else "01"  # 가격 지정 시 지정가, 아니면 시장가
    price_label = f"지정가 {price}" if price else "시장가"

    print(f"=== 모의 주문 스모크 · {ticker} {qty}주 · {price_label} ===")

    # 1) hashkey
    sample = {"CANO": client._cano, "ACNT_PRDT_CD": client._acnt, "PDNO": ticker, "ORD_QTY": str(qty)}
    h = await client.hashkey(sample)
    print(f"[1] hashkey OK · HASH={h[:10]}…")

    # 2) 매수가능조회
    psbl = await client.inquire_psbl_order(ticker, price=price, ord_dvsn=ord_dvsn)
    out = psbl.get("output", {})
    print(f"[2] 매수가능 수량={out.get('nrcvb_buy_qty')} 금액={out.get('nrcvb_buy_amt')}")

    if not send:
        print("[dry-run] 여기까지 배관 확인 완료. 실제 주문은 --send 로 실행.")
        return 0

    # 3) 매수
    print(f"[3] 매수 주문 전송: {ticker} {qty}주 ({price_label})")
    buy = await client.order_cash("buy", ticker, qty, price=price, ord_dvsn=ord_dvsn)
    odno = buy.get("output", {}).get("ODNO")
    print(f"    → 접수 odno={odno} msg={buy.get('msg1')}")
    await asyncio.sleep(random.uniform(1.5, 3.0))

    # 3.5) 체결확인 (inquire-daily-ccld) — 접수≠체결 검증
    fill = await client.confirm_fill(ticker, odno)
    if fill is None:
        print("[3.5] 체결확인: 당일 체결내역에서 주문 미발견(조회 시점 미반영 가능)")
    else:
        print(f"[3.5] 체결확인: 체결 {fill['filled_qty']}주 @평균 {fill['avg_price']:,.0f} "
              f"(주문 {fill['ord_qty']}주, 잔여 {fill['rmn_qty']}주)")

    # 4) 잔고 확인
    bal = await client.inquire_balance()
    held = [
        r for r in bal.get("output1", [])
        if r.get("pdno") == ticker and int(r.get("hldg_qty", "0") or "0") > 0
    ]
    print(f"[4] 보유 확인: {held if held else '(아직 미체결 또는 0)'}")

    # 5) 매도(청산)
    print(f"[5] 매도 주문 전송(청산): {ticker} {qty}주 시장가")
    sell = await client.order_cash("sell", ticker, qty, price=0, ord_dvsn="01")
    print(f"    → 접수 odno={sell.get('output', {}).get('ODNO')} msg={sell.get('msg1')}")
    await asyncio.sleep(random.uniform(1.5, 3.0))

    bal2 = await client.inquire_balance()
    cash = (bal2.get("output2") or [{}])[0].get("dnca_tot_amt")
    print(f"[6] 최종 예수금={cash}")
    print("=== 스모크 완료 ===")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="KIS 모의 주문 배관 스모크")
    ap.add_argument("--ticker", default="005930", help="종목코드 6자리(기본 삼성전자)")
    ap.add_argument("--qty", type=int, default=1, help="수량(기본 1, 최대 10)")
    ap.add_argument("--price", type=int, default=0, help="지정가(0이면 시장가)")
    ap.add_argument("--send", action="store_true", help="실제 모의주문 전송(미지정 시 dry-run)")
    a = ap.parse_args()
    if not (a.ticker.isdigit() and len(a.ticker) == 6):
        print("[중단] 종목코드는 6자리 숫자여야 합니다.")
        raise SystemExit(1)
    raise SystemExit(asyncio.run(run(a.ticker, a.qty, a.price, a.send)))


if __name__ == "__main__":
    main()
