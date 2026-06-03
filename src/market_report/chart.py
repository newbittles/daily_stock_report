"""추천 종목 차트 생성 — pykrx + ta + mplfinance.

사용자 지정 사양:
- 메인 차트: 캔들 + MA5(분홍) / MA10(파랑) / MA20(주황 굵기3) / MA60(초록 굵기2) / MA120(빨강 굵기2)
  + 볼린저밴드(20, 2σ) + 일목구름표(9/26/52)
- 보조 차트: MACD(12/26/9), RSI(14), CCI(20)

출력: PNG → docs/reports/charts/{date}-{ticker}.png
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # non-interactive backend (asyncio thread-safe, 헤드리스 OK)

import mplfinance as mpf  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib import pyplot as plt  # noqa: E402
from pykrx import stock  # noqa: E402
from ta.momentum import RSIIndicator  # noqa: E402
from ta.trend import CCIIndicator, MACD  # noqa: E402
from ta.volatility import BollingerBands  # noqa: E402

# 한글 폰트 (Windows 기본 Malgun Gothic, 폴백 다수). mplfinance가 스타일로 font.family를
# 덮어쓰므로 STYLE의 rc에도 동일 리스트를 넣어야 차트 제목·축의 한글이 깨지지 않는다.
KOREAN_FONTS = ["Malgun Gothic", "AppleGothic", "Noto Sans CJK KR", "NanumGothic", "DejaVu Sans"]
matplotlib.rcParams["font.family"] = KOREAN_FONTS
matplotlib.rcParams["axes.unicode_minus"] = False

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CHARTS_DIR = PROJECT_ROOT / "docs" / "reports" / "charts"

# 사용자 지정 색·굵기 (MA)
MA_STYLE = [
    {"period": 5,   "color": "#FF69B4", "width": 1.0, "label": "MA5"},
    {"period": 10,  "color": "#4A90E2", "width": 1.0, "label": "MA10"},
    {"period": 20,  "color": "#FF8C00", "width": 3.0, "label": "MA20"},
    {"period": 60,  "color": "#2ECC71", "width": 2.0, "label": "MA60"},
    {"period": 120, "color": "#E74C3C", "width": 2.0, "label": "MA120"},
]

# 한국식 캔들: 양봉 빨강, 음봉 파랑
MC = mpf.make_marketcolors(
    up="red", down="blue",
    edge={"up": "red", "down": "blue"},
    wick={"up": "red", "down": "blue"},
    volume={"up": "#ff6b6b", "down": "#5b9bd5"},
)
STYLE = mpf.make_mpf_style(
    marketcolors=MC,
    facecolor="#0f172a",
    edgecolor="#334155",
    figcolor="#0f172a",
    gridcolor="#1e293b",
    gridstyle="--",
    rc={
        "font.family": KOREAN_FONTS,  # mplfinance가 font.family를 덮어쓰는 것 방지 (한글 깨짐 해결)
        "axes.unicode_minus": False,
        "axes.labelcolor": "#94a3b8",
        "xtick.color": "#94a3b8",
        "ytick.color": "#94a3b8",
        "axes.titlecolor": "#e2e8f0",
        "axes.titlesize": 11,
        "text.color": "#e2e8f0",
    },
)


def _fetch_ohlcv(ticker: str, days: int = 220) -> pd.DataFrame | None:
    """pykrx 일봉 OHLCV (인증 없이 동작). 컬럼: Open/High/Low/Close/Volume."""
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    try:
        df = stock.get_market_ohlcv(start, end, ticker)
    except Exception as exc:
        logger.warning("ohlcv_fetch_failed ticker=%s error=%s", ticker, exc)
        return None
    if df is None or df.empty:
        return None

    # 한글 컬럼명을 mplfinance용 영문으로
    rename = {"시가": "Open", "고가": "High", "저가": "Low", "종가": "Close", "거래량": "Volume"}
    df = df.rename(columns=rename)
    df.index = pd.to_datetime(df.index)
    df.index.name = "Date"
    return df[["Open", "High", "Low", "Close", "Volume"]]


def _ichimoku(df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series, pd.Series]:
    """일목구름표 5개 선 직접 계산 (ta 라이브러리 호환성 회피).

    - 전환선 (tenkan): (max(9)+min(9))/2
    - 기준선 (kijun):  (max(26)+min(26))/2
    - 선행스팬1: (tenkan+kijun)/2  → 26일 앞으로 시프트
    - 선행스팬2: (max(52)+min(52))/2 → 26일 앞으로 시프트
    - 후행스팬:  종가 → 26일 뒤로 시프트
    """
    high, low, close = df["High"], df["Low"], df["Close"]
    tenkan = (high.rolling(9).max() + low.rolling(9).min()) / 2
    kijun = (high.rolling(26).max() + low.rolling(26).min()) / 2
    span_a = ((tenkan + kijun) / 2).shift(26)
    span_b = ((high.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)
    chikou = close.shift(-26)
    return tenkan, kijun, span_a, span_b, chikou


def _candles_to_df(candles) -> "pd.DataFrame | None":
    """src.datasource.base.Candle 리스트 → mplfinance DataFrame.

    스크리닝에서 이미 가져온 KIS candles를 차트에 재사용 (중복 API 호출 제거).
    """
    if not candles:
        return None
    rows = []
    idx = []
    for c in candles:
        try:
            idx.append(pd.to_datetime(c.date, format="%Y%m%d"))
            rows.append({"Open": c.open, "High": c.high, "Low": c.low,
                         "Close": c.close, "Volume": c.volume})
        except Exception:
            continue
    if not rows:
        return None
    df = pd.DataFrame(rows, index=pd.DatetimeIndex(idx, name="Date"))
    return df


def render_chart_from_candles(candles, ticker: str, name: str, date: str | None = None) -> Path | None:
    """KIS candles 리스트로 차트 생성 (API 재호출 없음)."""
    df = _candles_to_df(candles)
    if df is None or len(df) < 60:
        logger.warning("chart_skip_insufficient ticker=%s rows=%s", ticker, 0 if df is None else len(df))
        return None
    return _render_df(df, ticker, name, date)


def render_chart(ticker: str, name: str, date: str | None = None) -> Path | None:
    """추천 종목 차트 PNG 생성 (pykrx로 OHLCV 조회). 실패 시 None."""
    df = _fetch_ohlcv(ticker, days=250)
    if df is None or len(df) < 60:
        logger.warning("chart_skip_insufficient ticker=%s rows=%s", ticker, len(df) if df is not None else 0)
        return None
    return _render_df(df, ticker, name, date)


def _candidate_signal_dates(df: "pd.DataFrame", max_bars: int = 45) -> list[str]:
    """표시 구간(최근 max_bars봉)에서 screener 전략(A/B/C) 신호 발생일 탐색.

    종가베팅 후보 차트의 '전략' 오버레이용 — pykrx OHLCV를 Candle로 변환해
    각 봉 시점까지의 캔들로 screen_stock을 롤링 평가, 매칭일을 YYYYMMDD로 반환.
    실패/전략없음 시 빈 리스트 (마커 미표시).
    """
    try:
        from src.datasource.base import Candle
        from src.screener.config import load_screener_config
        from src.screener.engine import screen_stock

        strategies = load_screener_config().enabled_strategies()
        if not strategies:
            return []

        candles = [
            Candle(date=idx.strftime("%Y%m%d"), open=float(r.Open), high=float(r.High),
                   low=float(r.Low), close=float(r.Close), volume=int(r.Volume))
            for idx, r in df.iterrows()
        ]
        n = len(candles)
        out: list[str] = []
        for i in range(max(60, n - max_bars), n):
            chg = 0.0
            if i >= 1 and candles[i - 1].close > 0:
                chg = (candles[i].close - candles[i - 1].close) / candles[i - 1].close * 100
            if screen_stock(strategies, candles[: i + 1], chg):
                out.append(candles[i].date)
        return out
    except Exception as exc:
        logger.warning("candidate_signal_failed error=%s", exc)
        return []


def render_candidate_chart(ticker: str, name: str, date: str | None = None) -> Path | None:
    """종가베팅 후보 전용 차트 — 2달 구간 + 이평 + 전략(screener)신호 마커 + MACD + 거래대금."""
    df = _fetch_ohlcv(ticker, days=250)
    if df is None or len(df) < 60:
        logger.warning("chart_skip_insufficient ticker=%s rows=%s", ticker, len(df) if df is not None else 0)
        return None
    signal_dates = _candidate_signal_dates(df)
    return _render_df(df, ticker, name, date, signal_dates=signal_dates, layout="candidate")


def _render_df(
    df: "pd.DataFrame", ticker: str, name: str, date: str | None = None,
    signal_dates: list[str] | None = None, buy_dates: list[str] | None = None,
    out_suffix: str = "", layout: str = "full",
) -> Path | None:
    """DataFrame으로 실제 차트 렌더링.

    signal_dates: B 신호 발생일 (YYYYMMDD) → 초록 ▲ 마커
    buy_dates: 사용자 실제 매수일 → 노랑 ★ 마커 (비교용)
    layout:
      - "full"     : 캔들+MA+BB+일목 / MACD / RSI / CCI (분석·백테스트용, 150봉)
      - "candidate": 캔들+MA+전략마커 / MACD / 거래대금 (종가베팅 후보용, 2달≈45봉)
    """
    candidate = layout == "candidate"

    # ── 표시 구간 ──────────────────────────────────────────────────────────
    show_len = min(45 if candidate else 150, len(df))
    df_show = df.tail(show_len)

    def _last(series: pd.Series) -> pd.Series:
        return series.tail(show_len)

    # ── 지표 계산 (공통: MA·MACD) ─────────────────────────────────────────
    ma_series = {m["period"]: df["Close"].rolling(m["period"]).mean() for m in MA_STYLE}
    macd_ind = MACD(close=df["Close"], window_slow=26, window_fast=12, window_sign=9)
    macd_line = macd_ind.macd()
    macd_sig = macd_ind.macd_signal()
    macd_hist = macd_ind.macd_diff()

    ap = []
    # MA — candidate는 2달 구간이라 단기선(5/10/20) 위주로, 장기선(60/120)은 생략해 가독성↑
    ma_styles = [m for m in MA_STYLE if m["period"] <= 20] if candidate else MA_STYLE
    for m in ma_styles:
        ap.append(mpf.make_addplot(
            _last(ma_series[m["period"]]),
            color=m["color"], width=m["width"], panel=0,
        ))

    # 볼린저밴드 + 일목구름 (양 레이아웃 공통 계산 — 구름 채움은 출력부에서)
    bb = BollingerBands(close=df["Close"], window=20, window_dev=2)
    bb_high, bb_low = bb.bollinger_hband(), bb.bollinger_lband()
    tenkan, kijun, span_a, span_b, _chikou = _ichimoku(df)

    if candidate:
        # 볼린저밴드 상/하단만 (중심선 제외) — 검은 배경 대비 흰색 bold(width 2)
        ap.append(mpf.make_addplot(_last(bb_high), color="#ffffff", width=2.0, panel=0))
        ap.append(mpf.make_addplot(_last(bb_low), color="#ffffff", width=2.0, panel=0))
        # 일목 선(전환/기준/선행)은 생략 — 구름대(채움)만 표시
    else:
        ap.append(mpf.make_addplot(_last(bb_high), color="#888888", width=0.8, linestyle="--", panel=0))
        ap.append(mpf.make_addplot(_last(bb_low), color="#888888", width=0.8, linestyle="--", panel=0))
        ap.append(mpf.make_addplot(_last(tenkan), color="#FFA500", width=0.7, panel=0))
        ap.append(mpf.make_addplot(_last(kijun), color="#1E90FF", width=0.7, panel=0))
        ap.append(mpf.make_addplot(_last(span_a), color="#2ECC7180", width=0.5, panel=0))
        ap.append(mpf.make_addplot(_last(span_b), color="#E74C3C80", width=0.5, panel=0))

    # MACD panel (panel 1, 공통)
    ap.append(mpf.make_addplot(_last(macd_line), color="#60a5fa", panel=1, ylabel="MACD"))
    ap.append(mpf.make_addplot(_last(macd_sig), color="#fbbf24", panel=1))
    ap.append(mpf.make_addplot(_last(macd_hist), type="bar", color="#94a3b855", panel=1))

    if candidate:
        # 거래대금 panel (panel 2) — 종가×거래량, 양봉 빨강/음봉 파랑 바
        tv = (df["Close"] * df["Volume"]).astype(float)
        up_day = df["Close"] >= df["Open"]
        tv_up = _last(tv.where(up_day))
        tv_dn = _last(tv.where(~up_day))
        ap.append(mpf.make_addplot(tv_up, type="bar", color="#ff6b6b", panel=2, ylabel="거래대금"))
        ap.append(mpf.make_addplot(tv_dn, type="bar", color="#5b9bd5", panel=2))
    else:
        # RSI / CCI (full 전용)
        rsi = RSIIndicator(close=df["Close"], window=14).rsi()
        cci = CCIIndicator(high=df["High"], low=df["Low"], close=df["Close"], window=20).cci()
        ap.append(mpf.make_addplot(_last(rsi), color="#a78bfa", panel=2, ylabel="RSI", ylim=(0, 100)))
        ap.append(mpf.make_addplot(_last(cci), color="#34d399", panel=3, ylabel="CCI"))

    # ── 신호/매수일 마커 ──────────────────────────────────────────────────
    def _marker_series(dates: list[str] | None, offset: float):
        """해당 날짜의 저가 아래(offset)에 마커 위치. 나머지는 NaN."""
        if not dates:
            return None
        want = {pd.to_datetime(d, format="%Y%m%d") for d in dates}
        ys = []
        any_hit = False
        for idx in df_show.index:
            if idx in want:
                ys.append(df_show.loc[idx, "Low"] * offset)
                any_hit = True
            else:
                ys.append(float("nan"))
        return pd.Series(ys, index=df_show.index) if any_hit else None

    sig_marker = _marker_series(signal_dates, 0.97)
    if sig_marker is not None:
        ap.append(mpf.make_addplot(sig_marker, type="scatter", marker="^",
                                   markersize=120, color="#34d399", panel=0))
    buy_marker = _marker_series(buy_dates, 0.93)
    if buy_marker is not None:
        ap.append(mpf.make_addplot(buy_marker, type="scatter", marker="*",
                                   markersize=200, color="#fbbf24", panel=0))

    # ── 출력 ─────────────────────────────────────────────────────────────
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = date or datetime.now().strftime("%Y-%m-%d")
    out_path = CHARTS_DIR / f"{date_str}-{ticker}{out_suffix}.png"

    panel_ratios = (4, 1.3, 1.6) if candidate else (5, 1.5, 1.5, 1.5)
    figsize = (8, 6.5) if candidate else (8, 9)
    fig, axes = mpf.plot(
        df_show,
        type="candle",
        style=STYLE,
        addplot=ap,
        panel_ratios=panel_ratios,
        figsize=figsize,
        title=f"\n{name} ({ticker})",
        tight_layout=True,
        returnfig=True,
        volume=False,
        warn_too_much_data=300,
    )

    # 일목구름 채움 (span_a/span_b 사이) — 양 레이아웃 공통. candidate는 더 진하게(가독성)
    ax_main = axes[0]
    sa = _last(span_a).values
    sb = _last(span_b).values
    valid = ~(np.isnan(sa) | np.isnan(sb))
    if valid.any():
        x = np.arange(len(df_show))
        up_c = "#2ECC7133" if candidate else "#2ECC7110"
        dn_c = "#E74C3C33" if candidate else "#E74C3C10"
        ax_main.fill_between(x, sa, sb, where=valid & (sa >= sb), color=up_c, interpolate=True)
        ax_main.fill_between(x, sa, sb, where=valid & (sa < sb), color=dn_c, interpolate=True)

    if candidate:
        # 거래대금 y축 억 단위 포맷 (panel 2 primary axis = axes[4])
        from matplotlib.ticker import FuncFormatter
        axes[4].yaxis.set_major_formatter(
            FuncFormatter(lambda v, _p: f"{v / 1e8:,.0f}억" if v >= 1e8 else f"{v / 1e4:,.0f}만"))
    else:

        # RSI 30/70 가이드라인
        axes[4].axhline(30, color="#94a3b8", linewidth=0.5, linestyle=":")
        axes[4].axhline(70, color="#94a3b8", linewidth=0.5, linestyle=":")
        # CCI ±100 가이드라인
        axes[6].axhline(100, color="#94a3b8", linewidth=0.5, linestyle=":")
        axes[6].axhline(-100, color="#94a3b8", linewidth=0.5, linestyle=":")

    fig.savefig(out_path, dpi=110, bbox_inches="tight", facecolor="#0f172a")
    plt.close(fig)

    logger.info("chart_rendered ticker=%s path=%s layout=%s", ticker, out_path, layout)
    return out_path


def chart_url_rel(ticker: str, date: str | None = None) -> str:
    """리포트 HTML(docs/reports/)에서 상대 경로로 차트 참조."""
    date_str = date or datetime.now().strftime("%Y-%m-%d")
    return f"charts/{date_str}-{ticker}.png"


# ── 지수 스파크라인 ──────────────────────────────────────────────────────────

_INDEX_CODE = {"KOSPI": "KS11", "KOSDAQ": "KQ11"}  # FinanceDataReader 코드


def render_index_sparkline(market: str, date: str | None = None) -> Path | None:
    """KOSPI/KOSDAQ 60일 시계열 미니 차트 (축·라벨 없음, 단순 라인).

    데이터 출처: FinanceDataReader (KRX 인증 불필요).
    """
    import FinanceDataReader as fdr

    code = _INDEX_CODE.get(market)
    if not code:
        return None

    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")
    try:
        df = fdr.DataReader(code, start, end)
    except Exception as exc:
        logger.warning("index_fdr_failed market=%s error=%s", market, exc)
        return None
    if df is None or df.empty:
        return None

    closes = df["Close"].tail(60).reset_index(drop=True)
    if len(closes) < 2:
        return None

    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = date or datetime.now().strftime("%Y-%m-%d")
    out_path = CHARTS_DIR / f"{date_str}-{market}-spark.png"

    # 등락 색상: 시작 대비 마지막 비교
    color = "#34d399" if closes.iloc[-1] >= closes.iloc[0] else "#f87171"

    fig, ax = plt.subplots(figsize=(3.6, 0.9), dpi=120)
    ax.plot(closes.index, closes.values, color=color, linewidth=1.6)
    ax.fill_between(closes.index, closes.values, closes.min(), color=color, alpha=0.15)
    ax.axis("off")
    fig.patch.set_facecolor("#1e293b")
    ax.set_facecolor("#1e293b")
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
    fig.savefig(out_path, facecolor="#1e293b", bbox_inches="tight", pad_inches=0)
    plt.close(fig)
    return out_path


def index_spark_url_rel(market: str, date: str | None = None) -> str:
    date_str = date or datetime.now().strftime("%Y-%m-%d")
    return f"charts/{date_str}-{market}-spark.png"


def render_mini_candle(symbol: str, key: str, date: str | None = None,
                       source: str = "yf", days: int = 30) -> Path | None:
    """심볼 OHLC → 미니 캔들차트 PNG. source: yf(yfinance) | fdr(FinanceDataReader).

    지수·환율·유가·금 등 각 항목 카드의 흐름 표시용. 축·라벨 없는 다크 미니 캔들.
    """
    date_str = date or datetime.now().strftime("%Y-%m-%d")
    out = CHARTS_DIR / f"{date_str}-{key}-candle.png"
    try:
        if source == "fdr":
            import FinanceDataReader as fdr
            start = (datetime.now() - timedelta(days=days * 3)).strftime("%Y-%m-%d")
            df = fdr.DataReader(symbol, start)
        else:
            import yfinance as yf
            df = yf.download(symbol, period="3mo", interval="1d",
                             progress=False, auto_adjust=True)
            if df is not None and isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0] for c in df.columns]
        if df is None or df.empty:
            return None
        df = df[["Open", "High", "Low", "Close"]].dropna().tail(days)
        if len(df) < 5:
            return None
        CHARTS_DIR.mkdir(parents=True, exist_ok=True)
        mc = mpf.make_marketcolors(up="#34d399", down="#f87171", edge="inherit", wick="inherit")
        style = mpf.make_mpf_style(marketcolors=mc, facecolor="#1e293b", figcolor="#1e293b")
        fig, _ = mpf.plot(df, type="candle", style=style, axisoff=True,
                          figsize=(3.8, 1.3), returnfig=True, tight_layout=True)
        fig.savefig(out, facecolor="#1e293b", bbox_inches="tight", pad_inches=0.03)
        plt.close(fig)
        return out
    except Exception as exc:
        logger.warning("mini_candle_failed symbol=%s error=%s", symbol, exc)
        return None


def candle_url_rel(key: str, date: str | None = None) -> str:
    date_str = date or datetime.now().strftime("%Y-%m-%d")
    return f"charts/{date_str}-{key}-candle.png"


def cleanup_old_charts(keep_days: int = 7) -> int:
    """charts/ 의 오래된 PNG 삭제 (기본 7일 이전). git 용량 누적 방지. 삭제 개수 반환."""
    import time
    if not CHARTS_DIR.exists():
        return 0
    cutoff = time.time() - keep_days * 86400
    n = 0
    for f in CHARTS_DIR.glob("*.png"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                n += 1
        except Exception:
            pass
    if n:
        logger.info("cleanup_old_charts removed=%d keep_days=%d", n, keep_days)
    return n
