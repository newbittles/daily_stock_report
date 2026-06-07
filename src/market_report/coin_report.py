"""코인 시세 리포트 — 매일 17:00(주말 포함). 시세(KRW·USD) + 김치프리미엄 + 심리지표.

사용자 확정 스펙(2026-06-07): BTC·ETH+시총상위 10개 / 업비트 KRW + CoinGecko USD /
김프 + 코인 공포탐욕 + BTC 도미넌스 / 텔레그램 + 웹(독립 HTML, 주식 index 미통합 v1).
AI 요약 없음(시세·지표만). 주식 스냅샷 머신과 독립 — 기존 리포트 출력 불변.

CLI: python -m src.market_report.coin_report [--no-send] [--no-publish]
"""
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

from src.datasource.coin.sources import (  # noqa: E402
    COIN_UNIVERSE,
    fetch_coin_fng,
    fetch_gecko_global,
    fetch_gecko_markets,
    fetch_upbit_tickers,
)

logger = logging.getLogger(__name__)

REPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "docs" / "reports"
PAGES_BASE = "https://newbittles.github.io/daily_stock_report"

DISCLAIMER = "📌 참고용 정보입니다. 매수·매도 추천이 아니며 투자 판단과 책임은 본인에게 있습니다."


# ─── 순수 계산/포맷 ──────────────────────────────────────────────────────────


def kimchi_premium(krw: float | None, usd: float | None, fx: float | None) -> float | None:
    """김치프리미엄(%) = 업비트KRW / (글로벌USD × USDKRW환율) − 1. 결측·0 나눗셈 → None."""
    if not krw or not usd or not fx:
        return None
    return (krw / (usd * fx) - 1.0) * 100.0


def _fmt_krw(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:,.0f}" if v >= 100 else f"{v:,.2f}"


def _fmt_usd(v: float | None) -> str:
    if v is None:
        return "—"
    if v >= 100:
        return f"${v:,.0f}"
    return f"${v:,.2f}" if v >= 1 else f"${v:,.4f}"


def _fmt_pct(v: float | None) -> str:
    return "—" if v is None else f"{v:+.1f}%"


def build_coin_rows(
    universe: list[dict], upbit: dict[str, dict], gecko: dict[str, dict], fx: float,
) -> list[dict]:
    """유니버스 × 소스 병합 → 표시 행. 한쪽 소스 결측이어도 행 유지(있는 값만)."""
    rows: list[dict] = []
    for c in universe:
        u = upbit.get(c["upbit"]) or {}
        g = gecko.get(c["gecko"]) or {}
        krw, usd = u.get("krw"), g.get("usd")
        rows.append({
            "sym": c["sym"], "name_ko": c["name_ko"],
            "krw": krw, "krw_change": u.get("change_pct"), "value_24h": u.get("value_24h"),
            "usd": usd, "usd_change": g.get("change_pct"),
            "mcap": g.get("mcap"), "rank": g.get("rank"),
            "kimchi": kimchi_premium(krw, usd, fx),
        })
    return rows


def format_coin_telegram(
    rows: list[dict], *, fng: dict | None, glob: dict | None, fx: float,
    now: datetime, url: str = "",
) -> str:
    """텔레그램 메시지 — 헤더(심리지표) + 코인별 1줄 + 면책."""
    lines = [f"🪙 코인 시세 ({now.strftime('%m-%d %H:%M')})"]
    senti = []
    if fng:
        senti.append(f"공포탐욕 {fng['score']} ({fng.get('rating_ko', '')})")
    if glob:
        dom = f"BTC 도미넌스 {glob['btc_dominance']:.1f}%"
        if glob.get("mcap_change_24h") is not None:
            dom += f" · 전체시총 24h {glob['mcap_change_24h']:+.1f}%"
        senti.append(dom)
    if senti:
        lines.append("🧭 " + " | ".join(senti))
    if fx:
        lines.append(f"💱 환율 {fx:,.0f}원/$")
    lines.append("")
    for r in rows:
        seg = [f"{r['name_ko']}({r['sym']})"]
        if r["krw"] is not None:
            seg.append(f"{_fmt_krw(r['krw'])}원 ({_fmt_pct(r['krw_change'])})")
        if r["usd"] is not None:
            seg.append(f"{_fmt_usd(r['usd'])} ({_fmt_pct(r['usd_change'])})")
        if r["kimchi"] is not None:
            seg.append(f"김프 {r['kimchi']:+.1f}%")
        lines.append(" · ".join(seg))
    lines.append("")
    lines.append(DISCLAIMER)
    if url:
        lines.append(f"🔗 {url}")
    return "\n".join(lines)


