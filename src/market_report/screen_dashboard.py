"""스크린 대시보드 — A/B/C 전략별·일자별 포착 종목 + 차트 매수/매도 시그널.

웹페이지(GitHub Pages)에 게시되는 대시보드를 생성한다:
  - 전략(A/B/C) 탭 + 일자별 그룹 + 포착 종목 리스트(근거 병기)
  - 종목별 lightweight 인터랙티브 차트(매수 ▲ / 매도 ▼ 마커)로 시그널 위치 확인

데이터: 유니버스(관심+핫+주도주) 각 종목의 일봉을 1회 수집 → 최근 N거래일을
각 전략으로 오프라인 평가(candles[:i+1]). 매수=전략 신규 진입일, 매도=손절일
(diagnose_holding STOP). 결과를 JSON + HTML로 docs/reports/에 기록.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import datetime
from pathlib import Path

from src.market_report import chart_lightweight as lw
from src.patterns.core import diagnose_holding
from src.screener.config import load_screener_config
from src.screener.engine import evaluate_strategy
from src.screener.pipeline import _is_etf

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
REPORTS_DIR = PROJECT_ROOT / "docs" / "reports"
DASHBOARD_HTML = REPORTS_DIR / "screen-dashboard.html"
DASHBOARD_JSON = REPORTS_DIR / "screen-dashboard.json"

# 검증된 주도주 (유니버스 보강 — 핫종목에 안 잡혀도 추세 추적)
LEADERS = {
    "000660": "SK하이닉스", "005930": "삼성전자", "009150": "삼성전기",
    "011070": "LG이노텍", "066570": "LG전자", "005380": "현대차",
    "307950": "현대오토에버", "018260": "삼성에스디에스",
}
_STOP_STATES = {"STOP20", "STOP60", "BREAKDOWN"}


def _dedupe_clusters(idxs: list[int], gap: int = 3) -> list[int]:
    """연속/근접 인덱스의 첫날만 남김 (마커 과밀 방지)."""
    out: list[int] = []
    prev = -999
    for i in sorted(idxs):
        if i - prev > gap:
            out.append(i)
        prev = i
    return out


async def collect_dashboard_data(adapter, days_back: int = 12, end_date: str | None = None) -> dict:
    """유니버스 일봉 1회 수집 → 일자별·전략별 포착 + 종목별 시그널 마커."""
    cfg = load_screener_config()
    strategies = cfg.enabled_strategies()
    min_price = cfg.global_filters.get("min_price", 0)
    exclude_etf = cfg.global_filters.get("exclude_etf", False)

    # 유니버스: 주도주 + 핫종목(거래량·등락률 상위)
    universe = dict(LEADERS)
    try:
        from src.datasource.base import RankingKind
        for kind in (RankingKind.VOLUME, RankingKind.CHANGE_PCT):
            for r in await adapter.get_ranking(kind, top=cfg.hot_stocks_top):
                if r.ticker and r.ticker not in universe:
                    universe[r.ticker] = r.name
    except Exception as exc:
        logger.warning("dashboard_ranking_failed error=%s", exc)

    # 종목별 일봉 1회 수집
    cmap: dict[str, list] = {}
    names: dict[str, str] = {}
    for tk, nm in universe.items():
        if exclude_etf and _is_etf(nm):
            continue
        await asyncio.sleep(random.uniform(0.2, 0.5))
        try:
            c = await adapter.get_ohlcv(tk, days=220, end_date=end_date)
        except Exception:
            continue
        if len(c) >= 135 and c[-1].close >= min_price:
            cmap[tk] = c
            names[tk] = nm

    if not cmap:
        return {"generated_at": datetime.now().isoformat(), "strategies": [], "by_strategy": {}, "charts": {}}

    # 평가 대상 거래일 (최근 days_back)
    sample = max(cmap.values(), key=len)
    all_dates = sorted({x.date for x in sample})
    target_dates = all_dates[-days_back:]

    # by_strategy[strategy][date] = [ {ticker,name,reason,endstage} ]
    by_strategy: dict[str, dict[str, list]] = {s.name: {} for s in strategies}
    # 종목별 매수(진입)·매도(손절) 인덱스
    buy_idx: dict[str, list[int]] = {tk: [] for tk in cmap}
    sell_idx: dict[str, list[int]] = {tk: [] for tk in cmap}
    matched_tickers: set[str] = set()

    for tk, c in cmap.items():
        idx_by_date = {x.date: i for i, x in enumerate(c)}
        for d in target_dates:
            i = idx_by_date.get(d)
            if i is None or i < 130:
                continue
            sub = c[: i + 1]
            change_pct = 0.0
            if i >= 1 and c[i - 1].close > 0:
                change_pct = (c[i].close - c[i - 1].close) / c[i - 1].close * 100
            for s in strategies:
                res = evaluate_strategy(s.name, s.opinion, s.conditions, sub, change_pct)
                if res.matched:
                    by_strategy.setdefault(s.name, {}).setdefault(d, []).append({
                        "ticker": tk, "name": names[tk],
                        "reason": res.reason if hasattr(res, "reason") else "; ".join(res.reasons),
                        "endstage": bool(res.metrics.get("endstage")),
                        "price": round(c[i].close, 1),
                    })
                    buy_idx[tk].append(i)
                    matched_tickers.add(tk)
            # 손절 마커 (보유 가정)
            diag = diagnose_holding(sub)
            if diag.metrics.get("state") in _STOP_STATES:
                sell_idx[tk].append(i)

    # 차트 생성 (포착된 종목만) — 매수/매도 마커
    charts: dict[str, str] = {}
    date_str = (end_date and f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}") or datetime.now().strftime("%Y-%m-%d")
    for tk in sorted(matched_tickers):
        c = cmap[tk]
        bdays = [c[i].date for i in _dedupe_clusters(buy_idx[tk])]
        sdays = [c[i].date for i in _dedupe_clusters(sell_idx[tk])]
        try:
            out = lw.render_interactive(c, tk, names[tk], date=date_str,
                                        signal_dates=bdays, sell_dates=sdays)
            if out:
                charts[tk] = f"charts_interactive/{out.name}"
        except Exception as exc:
            logger.warning("dashboard_chart_failed ticker=%s error=%s", tk, exc)

    # JSON 직렬화 가능 구조로 (날짜 내림차순)
    out_by_strategy = {}
    for sname, daymap in by_strategy.items():
        out_by_strategy[sname] = {
            d: daymap[d] for d in sorted(daymap.keys(), reverse=True)
        }

    # ── 시장 개요 (KIS 직접 — stockeasy 대체) ──────────────────────────────
    market: dict = {}
    # 52주(최대 250일) 신고가 돌파 — 보유 candles로 계산 (추가 호출 없음)
    new_highs = []
    for tk, c in cmap.items():
        lb = min(len(c), 250)
        if c[-1].close >= max(x.high for x in c[-lb:]):
            new_highs.append({"ticker": tk, "name": names[tk],
                              "price": round(c[-1].close, 1), "lookback": lb})
    market["new_highs"] = sorted(new_highs, key=lambda x: -x["price"])[:20]
    # 업종 등락 (코스피)
    try:
        sectors = await adapter.get_sector_prices("K")
        sectors = [s for s in sectors if s["code"] != "0001"]  # 종합 제외
        sectors.sort(key=lambda x: x["change_pct"], reverse=True)
        market["sectors_up"] = sectors[:6]
        market["sectors_down"] = sectors[-6:][::-1]
    except Exception as exc:
        logger.warning("dashboard_sectors_failed error=%s", exc)
    # 외국인·기관 순매수 상위
    try:
        market["foreign_buy"] = (await adapter.get_investor_net_buy("foreign", "buy"))[:10]
        market["inst_buy"] = (await adapter.get_investor_net_buy("inst", "buy"))[:10]
    except Exception as exc:
        logger.warning("dashboard_investor_failed error=%s", exc)

    return {
        "generated_at": datetime.now().isoformat(),
        "date_str": date_str,
        "strategies": [s.name for s in strategies],
        "by_strategy": out_by_strategy,
        "charts": charts,
        "market": market,
    }


def render_dashboard_html(data: dict) -> str:
    """대시보드 HTML — 전략 탭 + 일자별 그룹 + 종목 리스트(차트 링크)."""
    strategies = data["strategies"]
    by_strategy = data["by_strategy"]
    charts = data["charts"]
    gen = data.get("generated_at", "")[:16].replace("T", " ")

    def _fmt_date(d: str) -> str:
        return f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else d

    tabs = []
    panels = []
    for si, sname in enumerate(strategies):
        active = " active" if si == 0 else ""
        short = sname.split(".")[0].strip() or sname[:1]
        daymap = by_strategy.get(sname, {})
        total = sum(len(v) for v in daymap.values())
        tabs.append(f'<button class="tab{active}" onclick="showTab({si})">{short} <span class="cnt">{total}</span></button>')

        day_blocks = []
        if not daymap:
            day_blocks.append('<p class="empty">포착 종목 없음</p>')
        for d, stocks in daymap.items():
            rows = []
            for s in stocks:
                warn = ' <span class="warn">⚠️끝물</span>' if s.get("endstage") else ""
                url = charts.get(s["ticker"])
                link_open = f'<a href="{url}" target="_blank">' if url else "<span>"
                link_close = "</a>" if url else "</span>"
                rows.append(
                    f'<li>{link_open}<b>{s["name"]}</b> <code>{s["ticker"]}</code>{link_close} '
                    f'<span class="px">{s["price"]:,.0f}</span>{warn}'
                    f'<div class="reason">{s["reason"]}</div></li>'
                )
            day_blocks.append(
                f'<div class="day"><h3>{_fmt_date(d)} <span class="dcnt">{len(stocks)}</span></h3>'
                f'<ul>{"".join(rows)}</ul></div>'
            )
        panels.append(f'<div class="panel{active}" id="panel{si}">{"".join(day_blocks)}</div>')

    # 📊 시장개요 탭 (KIS 직접 — 섹터·투자자 순매수·52주 신고가)
    mkt = data.get("market", {})
    mi = len(strategies)
    tabs.append(f'<button class="tab" onclick="showTab({mi})">📊 시장</button>')
    chart_for = lambda tk: charts.get(tk)

    def _stock_li(s, extra=""):
        url = chart_for(s.get("ticker", ""))
        op = f'<a href="{url}" target="_blank">' if url else "<span>"
        cl = "</a>" if url else "</span>"
        sign = "+" if s.get("change_pct", 0) >= 0 else ""
        return (f'<li>{op}<b>{s["name"]}</b> <code>{s.get("ticker","")}</code>{cl} '
                f'<span class="px">{sign}{s.get("change_pct",0):.1f}%</span>'
                f'{(" <span class=reason>"+extra+"</span>") if extra else ""}</li>')

    blocks = []
    if mkt.get("sectors_up") or mkt.get("sectors_down"):
        up = "".join(f'<li><b>{s["name"]}</b> <span class="px">+{s["change_pct"]:.2f}%</span></li>'
                     for s in mkt.get("sectors_up", []))
        dn = "".join(f'<li><b>{s["name"]}</b> <span class="px" style="color:var(--red)">{s["change_pct"]:.2f}%</span></li>'
                     for s in mkt.get("sectors_down", []))
        blocks.append(f'<div class="day"><h3>🔥 강세 업종</h3><ul>{up}</ul></div>')
        blocks.append(f'<div class="day"><h3>❄️ 약세 업종</h3><ul>{dn}</ul></div>')
    if mkt.get("foreign_buy"):
        fb = "".join(_stock_li(s, f'외인순매수 {s.get("frgn_net_value",0):,}백만') for s in mkt["foreign_buy"])
        blocks.append(f'<div class="day"><h3>🌏 외국인 순매수 상위</h3><ul>{fb}</ul></div>')
    if mkt.get("inst_buy"):
        ib = "".join(_stock_li(s, f'기관순매수 {s.get("orgn_net_value",0):,}백만') for s in mkt["inst_buy"])
        blocks.append(f'<div class="day"><h3>🏛️ 기관 순매수 상위</h3><ul>{ib}</ul></div>')
    if mkt.get("new_highs"):
        nh = "".join(_stock_li(s, f'{s.get("lookback",250)}일 신고가') for s in mkt["new_highs"])
        blocks.append(f'<div class="day"><h3>🚀 52주(최대) 신고가 돌파</h3><ul>{nh}</ul></div>')
    if not blocks:
        blocks.append('<p class="empty">시장 데이터 수집 실패</p>')
    panels.append(f'<div class="panel" id="panel{mi}">{"".join(blocks)}</div>')

    return _TEMPLATE.format(
        gen=gen, tabs="".join(tabs), panels="".join(panels),
        date_str=data.get("date_str", ""),
    )


_TEMPLATE = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>전략 스크린 대시보드</title>
<style>
:root {{ --bg:#0d1117; --card:#161b22; --line:#30363d; --txt:#e6edf3; --sub:#8b949e;
  --green:#3fb950; --red:#f85149; --accent:#58a6ff; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--txt); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Apple SD Gothic Neo","Malgun Gothic",sans-serif; }}
header {{ padding:18px 16px; border-bottom:1px solid var(--line); }}
h1 {{ margin:0; font-size:18px; }}
.meta {{ color:var(--sub); font-size:12px; margin-top:4px; }}
.tabs {{ display:flex; gap:6px; padding:12px 16px 0; flex-wrap:wrap; }}
.tab {{ background:var(--card); color:var(--txt); border:1px solid var(--line); border-radius:8px 8px 0 0;
  padding:9px 16px; cursor:pointer; font-size:14px; font-weight:600; }}
.tab.active {{ border-bottom:2px solid var(--accent); color:var(--accent); }}
.cnt {{ background:var(--line); border-radius:10px; padding:1px 7px; font-size:11px; margin-left:4px; }}
.panel {{ display:none; padding:16px; }}
.panel.active {{ display:block; }}
.day {{ margin-bottom:18px; }}
.day h3 {{ font-size:14px; color:var(--accent); margin:0 0 8px; border-bottom:1px solid var(--line); padding-bottom:5px; }}
.dcnt {{ color:var(--sub); font-size:12px; font-weight:400; }}
ul {{ list-style:none; margin:0; padding:0; }}
li {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:10px 12px; margin-bottom:7px; }}
li a {{ color:var(--txt); text-decoration:none; border-bottom:1px dashed var(--accent); }}
li code {{ color:var(--sub); font-size:12px; }}
.px {{ float:right; color:var(--green); font-weight:600; }}
.warn {{ color:#d29922; font-size:12px; }}
.reason {{ color:var(--sub); font-size:12px; margin-top:4px; line-height:1.4; }}
.empty {{ color:var(--sub); font-style:italic; }}
.note {{ color:var(--sub); font-size:11px; padding:0 16px 24px; line-height:1.5; }}
</style></head><body>
<header><h1>📊 전략 스크린 대시보드</h1>
<div class="meta">기준일 {date_str} · 생성 {gen} · 차트 클릭 → 매수 ▲ / 매도 ▼ 시그널 위치 확인</div></header>
<div class="tabs">{tabs}</div>
{panels}
<p class="note">※ 매수 ▲(초록)=전략 신규 진입 신호일, 매도 ▼(빨강)=손절 신호일(20·60선 이탈/추세붕괴).
참고용 정보이며 매매 판단·책임은 본인에게 있습니다. 자동 주문은 실행하지 않습니다.</p>
<script>
function showTab(n) {{
  document.querySelectorAll('.tab').forEach((t,i)=>t.classList.toggle('active', i===n));
  document.querySelectorAll('.panel').forEach((p,i)=>p.classList.toggle('active', i===n));
}}
</script></body></html>"""


