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


def compute_gaps(closes: list[float]) -> dict:
    """일봉 이평 이격(%) {5,20,60,120} + RSI + 전일 5일이격(g5_prev, 상승전환 판정). 부족분 None."""
    from src.indicators.core import moving_average
    from src.indicators.core import rsi as _rsi
    out: dict = {}
    last = closes[-1] if closes else None
    for n in (5, 20, 60, 120):
        ma = moving_average(closes, n)[-1] if len(closes) >= n else None
        out[n] = (last - ma) / ma * 100 if (ma and last is not None) else None
    r = _rsi(closes, 14) if len(closes) >= 15 else None
    out["rsi"] = r[-1] if r and r[-1] is not None else None
    out["g5_prev"] = None
    if len(closes) >= 6:
        prev = closes[:-1]
        ma5p = moving_average(prev, 5)[-1]
        if ma5p:
            out["g5_prev"] = (prev[-1] - ma5p) / ma5p * 100
    return out


# 코인 과열 임계 — 주식(_market_phase: 나스닥12/코스피40)보다 변동성이 커서 별도 보정.
# BTC 강세장 120일이격 +30%대가 일상이라 주식 임계(12%) 그대로면 상시 과열 오탐.
_COIN_OVERHEAT_120 = 30.0
_COIN_OVERHEAT_60 = 20.0


def coin_phase(gaps: dict) -> tuple[str, str]:
    """코인 일봉 국면 신호등 — 주식 _market_phase 골격(비대칭: 바닥 신뢰>고점) + 코인 임계.

    주봉/월봉/CCI 단계는 v1 미적용(데이터 200봉). (이모지, 국면명) 반환."""
    g5, g20, g60, g120 = (gaps.get(k) for k in (5, 20, 60, 120))
    rv = gaps.get("rsi")
    if g120 is None or g60 is None:
        return ("⚪", "판단불가")
    if (rv is not None and rv <= 30) or g60 <= -7:
        return ("🔵", "바닥권")
    if (g120 >= _COIN_OVERHEAT_120 or g60 >= _COIN_OVERHEAT_60) \
            and (rv is None or rv >= 70):
        return ("🔴", "과열")
    g5_prev = gaps.get("g5_prev")
    if g5_prev is not None and g5_prev < 0 and g5 is not None and g5 >= 0 \
            and g20 is not None and g20 >= 0:
        return ("🔼", "상승전환")
    if g60 < 0:
        return ("🔻", "하락전환")
    if g20 is not None and g20 < 0:
        return ("🟠", "조정")
    if g5 is not None and g5 < 0:
        return ("🟡", "단기눌림")
    return ("🟢", "정상")


