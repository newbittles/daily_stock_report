"""Kiwoom OpenAPI+ adapter (OCX/COM 기반).

인증: HTS 로그인 팝업 (CommConnect). 앱키/시크릿 없음.
TR 호출: block_request()를 asyncio.to_thread()로 래핑해 async 인터페이스 제공.
PyKiwoom 공식 문서: https://pykiwoom.readthedocs.io
KOA Studio TR 목록: KOAStudio에서 확인 필요.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

from src.datasource.base import Candle, MarketDataSource, Quote, RankedStock, RankingKind

logger = logging.getLogger(__name__)

MAX_RETRY = 3

# TR 코드 — KOAStudio에서 확인된 값
_TR = {
    "quote": "OPT10001",       # 주식기본정보요청
    "ohlcv_daily": "OPT10081", # 주식일봉차트조회요청
    "rank_change": "OPT10027", # 등락률순위요청
    "rank_volume": "OPT10030", # 거래량순위요청
    "rank_trade": "OPT10031",  # 거래대금순위요청
    "trade_history": "OPW00007",  # 계좌별주문체결내역상세 (TODO: KOAStudio 확인)
}

_RANKING_TR: dict[RankingKind, str] = {
    RankingKind.CHANGE_PCT: _TR["rank_change"],
    RankingKind.VOLUME: _TR["rank_volume"],
    RankingKind.TRADE_VALUE: _TR["rank_trade"],
}


def _clean(value: Any) -> str:
    """콤마·부호(+/-) 제거 후 문자열 반환."""
    return str(value).replace(",", "").replace("+", "").strip()


def _to_float(value: Any) -> float:
    s = _clean(value)
    # 하락인 경우 '-' 기호가 앞에 붙는 경우가 있음
    try:
        return float(s)
    except ValueError:
        return 0.0


def _to_int(value: Any) -> int:
    try:
        return int(_clean(value))
    except ValueError:
        return 0


def _to_abs_float(value: Any) -> float:
    """현재가처럼 절댓값이 필요한 경우 (등락 방향은 등락율로 판단)."""
    return abs(_to_float(value))


class KiwoomError(Exception):
    pass


class KiwoomAdapter:
    """MarketDataSource adapter for Kiwoom OpenAPI+ (OCX/COM).

    사용 전 반드시 `await adapter.connect()` 로 HTS 로그인 완료 후 사용.
    block_request()는 asyncio.to_thread()로 실행 — QApplication은 main에서 유지.

    kiwoom 인수: main 스레드에서 미리 생성한 Kiwoom() 인스턴스를 전달해야 한다.
    QAxWidget은 메인 Qt 스레드에서만 생성 가능하므로 asyncio.to_thread() 내부 생성 금지.
    """

    def __init__(self, account_no: str, env: str, kiwoom: Any = None) -> None:
        self._account_no = account_no
        self._env = env  # "real" | "paper"
        self._kiwoom: Any = kiwoom  # main 스레드에서 미리 생성된 Kiwoom 인스턴스

    async def connect(self) -> None:
        """로그인 완료 확인. kiwoom 인스턴스는 main 스레드에서 미리 생성·로그인돼야 한다."""
        if self._kiwoom is None:
            raise RuntimeError(
                "Kiwoom 인스턴스가 없습니다. main 스레드에서 Kiwoom()을 생성 후 "
                "KiwoomAdapter(kiwoom=instance)로 전달하세요."
            )
        logger.info("kiwoom_connected env=%s", self._env)

    def _do_connect(self) -> None:
        from pykiwoom.kiwoom import Kiwoom  # 런타임 import (OCX 환경에만 존재)
        self._kiwoom = Kiwoom()
        self._kiwoom.CommConnect(block=True)

    async def close(self) -> None:
        pass  # OCX는 별도 close 불필요

    async def _request(self, fn_name: str, **kwargs: Any) -> Any:
        """block_request를 retry+backoff로 래핑."""
        for attempt in range(MAX_RETRY):
            try:
                if attempt > 0:
                    wait = random.uniform(5 * (2 ** (attempt - 1)), 10 * (2 ** (attempt - 1)))
                    logger.info("kiwoom_retry attempt=%d wait=%.1fs", attempt, wait)
                    await asyncio.sleep(wait)
                else:
                    await asyncio.sleep(random.uniform(0.3, 0.8))

                result = await asyncio.to_thread(
                    self._kiwoom.block_request, fn_name, **kwargs
                )
                return result

            except Exception as exc:
                logger.error("kiwoom_request_error fn=%s attempt=%d error=%s", fn_name, attempt, exc)
                if attempt == MAX_RETRY - 1:
                    raise KiwoomError(f"{fn_name} failed after {MAX_RETRY} attempts") from exc

    # ── MarketDataSource interface ──────────────────────────────────────────

    async def get_quote(self, ticker: str) -> Quote:
        df = await self._request(
            _TR["quote"],
            종목코드=ticker,
            기준일자="",
            수정주가구분="1",
            output="주식기본정보",
            next=0,
        )
        if df is None or df.empty:
            raise KiwoomError(f"No data returned for {ticker}")

        row = df.iloc[0]
        return Quote(
            ticker=ticker,
            name=str(row.get("종목명", "")).strip(),
            price=_to_abs_float(row.get("현재가", 0)),
            change_pct=_to_float(row.get("등락율", 0)),
            volume=_to_int(row.get("거래량", 0)),
            timestamp=str(row.get("기준일자", "")).strip(),
        )

    async def get_ohlcv(self, ticker: str, days: int = 60) -> list[Candle]:
        import datetime
        today = datetime.date.today().strftime("%Y%m%d")
        df = await self._request(
            _TR["ohlcv_daily"],
            종목코드=ticker,
            기준일자=today,
            끝일자="",
            수정주가구분=1,
            output="주식일봉차트조회",
            next=0,
        )
        if df is None or df.empty:
            return []

        result: list[Candle] = []
        for _, row in df.head(days).iterrows():
            try:
                result.append(
                    Candle(
                        date=str(row.get("일자", "")).strip(),
                        open=_to_abs_float(row.get("시가", 0)),
                        high=_to_abs_float(row.get("고가", 0)),
                        low=_to_abs_float(row.get("저가", 0)),
                        close=_to_abs_float(row.get("현재가", 0)),  # 일봉의 현재가 = 종가
                        volume=_to_int(row.get("거래량", 0)),
                    )
                )
            except Exception:
                continue
        return result

    async def get_trade_history(self, date: str) -> list[dict]:
        """특정 날짜의 체결내역 조회. date 형식: 'YYYYMMDD'.

        TODO: KOAStudio에서 OPW00007 입력/출력 필드명 확인 후 파라미터 조정 필요.
        """
        df = await self._request(
            _TR["trade_history"],
            계좌번호=self._account_no,
            비밀번호="",
            비밀번호입력매체구분="00",
            조회구분=1,
            주문일자=date,
            output="계좌별주문체결내역상세",
            next=0,
        )
        if df is None or df.empty:
            return []

        records = []
        for _, row in df.iterrows():
            try:
                raw_type = str(row.get("주문구분", "")).strip()
                trade_type = "BUY" if "매수" in raw_type else "SELL" if "매도" in raw_type else None
                if trade_type is None:
                    continue

                # 체결수량 0이면 미체결 → 건너뜀
                qty = _to_int(row.get("체결수량", 0))
                if qty == 0:
                    continue

                records.append({
                    "ticker": str(row.get("종목번호", row.get("종목코드", ""))).strip().lstrip("A"),
                    "name": str(row.get("종목명", "")).strip(),
                    "trade_type": trade_type,
                    "price": _to_abs_float(row.get("체결단가", row.get("체결가", 0))),
                    "quantity": qty,
                    "trade_date": date,
                    "order_no": str(row.get("주문번호", "")).strip() or None,
                })
            except Exception:
                continue
        return records

    async def get_ranking(self, kind: RankingKind, top: int = 20) -> list[RankedStock]:
        tr_code = _RANKING_TR[kind]

        # TR별 파라미터가 다름 — KOAStudio에서 각 TR의 입력값 확인 필요
        if kind == RankingKind.CHANGE_PCT:
            df = await self._request(
                tr_code,
                시장구분="000",   # 전체
                정렬구분="1",     # 등락률 기준
                관리종목포함="1",
                신용거래융자="0",
                거래량="0",
                등락구분="1",     # 상승
                output="등락률순위",
                next=0,
            )
        elif kind == RankingKind.VOLUME:
            df = await self._request(
                tr_code,
                시장구분="000",
                정렬구분="1",
                관리종목포함="1",
                신용거래융자="0",
                거래량="0",
                등락구분="1",
                output="거래량순위",
                next=0,
            )
        else:  # TRADE_VALUE
            df = await self._request(
                tr_code,
                시장구분="000",
                정렬구분="1",
                관리종목포함="1",
                신용거래융자="0",
                거래량="0",
                등락구분="1",
                output="거래대금순위",
                next=0,
            )

        if df is None or df.empty:
            return []

        result: list[RankedStock] = []
        for i, (_, row) in enumerate(df.head(top).iterrows()):
            try:
                result.append(
                    RankedStock(
                        rank=i + 1,
                        ticker=str(row.get("종목코드", "")).strip(),
                        name=str(row.get("종목명", "")).strip(),
                        price=_to_abs_float(row.get("현재가", 0)),
                        change_pct=_to_float(row.get("등락율", 0)),
                        volume=_to_int(row.get("거래량", 0)),
                    )
                )
            except Exception:
                continue
        return result
