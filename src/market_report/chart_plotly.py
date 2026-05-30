"""Plotly 인터랙티브 차트 — 웹 리포트용 (줌/팬/일·주·월 토글/보조지표).

텔레그램은 정적 PNG(chart.py)만 가능 → 웹 조회용 인터랙티브 HTML은 여기서 생성.
KIS candles(src.datasource.base.Candle) 입력 → 단일 HTML 파일 출력.

구성:
- 메인: 캔들 + MA5/10/20/60/120 + 볼린저밴드 + 일목구름
- 보조: 거래량, MACD, RSI
- 일/주/월봉 토글 (updatemenus)
- 줌/팬/호버 (Plotly 기본)
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CHARTS_DIR = PROJECT_ROOT / "docs" / "reports" / "charts_interactive"

# MA 색·굵기 (chart.py와 동일 사양)
MA_STYLE = [
    (5, "#FF69B4", 1), (10, "#4A90E2", 1), (20, "#FF8C00", 3),
    (60, "#2ECC71", 2), (120, "#E74C3C", 2),
]


def _resample(candles, rule: str):
    """일봉 → 주/월봉 집계. rule: 'W'(주) / 'M'(월). 'D'면 그대로."""
    if rule == "D":
        return candles
    import datetime
    groups: dict = {}
    order: list = []
    for c in candles:
        try:
            d = datetime.date(int(c.date[:4]), int(c.date[4:6]), int(c.date[6:8]))
        except (ValueError, IndexError):
            continue
        if rule == "W":
            iso = d.isocalendar()
            key = f"{iso[0]}-W{iso[1]:02d}"
        else:  # M
            key = f"{d.year}-{d.month:02d}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(c)
    from src.datasource.base import Candle
    out = []
    for key in order:
        g = groups[key]
        out.append(Candle(date=g[-1].date, open=g[0].open,
                          high=max(x.high for x in g), low=min(x.low for x in g),
                          close=g[-1].close, volume=sum(x.volume for x in g)))
    return out


def _ma(values, period):
    out = [None] * len(values)
    acc = 0.0
    for i, v in enumerate(values):
        acc += v
        if i >= period:
            acc -= values[i - period]
        if i >= period - 1:
            out[i] = acc / period
    return out


def _ichimoku_cloud(highs, lows):
    n = len(highs)

    def mid(p):
        o = [None] * n
        for i in range(p - 1, n):
            o[i] = (max(highs[i - p + 1:i + 1]) + min(lows[i - p + 1:i + 1])) / 2
        return o
    tenkan, kijun, sb = mid(9), mid(26), mid(52)
    sa = [((tenkan[i] + kijun[i]) / 2) if tenkan[i] is not None and kijun[i] is not None else None
          for i in range(n)]
    return sa, sb


def _macd(values, fast=12, slow=26, sig=9):
    def ema(vals, p):
        out = [None] * len(vals)
        if len(vals) < p:
            return out
        k = 2 / (p + 1)
        seed = sum(vals[:p]) / p
        out[p - 1] = seed
        prev = seed
        for i in range(p, len(vals)):
            prev = vals[i] * k + prev * (1 - k)
            out[i] = prev
        return out
    ef, es = ema(values, fast), ema(values, slow)
    line = [(ef[i] - es[i]) if ef[i] is not None and es[i] is not None else None for i in range(len(values))]
    valid = [(i, v) for i, v in enumerate(line) if v is not None]
    signal = [None] * len(values)
    if len(valid) >= sig:
        seq = ema([v for _, v in valid], sig)
        for (oi, _), s in zip(valid, seq):
            signal[oi] = s
    hist = [(line[i] - signal[i]) if line[i] is not None and signal[i] is not None else None
            for i in range(len(values))]
    return line, signal, hist


def _rsi(values, period=14):
    n = len(values)
    out = [None] * n
    if n <= period:
        return out
    g = l = 0.0
    for i in range(1, period + 1):
        ch = values[i] - values[i - 1]
        g += max(ch, 0)
        l += max(-ch, 0)
    ag, al = g / period, l / period
    out[period] = 100 - 100 / (1 + (ag / al if al else 999))
    for i in range(period + 1, n):
        ch = values[i] - values[i - 1]
        ag = (ag * (period - 1) + max(ch, 0)) / period
        al = (al * (period - 1) + max(-ch, 0)) / period
        out[i] = 100 - 100 / (1 + (ag / al if al else 999))
    return out


def _build_traces(candles):
    """단일 타임프레임 candles → plotly trace 리스트 + 축 정보."""
    import plotly.graph_objects as go
    dates = [c.date for c in candles]
    o = [c.open for c in candles]
    h = [c.high for c in candles]
    lo = [c.low for c in candles]
    cl = [c.close for c in candles]
    vol = [c.volume for c in candles]

    traces = []
    # 캔들 (한국식: 상승 빨강, 하락 파랑)
    traces.append(go.Candlestick(
        x=dates, open=o, high=h, low=lo, close=cl, name="가격",
        increasing_line_color="#e74c3c", decreasing_line_color="#3498db",
        xaxis="x", yaxis="y",
    ))
    # MA
    for period, color, width in MA_STYLE:
        ma = _ma(cl, period)
        traces.append(go.Scatter(x=dates, y=ma, name=f"MA{period}", mode="lines",
                                 line=dict(color=color, width=width), xaxis="x", yaxis="y"))
    # 볼린저밴드
    bb_mid = _ma(cl, 20)
    bb_up, bb_dn = [None] * len(cl), [None] * len(cl)
    for i in range(19, len(cl)):
        w = cl[i - 19:i + 1]
        m = sum(w) / 20
        sd = (sum((x - m) ** 2 for x in w) / 20) ** 0.5
        bb_up[i] = m + 2 * sd
        bb_dn[i] = m - 2 * sd
    traces.append(go.Scatter(x=dates, y=bb_up, name="볼린저상", mode="lines",
                             line=dict(color="#888", width=0.8, dash="dot"), xaxis="x", yaxis="y"))
    traces.append(go.Scatter(x=dates, y=bb_dn, name="볼린저하", mode="lines",
                             line=dict(color="#888", width=0.8, dash="dot"), xaxis="x", yaxis="y"))
    # 일목구름
    sa, sb = _ichimoku_cloud(h, lo)
    traces.append(go.Scatter(x=dates, y=sa, name="선행A", mode="lines",
                             line=dict(color="rgba(46,204,113,0.4)", width=0.5), xaxis="x", yaxis="y"))
    traces.append(go.Scatter(x=dates, y=sb, name="선행B", mode="lines",
                             line=dict(color="rgba(231,76,60,0.4)", width=0.5),
                             fill="tonexty", fillcolor="rgba(150,150,150,0.12)", xaxis="x", yaxis="y"))
    # 거래량 (panel2)
    vcol = ["#e74c3c" if cl[i] >= o[i] else "#3498db" for i in range(len(cl))]
    traces.append(go.Bar(x=dates, y=vol, name="거래량", marker_color=vcol,
                         xaxis="x", yaxis="y2", opacity=0.5))
    # MACD (panel3)
    ml, ms, hist = _macd(cl)
    traces.append(go.Scatter(x=dates, y=ml, name="MACD", mode="lines",
                             line=dict(color="#60a5fa", width=1), xaxis="x", yaxis="y3"))
    traces.append(go.Scatter(x=dates, y=ms, name="시그널", mode="lines",
                             line=dict(color="#fbbf24", width=1), xaxis="x", yaxis="y3"))
    traces.append(go.Bar(x=dates, y=hist, name="MACD히스토", marker_color="#94a3b8",
                         xaxis="x", yaxis="y3", opacity=0.4))
    # RSI (panel4)
    traces.append(go.Scatter(x=dates, y=_rsi(cl), name="RSI", mode="lines",
                             line=dict(color="#a78bfa", width=1), xaxis="x", yaxis="y4"))
    return traces


def render_interactive(candles, ticker: str, name: str, date: str | None = None) -> Path | None:
    """인터랙티브 HTML 차트 생성. 일/주/월 토글 버튼 포함."""
    import plotly.graph_objects as go
    from datetime import datetime

    if len(candles) < 60:
        logger.warning("plotly_chart_skip ticker=%s rows=%s", ticker, len(candles))
        return None

    # 일/주/월 trace 묶음 생성 (토글용)
    frames = {"일봉": candles, "주봉": _resample(candles, "W"), "월봉": _resample(candles, "M")}
    all_traces = []
    trace_tf = []  # 각 trace가 어느 타임프레임인지
    for tf, cs in frames.items():
        ts = _build_traces(cs)
        all_traces.extend(ts)
        trace_tf.extend([tf] * len(ts))

    fig = go.Figure(data=all_traces)
    # 기본: 일봉만 보이기
    for tr, tf in zip(fig.data, trace_tf):
        tr.visible = (tf == "일봉")

    # 토글 버튼
    buttons = []
    for tf in frames:
        vis = [t == tf for t in trace_tf]
        buttons.append(dict(label=tf, method="update", args=[{"visible": vis}]))

    fig.update_layout(
        title=f"{name} ({ticker})  {date or ''}",
        template="plotly_dark",
        height=950,
        # 메인 x축에 rangeslider(하단 미니맵) — 양끝 드래그로 기간 축소/확대 (HTS풍)
        xaxis=dict(domain=[0, 1], anchor="y4",
                   rangeslider=dict(visible=True, thickness=0.06, bgcolor="#0f172a")),
        yaxis=dict(domain=[0.48, 1.0], title="가격"),
        yaxis2=dict(domain=[0.34, 0.46], title="거래량"),
        yaxis3=dict(domain=[0.20, 0.32], title="MACD"),
        yaxis4=dict(domain=[0.08, 0.18], title="RSI", range=[0, 100]),
        updatemenus=[dict(type="buttons", direction="right", x=0.0, y=1.07,
                          buttons=buttons, bgcolor="#1e293b", font=dict(color="#e2e8f0"))],
        legend=dict(orientation="h", y=1.03, font=dict(size=9)),
        margin=dict(l=50, r=20, t=80, b=20),
        dragmode="pan",  # 드래그 = 좌우 이동 (HTS 기본). 휠 = 줌인/아웃.
    )

    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = date or datetime.now().strftime("%Y-%m-%d")
    out = CHARTS_DIR / f"{date_str}-{ticker}.html"
    # scrollZoom: 휠 줌 / displaylogo 제거 / 한글 모드바
    config = {
        "scrollZoom": True,
        "displaylogo": False,
        "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"],
        "doubleClick": "reset",  # 더블클릭 = 전체보기 리셋
    }
    fig.write_html(str(out), include_plotlyjs="cdn", config=config)
    logger.info("plotly_chart_rendered ticker=%s path=%s", ticker, out)
    return out


def chart_url_rel(ticker: str, date: str | None = None) -> str:
    from datetime import datetime
    date_str = date or datetime.now().strftime("%Y-%m-%d")
    return f"charts_interactive/{date_str}-{ticker}.html"
