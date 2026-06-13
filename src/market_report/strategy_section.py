"""리포트용 전략(A/B/C) 스크린 + 보유종목 상태 수집.

일일 리포트(마감 전/후)에 '오늘 A/B/C 포착 종목' + '보유종목 상태'를 포함시키기 위한
수집 헬퍼. KIS 어댑터로 유니버스를 평가하고, 보유종목은 KIS 잔고 → 비면 config/holdings.yaml.
"""
from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path

import yaml

from src.alerts.holdings_report import diagnose_holdings
from src.indicators.core import average_true_range, moving_average, round_to_tick
from src.patterns.core import bullish_divergence, gave_back_recent_gain, ma_cross_signal
from src.screener.config import load_screener_config
from src.screener.engine import evaluate_strategy
from src.screener.pipeline import _is_etf, _is_pref

logger = logging.getLogger(__name__)

_HOLDINGS_CONFIG = Path(__file__).resolve().parent.parent.parent / "config" / "holdings.yaml"

# 주도주 보강 (대시보드와 동일)
_LEADERS = {
    "000660": "SK하이닉스", "005930": "삼성전자", "009150": "삼성전기",
    "011070": "LG이노텍", "066570": "LG전자", "005380": "현대차",
    "307950": "현대오토에버", "018260": "삼성에스디에스",
}


def _watchlist_tickers() -> list[tuple[str, str]]:
    """관심종목(SQLite watchlist) → [(ticker, name)]. 실패/빈 경우 []."""
    try:
        from src.config.settings import get_settings
        from src.storage.db import get_connection
        from src.storage.repos import WatchlistRepo
        conn = get_connection(get_settings().db_path)
        items = WatchlistRepo(conn).get_all()
        conn.close()
        return [(w.ticker, w.name) for w in items]
    except Exception as exc:
        logger.warning("watchlist_load_failed error=%s", exc)
        return []


def load_manual_holdings() -> list[dict]:
    """config/holdings.yaml의 수동 보유종목 로드. 없으면 빈 리스트."""
    if not _HOLDINGS_CONFIG.exists():
        return []
    try:
        raw = yaml.safe_load(_HOLDINGS_CONFIG.read_text(encoding="utf-8")) or {}
        return [h for h in raw.get("holdings", []) if isinstance(h, dict) and h.get("ticker")]
    except Exception as exc:
        logger.warning("holdings_config_load_failed error=%s", exc)
        return []