def render_coin_html(
    rows: list[dict], *, fng: dict | None, glob: dict | None, fx: float, now: datetime,
) -> str:
    """독립 HTML 리포트 (주식 report.html과 무관한 경량 페이지)."""
    date_str = now.strftime("%Y-%m-%d")

    def chg_cls(v: float | None) -> str:
        if v is None:
            return ""
        return "up" if v >= 0 else "down"

    body_rows = []
    for r in rows:
        body_rows.append(
            "<tr>"
            f"<td class='name'>{r['name_ko']} <span class='sym'>{r['sym']}</span></td>"
            f"<td class='num'>{_fmt_krw(r['krw'])}</td>"
            f"<td class='num {chg_cls(r['krw_change'])}'>{_fmt_pct(r['krw_change'])}</td>"
            f"<td class='num'>{_fmt_usd(r['usd'])}</td>"
            f"<td class='num {chg_cls(r['usd_change'])}'>{_fmt_pct(r['usd_change'])}</td>"
            f"<td class='num {chg_cls(r['kimchi'])}'>"
            f"{('%+.2f%%' % r['kimchi']) if r['kimchi'] is not None else '—'}</td>"
            "</tr>"
        )
    senti = []
    if fng:
        senti.append(f"😨 공포탐욕 <b>{fng['score']}</b> ({fng.get('rating_ko', '')})")
    if glob:
        s = f"👑 BTC 도미넌스 <b>{glob['btc_dominance']:.1f}%</b>"
        if glob.get("mcap_change_24h") is not None:
            s += f" · 전체시총 24h {glob['mcap_change_24h']:+.1f}%"
        senti.append(s)
    if fx:
        senti.append(f"💱 환율 {fx:,.0f}원/$")
    senti_html = " &nbsp;|&nbsp; ".join(senti)
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>🪙 코인 시세 리포트 {date_str}</title>
<style>
  :root {{ --bg:#0f1117; --card:#1a1d27; --text:#e8eaf0; --muted:#8b91a5;
           --up:#2ecc71; --down:#e74c3c; --line:#2a2e3d; }}
  body {{ background:var(--bg); color:var(--text); font-family:'Segoe UI',sans-serif;
          margin:0; padding:16px; }}
  .wrap {{ max-width:760px; margin:0 auto; }}
  h1 {{ font-size:1.3rem; }}
  .senti {{ background:var(--card); border:1px solid var(--line); border-radius:10px;
            padding:10px 14px; margin:12px 0; font-size:.95rem; }}
  table {{ width:100%; border-collapse:collapse; background:var(--card);
           border-radius:10px; overflow:hidden; }}
  th, td {{ padding:8px 10px; border-bottom:1px solid var(--line); font-size:.9rem; }}
  th {{ color:var(--muted); text-align:right; font-weight:600; }}
  th.name, td.name {{ text-align:left; }}
  td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .sym {{ color:var(--muted); font-size:.8rem; }}
  .up {{ color:var(--up); }} .down {{ color:var(--down); }}
  .disclaimer {{ color:var(--muted); font-size:.8rem; margin-top:14px; }}
</style>
</head>
<body>
<div class="wrap">
<h1>🪙 코인 시세 리포트 <span class="sym">{date_str} {now.strftime('%H:%M')} KST</span></h1>
<div class="senti">{senti_html}</div>
<table>
<thead><tr><th class="name">코인</th><th>업비트(원)</th><th>24h</th>
<th>글로벌(USD)</th><th>24h</th><th>김치프리미엄</th></tr></thead>
<tbody>
{''.join(body_rows)}
</tbody>
</table>
<p class="disclaimer">{DISCLAIMER}<br>
소스: 업비트(KRW) · CoinGecko(USD·도미넌스) · alternative.me(공포탐욕). 김프 = 업비트 ÷ (글로벌×환율) − 1.</p>
</div>
</body>
</html>
"""


# ─── 러너 ────────────────────────────────────────────────────────────────────


async def _send_telegram(text: str) -> bool:
    """allowed_chat_ids 전체로 발송. 실패는 best-effort(리포트 발행은 계속)."""
    try:
        from telegram import Bot

        from src.config.settings import get_settings
        s = get_settings()
        if not s.telegram_bot_token:
            return False
        bot = Bot(token=s.telegram_bot_token)
        for cid in s.allowed_chat_ids():
            await bot.send_message(chat_id=cid, text=text)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("coin_telegram_failed error=%s", exc)
        return False


async def run_coin_report(*, send: bool = True, publish: bool = True) -> dict | None:
    """수집 → 리포트 생성 → 웹 발행 → 텔레그램. 핵심 시세 전부 실패 시 None(발송 안 함)."""
    now = datetime.now()
    markets = [c["upbit"] for c in COIN_UNIVERSE]
    ids = [c["gecko"] for c in COIN_UNIVERSE]

    upbit = await fetch_upbit_tickers(markets)
    await asyncio.sleep(random.uniform(1.0, 3.0))  # §7 요청 간 랜덤 딜레이
    gecko = await fetch_gecko_markets(ids)
    await asyncio.sleep(random.uniform(1.0, 3.0))
    glob = await fetch_gecko_global()
    fng = await fetch_coin_fng()
    from src.datasource.us.fdr_source import fetch_usd_krw
    fx = await fetch_usd_krw()

    if not upbit and not gecko:
        logger.error("coin_report_abort — 업비트·CoinGecko 둘 다 실패")
        return None

    rows = build_coin_rows(COIN_UNIVERSE, upbit, gecko, fx)
    date_str = now.strftime("%Y-%m-%d")
    url = f"{PAGES_BASE}/reports/{date_str}-coin.html"

    html = render_coin_html(rows, fng=fng, glob=glob, fx=fx, now=now)
    out = REPORTS_DIR / f"{date_str}-coin.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    logger.info("coin_report_written path=%s", out)

    published = False
    if publish:
        from src.market_report.publisher import publish_docs
        published = publish_docs(f"🪙 {date_str} 코인 시세 리포트 ({now.strftime('%H:%M')})")
        logger.info("coin_report_published ok=%s url=%s", published, url)

    text = format_coin_telegram(rows, fng=fng, glob=glob, fx=fx, now=now,
                                url=url if published else "")
    if send:
        sent = await _send_telegram(text)
        logger.info("coin_report_sent ok=%s", sent)
    else:
        print(text)

    return {"rows": len(rows), "fng": fng is not None, "glob": glob is not None,
            "fx": fx, "published": published}


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description="코인 시세 리포트 1회 실행")
    ap.add_argument("--no-send", action="store_true", help="텔레그램 발송 생략(콘솔 출력)")
    ap.add_argument("--no-publish", action="store_true", help="웹(git push) 발행 생략")
    a = ap.parse_args()
    res = asyncio.run(run_coin_report(send=not a.no_send, publish=not a.no_publish))
    raise SystemExit(0 if res else 1)


if __name__ == "__main__":
    main()
