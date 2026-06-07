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


def _macd_label(closes: list[float]) -> str | None:
    """MACD 상태 라벨 — '양/음(0선 기준)·골든/데드(시그널 교차)'. 데이터 부족 시 None."""
    if len(closes) < 35:  # slow26 + signal9
        return None
    from src.indicators.core import macd as _macd
    macd_line, signal_line, _hist = _macd(closes)
    m, s = macd_line[-1], signal_line[-1]
    if m is None or s is None:
        return None
    return f"{'양' if m >= 0 else '음'}·{'골든' if m >= s else '데드'}"


def _tf_analysis(candles: list, strategies: list, change_pct: float | None = None) -> dict:
    """한 타임프레임(일봉 or 4H봉) 분석 — 신호등·20/60이격·RSI·MACD·ABCDE 전략(공용)."""
    closes = [c.close for c in candles]
    gaps = compute_gaps(closes)
    em, nm = coin_phase(gaps)
    strats: list[str] = []
    if strategies:
        try:
            from src.screener.engine import screen_stock
            strats = [m.strategy_name.split(".")[0].strip()
                      for m in screen_stock(strategies, candles, change_pct)]
        except Exception as exc:  # noqa: BLE001 — 전략평가 실패가 리포트를 막지 않도록
            logger.warning("coin_screen_failed error=%s", exc)
    return {"phase_emoji": em, "phase_name": nm,
            "g20": gaps[20], "g60": gaps[60], "rsi": gaps["rsi"],
            "macd": _macd_label(closes), "strats": strats}


def analyze_coin(
    daily: list, h4: list, strategies: list, fng_score: float | None,
    change_pct: float | None = None,
) -> dict | None:
    """일봉·4H 각각 신호등·이격·RSI·MACD·ABCDE(주식 엔진 무수정 재사용, 사용자 2026-06-07).

    E = oversold_leader(일봉) + 4H RSI≤30 게이트 → 일봉 전략에 부착(주식과 동일 골격).
    코인 시장게이트 = 코인 F&G≤25(주식의 지수RSI/F&G 대응). 일봉 60봉 미만 → None."""
    if len(daily) < 60:
        return None
    d = _tf_analysis(daily, strategies, change_pct)
    h = _tf_analysis(h4, strategies) if len(h4) >= 20 else {
        "phase_emoji": "⚪", "phase_name": "", "g20": None, "g60": None,
        "rsi": None, "macd": None, "strats": []}

    e_bottom = False
    try:
        from src.patterns.core import oversold_leader
        ol = oversold_leader(daily)
        if ol.matched and h["rsi"] is not None and h["rsi"] <= 30:
            d["strats"].append("E")
            e_bottom = fng_score is not None and fng_score <= 25
    except Exception as exc:  # noqa: BLE001
        logger.warning("coin_e_eval_failed error=%s", exc)

    return {"daily": d, "h4": h, "e_bottom": e_bottom}


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


def _tf_text(tf: dict | None, units: tuple[str, str], e_bottom: bool = False) -> str:
    """타임프레임 분석 → '신호등 · 이격 · RSI · MACD · 전략' 한 줄(결측 항목 생략)."""
    if not tf:
        return ""
    parts = []
    if tf.get("phase_name"):
        parts.append(f"{tf.get('phase_emoji', '')}{tf['phase_name']}")
    if tf.get("g20") is not None:
        parts.append(f"{units[0]} {tf['g20']:+.1f}%")
    if tf.get("g60") is not None:
        parts.append(f"{units[1]} {tf['g60']:+.1f}%")
    if tf.get("rsi") is not None:
        parts.append(f"RSI {tf['rsi']:.0f}")
    if tf.get("macd"):
        parts.append(f"MACD {tf['macd']}")
    # 전략은 미매칭이어도 '없음' 명시 — 누락으로 오인 방지(사용자 2026-06-07)
    if tf.get("strats"):
        s = "전략 " + "·".join(tf["strats"])
        if e_bottom:
            s += " 🔥시장동반바닥"
        parts.append(s)
    elif parts:
        parts.append("전략 없음")
    return " · ".join(parts)


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
    for i, r in enumerate(rows, start=1):
        seg = [f"{i}. {r['name_ko']}({r['sym']})"]
        if r["krw"] is not None:
            seg.append(f"{_fmt_krw(r['krw'])}원 ({_fmt_pct(r['krw_change'])})")
        if r["usd"] is not None:
            seg.append(f"{_fmt_usd(r['usd'])} ({_fmt_pct(r['usd_change'])})")
        if r["kimchi"] is not None:
            seg.append(f"김프 {r['kimchi']:+.1f}%")
        lines.append(" · ".join(seg))
        # 텔레그램은 신호등 + 전략 여부만(사용자 2026-06-07 정보과다 축소) — 상세는 웹 전용
        a = r.get("analysis")
        if a:
            sig = []
            d = a.get("daily") or {}
            if d.get("phase_name"):
                sig.append(f"일봉 {d.get('phase_emoji', '')}{d['phase_name']}")
            h = a.get("h4") or {}
            if h.get("phase_name"):
                sig.append(f"4시간봉 {h.get('phase_emoji', '')}{h['phase_name']}")
            if sig:
                # 전략(일봉 기준): 미매칭이면 '없음' 명시 — 누락 오인 방지(사용자 2026-06-07)
                if d.get("strats"):
                    s = "전략 " + "·".join(d["strats"])
                    if a.get("e_bottom"):
                        s += " 🔥시장동반바닥"
                else:
                    s = "전략 없음"
                sig.append(s)
                lines.append("   ㄴ" + " · ".join(sig))
    lines.append("")
    lines.append(DISCLAIMER)
    if url:
        lines.append(f"🔗 {url}")
    return "\n".join(lines)


