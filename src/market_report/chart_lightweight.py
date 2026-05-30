"""Lightweight-charts 인터랙티브 차트 — 웹 리포트용.

TradingView lightweight-charts v4 기반. Plotly 대비 번들 크기 1/10 수준,
렌더 성능 대폭 개선 (Canvas 직접 렌더).

구성:
- 메인: 캔들 + MA5/10/20/60/120 + 볼린저밴드 + 일목구름 (선행스팬A/B 채움)
- 보조1: 거래량 히스토그램
- 보조2: MACD (라인 + 시그널 + 히스토그램)
- 보조3: RSI + 30/70 가이드라인
- 일/주/월봉 토글 (버튼)
- 매수(초록▲) / 매도(빨강▼) 신호 마커

출력: docs/reports/charts_interactive/{date}-{ticker}.html
"""
from __future__ import annotations

import json
import logging
from datetime import date as _date
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CHARTS_DIR = PROJECT_ROOT / "docs" / "reports" / "charts_interactive"

MA_STYLE = [
    (5, "#FF69B4", 1),
    (10, "#4A90E2", 1),
    (20, "#FF8C00", 3),
    (60, "#2ECC71", 2),
    (120, "#E74C3C", 2),
]

# ── 순수 수치 계산 ─────────────────────────────────────────────────────────────