async def collect_screen_picks(adapter, per_strategy: int = 8,
                               drop_today: bool = False,
                               e_out: list[dict] | None = None,
                               surge_out: list[dict] | None = None,
                               support_out: list[dict] | None = None,
                               coil_out: list[dict] | None = None,
                               extra_universe: list[tuple[str, str]] | None = None) -> list[dict]:
    """오늘 A/B/C 전략 포착 종목 (유니버스: 주도주 + 핫종목).

    drop_today: 마지막 봉이 '오늘'(장전 미완성 봉)이면 제외하고 전일 마감 기준 평가.
                us_morning(07:30 장전) 시초 Top3용 — real ohlcv는 장전에 당일 복제봉을 줌.
    e_out: 주어지면 E전략(과매도 주도주, 일봉 조건) 후보를 여기 누적(4H RSI는 pipeline에서 결합).
    """
    from datetime import datetime as _dt
    _today = _dt.now().strftime("%Y%m%d") if drop_today else None

    cfg = load_screener_config()
    strategies = cfg.enabled_strategies()
    min_price = cfg.global_filters.get("min_price", 0)
    exclude_etf = cfg.global_filters.get("exclude_etf", False)
    min_trade_value = float(cfg.global_filters.get("min_trade_value", 0) or 0)  # 당일 거래대금 하한(원)
    min_market_cap = float(cfg.global_filters.get("min_market_cap", 0) or 0)    # 시가총액 하한(원)

    # 시총 필터용 맵 (FDR Marcap, 원 단위) — min_market_cap 설정 시에만 로드
    marcap_map: dict[str, int] = {}
    if min_market_cap > 0:
        try:
            from src.datasource.market_cap import get_market_cap_map
            marcap_map = await asyncio.to_thread(get_market_cap_map)
        except Exception as exc:
            logger.warning("screen_marcap_map_failed error=%s — 시총필터 생략", exc)

    universe = dict(_LEADERS)
    try:
        from src.datasource.base import RankingKind
        for kind in (RankingKind.VOLUME, RankingKind.CHANGE_PCT):
            for r in await adapter.get_ranking(kind, top=cfg.hot_stocks_top):
                if r.ticker and r.ticker not in universe:
                    universe[r.ticker] = r.name
    except Exception as exc:
        logger.warning("screen_picks_ranking_failed error=%s", exc)

    # 관심종목(watchlist) 추가 — B(급등후 눌림)는 당일 핫종목보다 관심종목에서 잘 잡힘
    for tk, nm in _watchlist_tickers():
        if tk not in universe:
            universe[tk] = nm

    # 수급 주도 유니버스 확장 — 외인/기관 순매수 상위 종목 추가(거래대금 컷 밖이어도 A/B/C/D 평가
    # 받게 함. 미래에셋생명처럼 수급 급등주를 Top3 후보로 포착, 사용자 2026-06-11)
    for tk, nm in (extra_universe or []):
        if tk and tk not in universe:
            universe[tk] = nm

    # 시총 상위 풀 (HTS식 디텍팅) — config market_cap: true
    if getattr(cfg, "universe_market_cap", False):
        try:
            from src.datasource.universe import get_market_cap_universe
            cap = await asyncio.to_thread(
                get_market_cap_universe, cfg.market_cap_kospi, cfg.market_cap_kosdaq,
                getattr(cfg, "market_cap_min_amount", 0))
            for tk, nm in cap:
                if tk not in universe:
                    universe[tk] = nm
        except Exception as exc:
            logger.warning("screen_picks_marketcap_failed error=%s", exc)

    counts: dict[str, int] = {}
    picks: list[dict] = []
    for tk, nm in universe.items():
        if _is_pref(nm) or (exclude_etf and _is_etf(nm)):  # 우선주 항상 제외
            continue
        await asyncio.sleep(random.uniform(0.2, 0.5))
        try:
            c = await adapter.get_ohlcv(tk, days=180)
        except Exception:
            continue
        if drop_today and c and c[-1].date == _today:  # 장전 미완성 당일봉 제외
            c = c[:-1]
        if len(c) < 135 or c[-1].close < min_price:
            continue
        # 거래대금 필터 (당일 종가×거래량) — 활발한 종목만
        if min_trade_value > 0 and (c[-1].close * c[-1].volume) < min_trade_value:
            continue
        # 시총 필터 — marcap 맵에 있고 하한 미달이면 제외 (맵에 없으면 데이터 공백이라 통과)
        if min_market_cap > 0:
            _cap = marcap_map.get(tk, 0)
            if _cap and _cap < min_market_cap:
                continue
        change_pct = (c[-1].close - c[-2].close) / c[-2].close * 100 if len(c) >= 2 and c[-2].close else 0.0
        # Top3 종합점수용 지표 (거래대금·20선이격·신고가근접)
        from math import log10
        _closes = [x.close for x in c]
        _ma20 = moving_average(_closes, 20)[-1]
        _liq = log10(max(c[-1].close * c[-1].volume, 1))
        _gap20 = (c[-1].close - _ma20) / _ma20 * 100 if _ma20 else 0.0
        _hi60 = max(x.high for x in c[-60:])
        _nh = (c[-1].close / _hi60 - 0.97) * 100 if _hi60 else 0.0
        _high_dd = (c[-1].close / _hi60 - 1) * 100 if _hi60 else 0.0  # 60일 고점 대비 낙폭(음수, B 표시용)
        # 5·10 단기 데드크로스 + 20일이격 → 단기눌림(🟢)/조정시작(⚠️) 신호 (domain SSOT)
        # CROSS_PULLBACK/CORRECTION/None — 보유 종목 홀드·익절 판단 + 리포트 표시용
        _cross = ma_cross_signal(_closes)
        # 강세 다이버전스(가격 신저점 + RSI 저점↑) — 참고 태그용(가중치 0). 백테스트 OOS 미통과(사용자 2026-06-12).
        _bdiv = bullish_divergence(c, mode="rsi")
        # 🔥 과열 판정(사용자 2026-06-05 수정): 일봉 BB(20,2) 상단 종가돌파 = 과열.
        # 기존엔 이격≥30%·거래량≥1.8배 AND 게이트라 대형 우량주(BB는 넘어도 이격 작음)가
        # 과열로 안 잡혀 추천됨(삼성화재·신세계 사례). → BB돌파 단독으로 완화, 이격·거래량은 보조.
        # (4시간봉 BB 돌파는 pipeline에서 overheat_4h로 별도 부착 → top3에서 합산 판정)
        from statistics import pstdev
        _std20 = pstdev(_closes[-20:]) if len(_closes) >= 20 else 0.0
        _bbup = (_ma20 + 2 * _std20) if _ma20 else 0.0
        _vols = [x.volume for x in c]
        _volavg20 = sum(_vols[-20:]) / 20 if len(_vols) >= 20 else 0.0
        _volx = c[-1].volume / _volavg20 if _volavg20 else 0.0
        _overheat = bool(_bbup and c[-1].close > _bbup)  # 일봉 BB 상단 종가돌파
        # ATR(변동성) 기반 손절가 — 현재가 - 1.5×ATR. 급등주는 넓게, 안정주는 좁게 자동.
        # 배수 1.5: 한 달 백테스트상 종가베팅 다음날 손절 7.4%(2.0×는 0% 무의미, 1.0×는 18.5% 휩쏘 과다)
        _atr = average_true_range([x.high for x in c], [x.low for x in c], _closes, 14)
        _price = c[-1].close
        if _atr and _price:
            _stop_price = round_to_tick(max(_price - 1.5 * _atr, 0.0))  # 호가단위 정렬
            _stop_pct = (_stop_price - _price) / _price * 100  # 음수 = 하락 손절폭
        else:
            _stop_price, _stop_pct = 0.0, 0.0
        for s in strategies:
            if counts.get(s.name, 0) >= per_strategy:
                continue
            res = evaluate_strategy(s.name, s.opinion, s.conditions, c, change_pct)
            if res.matched:
                # B 급반전 제외(사용자 2026-06-05, 삼성에스디에스): 최근 3일내 최근 10일 상승분의
                # 대부분(≥50%)을 반납했으면 '눌림'이 아니라 급반전 → B 추천에서 제외.
                if s.name.startswith("B") and gave_back_recent_gain(c):
                    continue
                _reason = "; ".join(res.reasons)
                if s.name.startswith("B"):  # B 설명란에 고점대비 낙폭 표시(사용자 2026-06-05)
                    _reason += f" · 고점대비 {_high_dd:+.1f}%"
                picks.append({
                    "strategy": s.name,
                    "ticker": tk, "name": nm,
                    "price": round(c[-1].close, 1),
                    "change_pct": round(change_pct, 2),
                    "reason": _reason,
                    "endstage": bool(res.metrics.get("endstage")),
                    "_liq": round(_liq, 2), "gap20": round(_gap20, 1), "_nh": round(_nh, 2),
                    "high_dd": round(_high_dd, 1),
                    "stop_price": round(_stop_price, 1) if _stop_price else 0,
                    "stop_pct": round(_stop_pct, 1),
                    "overheat": _overheat, "vol_x": round(_volx, 1),
                    "cross_signal": _cross,  # PULLBACK(🟢 단기눌림)/CORRECTION(⚠️ 조정시작)/None
                    "bull_div": bool(_bdiv.matched),  # 🔀 강세 다이버전스(참고 태그·가중치0, D에 표시)
                    "bull_div_rsidiv": round(_bdiv.metrics.get("rsi_div") or 0, 1) if _bdiv.matched else 0,
                    "theme": "",            # pipeline에서 judal 테마/업종 폴백으로 채움
                    "theme_kind": "",       # "theme"(judal 테마) | "sector"(네이버 세분업종)
                    "theme_idx": "",        # judal themeIdx (테마 링크용)
                    "is_theme_leader": False,
                })
                counts[s.name] = counts.get(s.name, 0) + 1
        # E전략(과매도 주도주, 일봉 조건) — 전략매칭과 무관하게 별도 수집(4H RSI는 pipeline 결합)
        if e_out is not None and len(e_out) < 40:
            from src.patterns.core import oversold_leader
            _er = oversold_leader(c)
            if _er.matched:
                e_out.append({
                    "ticker": tk, "name": nm, "price": round(c[-1].close, 1),
                    "change_pct": round(change_pct, 2), "gap20": round(_gap20, 1),
                    "rsi": round(float(_er.metrics.get("rsi", 0)), 0), "reason": _er.reason,
                    "volume": c[-1].volume, "trade_value": c[-1].close * c[-1].volume,
                })
        # 급등 초입(20일 신고가 돌파+거래량급증+당일강세) — 별도 수집(사용자 2026-06-05)
        if surge_out is not None and len(surge_out) < 40:
            from src.patterns.core import is_surge_start
            _sr = is_surge_start(c)
            if _sr.matched:
                surge_out.append({
                    "ticker": tk, "name": nm, "price": round(c[-1].close, 1),
                    "change_pct": round(change_pct, 2), "gap20": round(_gap20, 1),
                    "vol_x": round(_volx, 1), "reason": _sr.reason,
                    "volume": c[-1].volume, "trade_value": c[-1].close * c[-1].volume,
                })
        # F. 60일선 지지 마감(참고용) — 별도 수집. 종합점수/Top3 미반영(가중치 0), 단순 참고 섹션만.
        # 백테스트상 다음날 반등 엣지 없음(48~49%) 확인됨 → 추천 아님(사용자 2026-06-09)
        if support_out is not None and len(support_out) < 40:
            from src.patterns.core import is_ma60_support
            _fr = is_ma60_support(c)
            if _fr.matched:
                support_out.append({
                    "ticker": tk, "name": nm, "price": round(c[-1].close, 1),
                    "change_pct": round(change_pct, 2), "gap20": round(_gap20, 1),
                    "vol_x": round(_volx, 1), "reason": _fr.reason,
                    "volume": c[-1].volume, "trade_value": c[-1].close * c[-1].volume,
                })
        # G. 삼각수렴(코일) 임박(참고용) — 별도 수집. BB17 완화기준(테크윙류 포함). 가중치 0·Top3 미반영.
        # 'fresh'(신규진입)만: 신호 5일전엔 코일 아니었던 것 — 백테스트상 첫신호만 베타 상회(질질끄는 코일 제외).
        if coil_out is not None and len(coil_out) < 40:
            from src.patterns.core import is_coil_squeeze, is_long_triangle
            _cr = is_coil_squeeze(c, bb_max=17.0)
            if _cr.matched and not (len(c) >= 130 and is_coil_squeeze(c[:-5], bb_max=17.0).matched):
                _shape = {1: "대칭수렴", 2: "바닥지지수렴"}.get(int(_cr.metrics.get("shape", 0)), "")
                coil_out.append({
                    "ticker": tk, "name": nm, "price": round(c[-1].close, 1),
                    "change_pct": round(change_pct, 2), "shape": _shape, "mode": "단기",
                    "bb_width": _cr.metrics.get("bb_width"), "ma_conv": _cr.metrics.get("ma_conv"),
                    "reason": _cr.reason, "volume": c[-1].volume, "trade_value": c[-1].close * c[-1].volume,
                })
            else:
                # G 장기 모드 — 수개월 대형 삼각수렴(볼록껍질 추세선+실수축). 백테스트 66%·+8.7%/20일
                # 으로 단기 코일보다 엣지 강함(사용자 2026-06-11 HOOD·TSLA 사례).
                _lt = is_long_triangle(c, win=150)
                if _lt.matched:
                    coil_out.append({
                        "ticker": tk, "name": nm, "price": round(c[-1].close, 1),
                        "change_pct": round(change_pct, 2),
                        "shape": _lt.metrics.get("shape_name", "장기 대칭수렴"), "mode": "장기",
                        "bb_width": _lt.metrics.get("band_now_pct"), "ma_conv": None,
                        "reason": _lt.reason, "volume": c[-1].volume, "trade_value": c[-1].close * c[-1].volume,
                    })
    return picks


async def collect_holdings_status(adapter) -> list[dict]:
    """보유종목 상태 — KIS 잔고 우선, 비면 config 수동 보유종목."""
    try:
        # prefer_nxt=True: 보유종목 평가손익을 증권사 MTS와 일치(NXT 시간외 종가 반영)
        balance = await adapter.get_balance(prefer_nxt=True)
    except Exception as exc:
        logger.warning("holdings_balance_failed error=%s", exc)
        balance = []
    holdings = balance if balance else load_manual_holdings()
    if not holdings:
        return []
    return await diagnose_holdings(adapter, holdings=holdings)