def render_coin_html(
    rows: list[dict], *, fng: dict | None, glob: dict | None, fx: float, now: datetime,
) -> str:
    """독립 HTML 리포트 — 모바일 우선 카드 레이아웃(사용자 2026-06-07).

    광폭 테이블은 모바일에서 가로 넘침·글자 짤림 → 코인당 1카드(좁은 화면 1열, ≥640px 2열)."""
    date_str = now.strftime("%Y-%m-%d")

    def chg_cls(v: float | None) -> str:
        if v is None:
            return ""
        return "up" if v >= 0 else "down"

    cards = []
    for r in rows:
        a = r.get("analysis") or {}
        daily = a.get("daily") or {}
        phase = (f"<span class='phase'>{daily['phase_emoji']}{daily['phase_name']}</span>"
                 if daily.get("phase_name") else "")
        krw_html = ""
        if r["krw"] is not None:
            krw_html = (f"<div class='krw'>{_fmt_krw(r['krw'])}<span class='won'>원</span> "
                        f"<span class='{chg_cls(r['krw_change'])}'>{_fmt_pct(r['krw_change'])}</span></div>")
        sub = []
        if r["usd"] is not None:
            sub.append(f"{_fmt_usd(r['usd'])} "
                       f"<span class='{chg_cls(r['usd_change'])}'>{_fmt_pct(r['usd_change'])}</span>")
        if r["kimchi"] is not None:
            sub.append(f"김프 <span class='{chg_cls(r['kimchi'])}'>{r['kimchi']:+.2f}%</span>")
        sub_html = f"<div class='sub'>{' · '.join(sub)}</div>" if sub else ""
        ind = []  # 일봉/4시간봉 각각 신호등·이격·RSI·MACD·전략(텔레그램과 동일, _tf_text 공용)
        day_txt = _tf_text(a.get("daily"), ("20일", "60일"), e_bottom=a.get("e_bottom", False))
        if day_txt:
            ind.append(f"일봉: {day_txt}")
        h4_txt = _tf_text(a.get("h4"), ("20MA", "60MA"))
        if h4_txt:
            ind.append(f"4시간봉: {h4_txt}")
        ind_html = f"<div class='ind'>{'<br>'.join(ind)}</div>" if ind else ""
        cards.append(
            "<div class=\"card\">"
            f"<div class='head'><span class='cname'>{r['name_ko']} "
            f"<span class='sym'>{r['sym']}</span></span>{phase}</div>"
            f"{krw_html}{sub_html}{ind_html}"
            "</div>"
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
    senti_html = "".join(f"<span class='chip'>{s}</span>" for s in senti)
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>🪙 코인 시세 리포트 {date_str}</title>
<style>
  :root {{ --bg:#0f1117; --card:#1a1d27; --text:#e8eaf0; --muted:#8b91a5;
           --up:#2ecc71; --down:#e74c3c; --line:#2a2e3d; }}
  * {{ box-sizing:border-box; }}
  body {{ background:var(--bg); color:var(--text); font-family:'Segoe UI',sans-serif;
          margin:0; padding:12px; overflow-wrap:anywhere; }}
  .wrap {{ max-width:760px; margin:0 auto; }}
  h1 {{ font-size:1.15rem; margin:6px 0 10px; line-height:1.4; }}
  .senti {{ display:flex; flex-wrap:wrap; gap:6px; margin:10px 0 14px; }}
  .chip {{ background:var(--card); border:1px solid var(--line); border-radius:8px;
           padding:6px 10px; font-size:.85rem; line-height:1.5; }}
  .cards {{ display:grid; grid-template-columns:1fr; gap:10px; }}
  @media (min-width:640px) {{ .cards {{ grid-template-columns:1fr 1fr; }} }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:12px;
           padding:12px 14px; min-width:0; }}
  .head {{ display:flex; justify-content:space-between; align-items:center; gap:8px; }}
  .cname {{ font-weight:700; font-size:1rem; }}
  .phase {{ font-size:.85rem; white-space:nowrap; }}
  .krw {{ font-size:1.25rem; font-weight:700; margin-top:6px;
          font-variant-numeric:tabular-nums; }}
  .won {{ font-size:.85rem; font-weight:400; color:var(--muted); margin:0 4px 0 1px; }}
  .krw .up, .krw .down {{ font-size:.95rem; }}
  .sub {{ color:var(--text); font-size:.9rem; margin-top:4px;
          font-variant-numeric:tabular-nums; }}
  .ind {{ color:var(--muted); font-size:.82rem; margin-top:8px; line-height:1.6;
          border-top:1px solid var(--line); padding-top:8px;
          font-variant-numeric:tabular-nums; }}
  .strat {{ margin-top:6px; font-size:.85rem; color:#f5c542; }}
  .sym {{ color:var(--muted); font-size:.8rem; font-weight:400; }}
  .up {{ color:var(--up); }} .down {{ color:var(--down); }}
  .disclaimer {{ color:var(--muted); font-size:.78rem; margin-top:16px; line-height:1.6; }}
</style>
</head>
<body>
<div class="wrap">
<h1>🪙 코인 시세 리포트 <span class="sym">{date_str} {now.strftime('%H:%M')} KST</span></h1>
<div class="senti">{senti_html}</div>
<div class="cards">
{''.join(cards)}
</div>
<p class="disclaimer">{DISCLAIMER}<br>
소스: 업비트(KRW) · CoinGecko(USD·도미넌스) · alternative.me(공포탐욕). 김치프리미엄 = 업비트 ÷ (글로벌×환율) − 1.<br>
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
            if not c.get("analyze", True):  # USDT 등 스테이블 — 전략/국면 오탐 방지
                continue
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