def _ma(values: list[float | None], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    acc = 0.0
    for i, v in enumerate(values):
        if v is None:
            continue
        acc += v
        if i >= period:
            prev = values[i - period]
            if prev is not None:
                acc -= prev
        if i >= period - 1:
            out[i] = acc / period
    return out


def _bollinger(closes: list[float | None], period: int = 20, k: float = 2.0):
    n = len(closes)
    mid: list[float | None] = [None] * n
    upper: list[float | None] = [None] * n
    lower: list[float | None] = [None] * n
    for i in range(period - 1, n):
        window = [v for v in closes[i - period + 1:i + 1] if v is not None]
        if len(window) < period:
            continue
        m = sum(window) / period
        sd = (sum((x - m) ** 2 for x in window) / period) ** 0.5
        mid[i] = m
        upper[i] = m + k * sd
        lower[i] = m - k * sd
    return mid, upper, lower


def _ichimoku(highs: list[float], lows: list[float]):
    n = len(highs)

    def _mid(p: int) -> list[float | None]:
        out: list[float | None] = [None] * n
        for i in range(p - 1, n):
            h_slice = highs[i - p + 1:i + 1]
            l_slice = lows[i - p + 1:i + 1]
            out[i] = (max(h_slice) + min(l_slice)) / 2
        return out

    tenkan = _mid(9)
    kijun = _mid(26)
    sb52 = _mid(52)

    span_a: list[float | None] = [None] * n
    span_b: list[float | None] = [None] * n
    for i in range(n):
        t, k52 = tenkan[i], kijun[i]
        if t is not None and k52 is not None:
            span_a[i] = (t + k52) / 2
        b = sb52[i]
        span_b[i] = b
    return tenkan, kijun, span_a, span_b


def _ema(values: list[float | None], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    k = 2 / (period + 1)
    prev: float | None = None
    seed_sum = 0.0
    seed_count = 0
    for i, v in enumerate(values):
        if v is None:
            continue
        if prev is None:
            seed_sum += v
            seed_count += 1
            if seed_count == period:
                prev = seed_sum / period
                out[i] = prev
        else:
            prev = v * k + prev * (1 - k)
            out[i] = prev
    return out


def _macd(closes: list[float | None], fast: int = 12, slow: int = 26, sig: int = 9):
    ef = _ema(closes, fast)
    es = _ema(closes, slow)
    n = len(closes)
    line: list[float | None] = [
        (ef[i] - es[i]) if ef[i] is not None and es[i] is not None else None
        for i in range(n)
    ]
    signal = _ema(line, sig)
    hist: list[float | None] = [
        (line[i] - signal[i]) if line[i] is not None and signal[i] is not None else None
        for i in range(n)
    ]
    return line, signal, hist


def _rsi(closes: list[float | None], period: int = 14) -> list[float | None]:
    n = len(closes)
    out: list[float | None] = [None] * n
    vals = [v for v in closes if v is not None]
    if len(vals) <= period:
        return out
    gains, losses = [], []
    for i in range(1, period + 1):
        ch = vals[i] - vals[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    ag = sum(gains) / period
    al = sum(losses) / period

    real_idx = [i for i, v in enumerate(closes) if v is not None]
    out[real_idx[period]] = 100 - 100 / (1 + ag / al) if al else 100.0
    for j in range(period + 1, len(vals)):
        ch = vals[j] - vals[j - 1]
        ag = (ag * (period - 1) + max(ch, 0)) / period
        al = (al * (period - 1) + max(-ch, 0)) / period
        out[real_idx[j]] = 100 - 100 / (1 + ag / al) if al else 100.0
    return out


# ── 리샘플 ────────────────────────────────────────────────────────────────────

def _resample(candles, rule: str):
    """일봉 → 주(W)/월(M) 집계. 'D'는 원본 그대로."""
    if rule == "D":
        return candles
    from src.datasource.base import Candle
    groups: dict[str, list] = {}
    order: list[str] = []
    for c in candles:
        try:
            d = _date(int(c.date[:4]), int(c.date[4:6]), int(c.date[6:8]))
        except (ValueError, IndexError):
            continue
        if rule == "W":
            iso = d.isocalendar()
            key = f"{iso[0]}-W{iso[1]:02d}"
        else:
            key = f"{d.year}-{d.month:02d}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(c)
    out = []
    for key in order:
        g = groups[key]
        out.append(Candle(
            date=g[-1].date,
            open=g[0].open,
            high=max(x.high for x in g),
            low=min(x.low for x in g),
            close=g[-1].close,
            volume=sum(x.volume for x in g),
        ))
    return out


# ── 데이터 직렬화 ──────────────────────────────────────────────────────────────

def _candles_to_payload(candles) -> dict:
    """lightweight-charts에 넘길 JSON payload 생성.

    lightweight-charts는 time을 'YYYY-MM-DD' 문자열 또는 Unix timestamp로 받는다.
    """
    dates = []
    for c in candles:
        d = c.date  # YYYYMMDD
        dates.append(f"{d[:4]}-{d[4:6]}-{d[6:8]}")

    o = [c.open for c in candles]
    h = [c.high for c in candles]
    lo_vals = [c.low for c in candles]
    cl = [c.close for c in candles]
    vol = [c.volume for c in candles]

    def _series(vals, key="value"):
        return [{"time": dates[i], key: vals[i]} for i in range(len(dates)) if vals[i] is not None]

    def _ohlc():
        return [{"time": dates[i], "open": o[i], "high": h[i], "low": lo_vals[i], "close": cl[i]}
                for i in range(len(dates))]

    def _volume():
        return [{"time": dates[i], "value": vol[i],
                 "color": "#e74c3caa" if cl[i] >= o[i] else "#3498dbaa"}
                for i in range(len(dates))]

    def _hist_color(vals):
        return [{"time": dates[i], "value": vals[i],
                 "color": "#34d399aa" if vals[i] and vals[i] >= 0 else "#f87171aa"}
                for i in range(len(dates)) if vals[i] is not None]

    bb_mid_v, bb_up_v, bb_dn_v = _bollinger(cl)
    tenkan, kijun, span_a, span_b = _ichimoku(h, lo_vals)
    macd_line, macd_sig, macd_hist = _macd(cl)
    rsi_v = _rsi(cl)

    # 일목구름 채움용: span_a/span_b 쌍 (시간 맞춰서)
    cloud_pairs = []
    for i in range(len(dates)):
        sa, sb = span_a[i], span_b[i]
        if sa is not None and sb is not None:
            cloud_pairs.append({"time": dates[i], "sa": sa, "sb": sb})

    payload: dict = {
        "candles": _ohlc(),
        "volume": _volume(),
        "ma": {str(p): _series(_ma(cl, p)) for p, *_ in MA_STYLE},
        "bb_mid": _series(bb_mid_v),
        "bb_up": _series(bb_up_v),
        "bb_dn": _series(bb_dn_v),
        "tenkan": _series(tenkan),
        "kijun": _series(kijun),
        "span_a": _series(span_a),
        "span_b": _series(span_b),
        "cloud_pairs": cloud_pairs,
        "macd_line": _series(macd_line),
        "macd_sig": _series(macd_sig),
        "macd_hist": _hist_color(macd_hist),
        "rsi": _series(rsi_v),
    }
    return payload


# ── HTML 템플릿 ───────────────────────────────────────────────────────────────

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<script src="https://unpkg.com/lightweight-charts@4/dist/lightweight-charts.standalone.production.js"></script>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0f172a; color: #e2e8f0; font-family: 'Malgun Gothic', sans-serif; }}
  #header {{ padding: 10px 16px 4px; font-size: 15px; font-weight: 600; color: #f1f5f9; }}
  #toolbar {{ padding: 4px 16px 8px; display: flex; gap: 6px; align-items: center; }}
  .tf-btn {{
    background: #1e293b; border: 1px solid #334155; color: #94a3b8;
    padding: 3px 11px; border-radius: 4px; cursor: pointer; font-size: 12px;
  }}
  .tf-btn.active {{ background: #334155; color: #f1f5f9; border-color: #475569; }}
  .legend {{
    padding: 0 16px 4px; font-size: 11px; color: #64748b; display: flex; gap: 12px; flex-wrap: wrap;
  }}
  .legend span {{ display: flex; align-items: center; gap: 4px; }}
  .legend .dot {{ width: 10px; height: 3px; border-radius: 1px; }}
  #charts {{ padding: 0 8px 8px; display: flex; flex-direction: column; gap: 2px; }}
  .chart-wrap {{ width: 100%; border-radius: 4px; overflow: hidden; }}
  #chart-main  {{ height: 460px; }}
  #chart-vol   {{ height: 100px; }}
  #chart-macd  {{ height: 110px; }}
  #chart-rsi   {{ height: 100px; }}
</style>
</head>
<body>
<div id="header">{title}</div>
<div id="toolbar">
  <button class="tf-btn active" id="btn-D" onclick="switchTf('D')">일봉</button>
  <button class="tf-btn" id="btn-W" onclick="switchTf('W')">주봉</button>
  <button class="tf-btn" id="btn-M" onclick="switchTf('M')">월봉</button>
</div>
<div class="legend" id="legend-ma"></div>
<div id="charts">
  <div class="chart-wrap" id="chart-main"></div>
  <div class="chart-wrap" id="chart-vol"></div>
  <div class="chart-wrap" id="chart-macd"></div>
  <div class="chart-wrap" id="chart-rsi"></div>
</div>
<script>
// ── 데이터 ─────────────────────────────────────────────────────────────────
const ALL_DATA = {all_data_json};
const SIGNAL_DATES = {signal_dates_json};
const SELL_DATES   = {sell_dates_json};
const MA_STYLE = {ma_style_json};

// ── 공통 차트 옵션 ──────────────────────────────────────────────────────────
const BG = '#0f172a', GRID = '#1e293b', TEXT = '#94a3b8', BORDER = '#334155';

function makeChart(el, height, opts) {{
  return LightweightCharts.createChart(el, {{
    width: el.clientWidth,
    height: height,
    layout: {{ background: {{ color: BG }}, textColor: TEXT }},
    grid: {{ vertLines: {{ color: GRID }}, horzLines: {{ color: GRID }} }},
    crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal }},
    timeScale: {{ borderColor: BORDER, timeVisible: true }},
    rightPriceScale: {{ borderColor: BORDER }},
    handleScroll: {{ mouseWheel: true, pressedMouseMove: true }},
    handleScale: {{ mouseWheel: true, pinch: true }},
    ...opts,
  }});
}}

// ── 차트 인스턴스 ──────────────────────────────────────────────────────────
const elMain = document.getElementById('chart-main');
const elVol  = document.getElementById('chart-vol');
const elMacd = document.getElementById('chart-macd');
const elRsi  = document.getElementById('chart-rsi');

const chartMain = makeChart(elMain, 460, {{}});
const chartVol  = makeChart(elVol,  100, {{ rightPriceScale: {{ visible: false }} }});
const chartMacd = makeChart(elMacd, 110, {{}});
const chartRsi  = makeChart(elRsi,  100, {{ rightPriceScale: {{ autoScale: false, visible: true }} }});

// ── 시리즈 ─────────────────────────────────────────────────────────────────
const seriesCandle = chartMain.addCandlestickSeries({{
  upColor: '#e74c3c', downColor: '#3498db',
  borderUpColor: '#e74c3c', borderDownColor: '#3498db',
  wickUpColor: '#e74c3c', wickDownColor: '#3498db',
}});

const seriesMa = MA_STYLE.map(([p, color, w]) =>
  chartMain.addLineSeries({{ color, lineWidth: w, title: `MA${{p}}`, priceLineVisible: false }})
);

const seriesBbMid = chartMain.addLineSeries({{
  color: '#64748b', lineWidth: 1, lineStyle: 2, priceLineVisible: false, title: 'BB중간',
}});
const seriesBbUp = chartMain.addLineSeries({{
  color: '#64748b88', lineWidth: 1, lineStyle: 1, priceLineVisible: false, title: 'BB상단',
}});
const seriesBbDn = chartMain.addLineSeries({{
  color: '#64748b88', lineWidth: 1, lineStyle: 1, priceLineVisible: false, title: 'BB하단',
}});

const seriesTenkan = chartMain.addLineSeries({{
  color: '#FFA500', lineWidth: 1, priceLineVisible: false, title: '전환',
}});
const seriesKijun = chartMain.addLineSeries({{
  color: '#1E90FF', lineWidth: 1, priceLineVisible: false, title: '기준',
}});
const seriesSpanA = chartMain.addLineSeries({{
  color: 'rgba(46,204,113,0.5)', lineWidth: 1, priceLineVisible: false, title: '선행A',
}});
const seriesSpanB = chartMain.addLineSeries({{
  color: 'rgba(231,76,60,0.5)', lineWidth: 1, priceLineVisible: false, title: '선행B',
}});

const seriesVol  = chartVol.addHistogramSeries({{ priceFormat: {{ type: 'volume' }} }});
const seriesMacdLine = chartMacd.addLineSeries({{ color: '#60a5fa', lineWidth: 1, title: 'MACD' }});
const seriesMacdSig  = chartMacd.addLineSeries({{ color: '#fbbf24', lineWidth: 1, title: 'Sig' }});
const seriesMacdHist = chartMacd.addHistogramSeries({{ priceLineVisible: false }});
const seriesRsi  = chartRsi.addLineSeries({{
  color: '#a78bfa', lineWidth: 1, title: 'RSI',
}});

// RSI 30/70 참조선 (priceLines)
seriesRsi.createPriceLine({{ price: 30, color: '#475569', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '30' }});
seriesRsi.createPriceLine({{ price: 70, color: '#475569', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '70' }});

// ── 신호 마커 ──────────────────────────────────────────────────────────────
function buildMarkers(data, sigDates, sellDates) {{
  const dateMap = {{}};
  data.candles.forEach(c => {{ dateMap[c.time] = c; }});
  const markers = [];
  if (sigDates) sigDates.forEach(raw => {{
    const t = raw.slice(0,4)+'-'+raw.slice(4,6)+'-'+raw.slice(6,8);
    if (dateMap[t]) markers.push({{
      time: t, position: 'belowBar', color: '#22c55e',
      shape: 'arrowUp', text: '매수', size: 1.5,
    }});
  }});
  if (sellDates) sellDates.forEach(raw => {{
    const t = raw.slice(0,4)+'-'+raw.slice(4,6)+'-'+raw.slice(6,8);
    if (dateMap[t]) markers.push({{
      time: t, position: 'aboveBar', color: '#ef4444',
      shape: 'arrowDown', text: '매도', size: 1.5,
    }});
  }});
  markers.sort((a, b) => a.time < b.time ? -1 : 1);
  return markers;
}}

// ── 데이터 적용 ──────────────────────────────────────────────────────────────
function applyData(tf) {{
  const d = ALL_DATA[tf];
  if (!d) return;

  seriesCandle.setData(d.candles);
  MA_STYLE.forEach(([p], i) => seriesMa[i].setData(d.ma[p] || []));
  seriesBbMid.setData(d.bb_mid);
  seriesBbUp.setData(d.bb_up);
  seriesBbDn.setData(d.bb_dn);
  seriesTenkan.setData(d.tenkan);
  seriesKijun.setData(d.kijun);
  seriesSpanA.setData(d.span_a);
  seriesSpanB.setData(d.span_b);
  seriesVol.setData(d.volume);
  seriesMacdLine.setData(d.macd_line);
  seriesMacdSig.setData(d.macd_sig);
  seriesMacdHist.setData(d.macd_hist);
  seriesRsi.setData(d.rsi);

  // 신호 마커는 일봉에만
  seriesCandle.setMarkers(tf === 'D' ? buildMarkers(d, SIGNAL_DATES, SELL_DATES) : []);

  chartMain.timeScale().fitContent();
  chartVol.timeScale().fitContent();
  chartMacd.timeScale().fitContent();
  chartRsi.timeScale().fitContent();
}}

// ── 시간축 동기화 ───────────────────────────────────────────────────────────
chartMain.timeScale().subscribeVisibleTimeRangeChange(range => {{
  if (!range) return;
  chartVol.timeScale().setVisibleRange(range);
  chartMacd.timeScale().setVisibleRange(range);
  chartRsi.timeScale().setVisibleRange(range);
}});

// ── TF 전환 ─────────────────────────────────────────────────────────────────
let currentTf = 'D';
function switchTf(tf) {{
  currentTf = tf;
  ['D','W','M'].forEach(t => {{
    document.getElementById('btn-'+t).classList.toggle('active', t === tf);
  }});
  applyData(tf);
}}

// ── MA 범례 생성 ─────────────────────────────────────────────────────────────
const legEl = document.getElementById('legend-ma');
MA_STYLE.forEach(([p, color]) => {{
  const sp = document.createElement('span');
  sp.innerHTML = `<span class="dot" style="background:${{color}}"></span>MA${{p}}`;
  legEl.appendChild(sp);
}});

// ── 반응형 리사이즈 ──────────────────────────────────────────────────────────
const charts = [
  [chartMain, elMain, 460],
  [chartVol, elVol, 100],
  [chartMacd, elMacd, 110],
  [chartRsi, elRsi, 100],
];
new ResizeObserver(() => {{
  charts.forEach(([c, el, h]) => c.resize(el.clientWidth, h));
}}).observe(document.getElementById('charts'));

// ── 초기화 ───────────────────────────────────────────────────────────────────
applyData('D');
</script>
</body>
</html>
"""


# ── 공개 API ──────────────────────────────────────────────────────────────────

def render_interactive(
    candles,
    ticker: str,
    name: str,
    date: str | None = None,
    signal_dates: list[str] | None = None,
    sell_dates: list[str] | None = None,
) -> Path | None:
    """lightweight-charts 인터랙티브 HTML 차트 생성.

    chart_plotly.render_interactive()와 동일한 시그니처 — drop-in 교체 가능.

    signal_dates: 매수 신호일(YYYYMMDD) → 초록 ▲ 마커
    sell_dates:   매도/손절일(YYYYMMDD)  → 빨강 ▼ 마커
    """
    if len(candles) < 60:
        logger.warning("lw_chart_skip ticker=%s rows=%d", ticker, len(candles))
        return None

    all_data: dict[str, dict] = {}
    for tf, rule in (("D", "D"), ("W", "W"), ("M", "M")):
        cs = _resample(candles, rule)
        if len(cs) < 5:
            continue
        all_data[tf] = _candles_to_payload(cs)

    date_str = date or datetime.now().strftime("%Y-%m-%d")
    title = f"{name} ({ticker})  {date_str}"

    html = _HTML_TEMPLATE.format(
        title=title,
        all_data_json=json.dumps(all_data, ensure_ascii=False),
        signal_dates_json=json.dumps(signal_dates or [], ensure_ascii=False),
        sell_dates_json=json.dumps(sell_dates or [], ensure_ascii=False),
        ma_style_json=json.dumps(MA_STYLE, ensure_ascii=False),
    )

    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    out = CHARTS_DIR / f"{date_str}-{ticker}.html"
    out.write_text(html, encoding="utf-8")
    logger.info("lw_chart_rendered ticker=%s path=%s", ticker, out)
    return out


def chart_url_rel(ticker: str, date: str | None = None) -> str:
    """리포트 HTML(docs/reports/)에서 상대 경로로 차트 참조."""
    date_str = date or datetime.now().strftime("%Y-%m-%d")
    return f"charts_interactive/{date_str}-{ticker}.html"