def analyze_coin(
    daily: list, h4: list, strategies: list, fng_score: float | None,
    change_pct: float | None = None,
) -> dict | None:
    """일봉·4H 이격/RSI + 국면 + ABCDE 전략 평가(주식 엔진 무수정 재사용, 사용자 2026-06-07).

    E = oversold_leader + 4H RSI≤30 (주식과 동일 골격). 코인 시장게이트 = 코인 F&G≤25
    (주식의 지수RSI/F&G 게이트 대응). 일봉 60봉 미만 → None(분석 생략)."""
    if len(daily) < 60:
        return None
    closes = [c.close for c in daily]
    gaps = compute_gaps(closes)
    em, nm = coin_phase(gaps)

    h4_rsi_v: float | None = None
    h4_g20: float | None = None
    h4_closes = [c.close for c in h4]
    if len(h4_closes) >= 20:
        from src.indicators.core import moving_average
        from src.indicators.core import rsi as _rsi
        r = _rsi(h4_closes, 14)
        h4_rsi_v = r[-1] if r and r[-1] is not None else None
        ma = moving_average(h4_closes, 20)[-1]
        if ma:
            h4_g20 = (h4_closes[-1] - ma) / ma * 100

    strats: list[str] = []
    if strategies:
        try:
            from src.screener.engine import screen_stock
            strats = [m.strategy_name.split(".")[0].strip()
                      for m in screen_stock(strategies, daily, change_pct)]
        except Exception as exc:  # noqa: BLE001 — 전략평가 실패가 리포트를 막지 않도록
            logger.warning("coin_screen_failed error=%s", exc)

    e_bottom = False
    try:
        from src.patterns.core import oversold_leader
        ol = oversold_leader(daily)
        if ol.matched and h4_rsi_v is not None and h4_rsi_v <= 30:
            strats.append("E")
            e_bottom = fng_score is not None and fng_score <= 25
    except Exception as exc:  # noqa: BLE001
        logger.warning("coin_e_eval_failed error=%s", exc)

    return {"phase_emoji": em, "phase_name": nm,
            "g20": gaps[20], "g60": gaps[60], "rsi": gaps["rsi"],
            "h4_rsi": h4_rsi_v, "h4_g20": h4_g20,
            "strats": strats, "e_bottom": e_bottom}


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
        a = r.get("analysis")
        if a:  # 일봉 국면·이격 + 4H + ABCDE 전략(주식 리포트와 동일 표현, 사용자 2026-06-07)
            parts = []
            head = f"일봉 {a['phase_emoji']}{a['phase_name']}"
            detail = []
            if a.get("g20") is not None:
                detail.append(f"20일 {a['g20']:+.1f}%")
            if a.get("g60") is not None:
                detail.append(f"60일 {a['g60']:+.1f}%")
            if a.get("rsi") is not None:
                detail.append(f"RSI {a['rsi']:.0f}")
            if detail:
                head += " (" + "·".join(detail) + ")"
            parts.append(head)
            if a.get("h4_rsi") is not None:
                h4s = f"4H RSI {a['h4_rsi']:.0f}"
                if a.get("h4_g20") is not None:
                    h4s += f" (20MA {a['h4_g20']:+.1f}%)"
                parts.append(h4s)
            if a.get("strats"):
                s = "·".join(a["strats"]) + " 시그널"
                if a.get("e_bottom"):
                    s += " 🔥시장동반바닥"
                parts.append(s)
            lines.append("  └ " + " · ".join(parts))
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
        a = r.get("analysis") or {}
        if a:
            detail = []
            if a.get("g20") is not None:
                detail.append(f"20일 {a['g20']:+.1f}%")
            if a.get("g60") is not None:
                detail.append(f"60일 {a['g60']:+.1f}%")
            if a.get("rsi") is not None:
                detail.append(f"RSI {a['rsi']:.0f}")
            if a.get("h4_rsi") is not None:
                h4s = f"4H RSI {a['h4_rsi']:.0f}"
                if a.get("h4_g20") is not None:
                    h4s += f"(20MA {a['h4_g20']:+.1f}%)"
                detail.append(h4s)
            phase_cell = (f"{a['phase_emoji']}{a['phase_name']}<br>"
                          f"<span class='sym'>{' · '.join(detail)}</span>")
            strat_cell = "·".join(a.get("strats") or []) or "—"
            if a.get("e_bottom"):
                strat_cell += " 🔥"
        else:
            phase_cell, strat_cell = "—", "—"
        body_rows.append(
            "<tr>"
            f"<td class='name'>{r['name_ko']} <span class='sym'>{r['sym']}</span></td>"
            f"<td class='num'>{_fmt_krw(r['krw'])}</td>"
            f"<td class='num {chg_cls(r['krw_change'])}'>{_fmt_pct(r['krw_change'])}</td>"
            f"<td class='num'>{_fmt_usd(r['usd'])}</td>"
            f"<td class='num {chg_cls(r['usd_change'])}'>{_fmt_pct(r['usd_change'])}</td>"
            f"<td class='num {chg_cls(r['kimchi'])}'>"
            f"{('%+.2f%%' % r['kimchi']) if r['kimchi'] is not None else '—'}</td>"
            f"<td>{phase_cell}</td>"
            f"<td class='num'>{strat_cell}</td>"
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
<th>글로벌(USD)</th><th>24h</th><th>김치프리미엄</th><th>일봉·4H 상태</th><th>전략</th></tr></thead>
<tbody>
{''.join(body_rows)}
</tbody>
</table>
<p class="disclaimer">{DISCLAIMER}<br>
소스: 업비트(KRW) · CoinGecko(USD·도미넌스) · alternative.me(공포탐욕). 김프 = 업비트 ÷ (글로벌×환율) − 1.<br>
전략(A~E) = 주식 스크리너와 동일 로직을 코인 일봉에 적용(참고용). E 🔥 = 코인 공포탐욕≤25 시장동반바닥.</p>
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

    # 일봉·4H 이격/국면 + ABCDE 전략 평가 (사용자 2026-06-07). 실패해도 시세 리포트는 계속.
    try:
        from src.datasource.coin.sources import fetch_upbit_4h, fetch_upbit_daily
        from src.screener.config import load_screener_config
        strategies = load_screener_config().enabled_strategies()
        fng_score = (fng or {}).get("score")
        for row, c in zip(rows, COIN_UNIVERSE):
            daily = await fetch_upbit_daily(c["upbit"])
            await asyncio.sleep(random.uniform(0.3, 0.8))  # §7 랜덤 딜레이(업비트 공개API)
            h4 = await fetch_upbit_4h(c["upbit"])
            await asyncio.sleep(random.uniform(0.3, 0.8))
            a = analyze_coin(daily, h4, strategies, fng_score,
                             change_pct=row.get("krw_change"))
            if a:
                row["analysis"] = a
        logger.info("coin_analysis_done analyzed=%d", sum(1 for r in rows if r.get("analysis")))
    except Exception as exc:  # noqa: BLE001
        logger.warning("coin_analysis_failed error=%s", exc)
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