async def build_dashboard(adapter, days_back: int = 12, end_date: str | None = None) -> Path:
    """대시보드 데이터 수집 → JSON + HTML 기록. HTML 경로 반환."""
    data = await collect_dashboard_data(adapter, days_back=days_back, end_date=end_date)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    DASHBOARD_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    html = render_dashboard_html(data)
    DASHBOARD_HTML.write_text(html, encoding="utf-8")
    n = sum(len(v) for v in data["by_strategy"].values())
    logger.info("dashboard_built picks=%d charts=%d path=%s", n, len(data["charts"]), DASHBOARD_HTML)
    return DASHBOARD_HTML


def dashboard_url() -> str:
    """게시된 대시보드 URL."""
    from src.market_report.publisher import GITHUB_PAGES_BASE
    return f"{GITHUB_PAGES_BASE}/reports/screen-dashboard.html"


def publish_dashboard() -> bool:
    """docs/ 변경분(대시보드+차트) git push. publisher와 동일 스코프(docs/만)."""
    from src.market_report.publisher import _run_git

    ok, _ = _run_git("add", "docs/")
    if not ok:
        return False
    if _run_git("diff", "--cached", "--quiet")[0]:
        logger.info("dashboard_publish_no_changes")
        return True
    date = datetime.now().strftime("%Y-%m-%d %H:%M")
    if not _run_git("commit", "-m", f"📊 {date} 전략 스크린 대시보드 갱신")[0]:
        return False
    ok, msg = _run_git("push", "origin", "main", timeout=120)
    if not ok:
        logger.error("dashboard_publish_push_failed error=%s", msg)
        return False
    logger.info("dashboard_published")
    return True


async def run_dashboard_job(days_back: int = 12, do_publish: bool = True) -> Path:
    """단일 진입점 — 어댑터 조립 → 대시보드 빌드 → (옵션) 게시. 스케줄러/CLI 공용."""
    from src.config.settings import get_settings
    from src.datasource.kis.adapter import KisAdapter

    s = get_settings()
    adapter = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)
    path = await build_dashboard(adapter, days_back=days_back)
    if do_publish:
        try:
            publish_dashboard()
        except Exception as exc:
            logger.error("dashboard_publish_failed error=%s", exc)
    return path
