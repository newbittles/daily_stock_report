"""한국투자증권 KIS Open API (REST) 어댑터.

MarketDataSource 포트 구현 + 잔고/체결내역 확장.
모든 TR_ID·엔드포인트는 KIS 공식 GitHub(koreainvestment/open-trading-api) 검증값.

전역 CLAUDE.md §7 적용: 랜덤 딜레이, 재시도 3회+백오프, HARD STOP(429/401 급변).

검증된 엔드포인트 (2026-05 기준):
  현재가     GET /uapi/domestic-stock/v1/quotations/inquire-price        FHKST01010100
  일봉기간   GET /uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice  FHKST03010100
  분봉       GET /uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice   FHKST03010200
  거래량순위 GET /uapi/domestic-stock/v1/quotations/volume-rank          FHPST01710000
  등락률순위 GET /uapi/domestic-stock/v1/ranking/fluctuation             FHPST01700000
  잔고       GET /uapi/domestic-stock/v1/trading/inquire-balance         TTTC8434R(실)/VTTC8434R(모)
  체결내역   GET /uapi/domestic-stock/v1/trading/inquire-daily-ccld      TTTC0081R(실)/VTSC9215R(모)
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta
from typing import Any

import httpx

from src.datasource.base import Candle, Quote, RankedStock, RankingKind
from src.datasource.kis.token import KisTokenManager

logger = logging.getLogger(__name__)

MAX_RETRY = 3

# TR_ID — 실전/모의 구분이 필요한 것만 dict, 나머지는 공통
_TR = {
    "quote": "FHKST01010100",
    "ohlcv_daily": "FHKST03010100",
    "ohlcv_minute": "FHKST03010200",
    "rank_volume": "FHPST01710000",
    "rank_fluctuation": "FHPST01700000",
    # 시세분석 (실전/모의 공통 — FH 계열, 공식 repo 검증 2026-05-31)
    "sector_price": "FHPUP02140000",       # 국내업종 구분별전체시세
    "foreign_inst_total": "FHPTJ04400000",  # 국내기관_외국인 매매종목 가집계
}
_TR_ENV = {
    "balance": {"real": "TTTC8434R", "paper": "VTTC8434R"},
    "ccld": {"real": "TTTC0081R", "paper": "VTSC9215R"},
}


class KisError(Exception):
    pass


class KisHardStop(KisError):
    """429/인증 급변 등 자동 재시도 금지 신호."""


def _f(v: Any) -> float:
    try:
        return float(str(v).replace(",", "").strip() or 0)
    except (ValueError, TypeError):
        return 0.0


def _i(v: Any) -> int:
    return int(_f(v))


class KisAdapter:
    """KIS REST 기반 MarketDataSource 어댑터.

    사용 전 별도 connect 불필요 — 첫 요청 시 토큰 자동 발급.
    """

    def __init__(self, app_key: str, app_secret: str, account_no: str, env: str = "paper") -> None:
        self._account_no = account_no
        self._env = env
        self._token_mgr = KisTokenManager(app_key, app_secret, env)
        self._base = self._token_mgr.base_url

    # ── 내부 요청 헬퍼 ────────────────────────────────────────────────────────
    async def _request(
        self, path: str, tr_id: str, params: dict[str, Any], *, tr_cont: str = ""
    ) -> dict[str, Any]:
        """GET 요청 + 재시도·백오프·HARD STOP. 응답 JSON 반환."""
        await self._token_mgr.get_token()  # 토큰 확보 (만료 시 갱신)
        url = f"{self._base}{path}"

        last_exc: Exception | None = None
        for attempt in range(MAX_RETRY):
            if attempt > 0:
                wait = random.uniform(2 * (2 ** (attempt - 1)), 5 * (2 ** (attempt - 1)))
                logger.info("kis_retry path=%s attempt=%d wait=%.1fs", path, attempt, wait)
                await asyncio.sleep(wait)
            else:
                await asyncio.sleep(random.uniform(0.2, 0.6))

            headers = self._token_mgr.auth_headers(tr_id, tr_cont=tr_cont)
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(url, headers=headers, params=params)

                # HARD STOP: 429 / 인증 오류
                if resp.status_code in (429, 401, 403):
                    raise KisHardStop(f"HTTP {resp.status_code}: {resp.text[:200]}")

                resp.raise_for_status()
                data = resp.json()

                # KIS 응답 코드: rt_cd '0'이 정상
                rt_cd = data.get("rt_cd")
                if rt_cd is not None and rt_cd != "0":
                    msg = data.get("msg1", "")
                    raise KisError(f"rt_cd={rt_cd} msg={msg}")

                return data

            except KisHardStop:
                raise  # 재시도 금지
            except Exception as exc:
                last_exc = exc
                logger.warning("kis_request_error path=%s attempt=%d error=%s", path, attempt, exc)

        raise KisError(f"{path} failed after {MAX_RETRY} attempts") from last_exc

    # ── 종목 제외 필터 (관리/경고/정지 등) ───────────────────────────────────
    async def get_exclusion_status(self, ticker: str, strict: bool = False) -> dict:
        """종목 제외 사유 판정 — inquire-price의 상태 필드 기반.

        기본(고위험만): 관리종목·거래정지·투자경고·투자위험.
          → 증거금100%·단기과열·투자주의는 제외 안 함 (대세상승주에 자주 붙음.
             대우건설+654%, SK네트웍스, 성호전자 등이 증거금100%였음 — 검증된 사실).
        strict=True: 위 + 증거금100%·단기과열·투자주의·투자유의도 제외 (안전 우선).
        반환: {"excluded": bool, "reasons": [str...], "name": 업종명}
        """
        data = await self._request(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            _TR["quote"],
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker},
        )
        out = data.get("output", {})
        reasons: list[str] = []

        # ── 고위험 (항상 제외) ──
        if str(out.get("mang_issu_cls_code", "N")).strip() == "Y":
            reasons.append("관리종목")
        if str(out.get("temp_stop_yn", "N")).strip() == "Y":
            reasons.append("거래정지")
        warn = str(out.get("mrkt_warn_cls_code", "00")).strip()
        if warn == "02":
            reasons.append("투자경고")
        elif warn == "03":
            reasons.append("투자위험")

        # ── strict 모드에서만 제외 (대세상승주에 자주 붙어 기본은 허용) ──
        if strict:
            if warn == "01":
                reasons.append("투자주의")
            if str(out.get("invt_caful_yn", "N")).strip() == "Y":
                reasons.append("투자유의")
            if str(out.get("short_over_yn", "N")).strip() == "Y":
                reasons.append("단기과열")
            if _f(out.get("marg_rate")) >= 100:
                reasons.append("증거금100%")

        return {"excluded": bool(reasons), "reasons": reasons,
                "name": str(out.get("bstp_kor_isnm", "")).strip()}

    # ── MarketDataSource 인터페이스 ───────────────────────────────────────────
    async def get_quote(self, ticker: str) -> Quote:
        data = await self._request(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            _TR["quote"],
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker},
        )
        out = data.get("output", {})
        return Quote(
            ticker=ticker,
            name=str(out.get("bstp_kor_isnm", "")).strip(),  # 업종명(종목명 대용) — 정확명은 별도 TR
            price=_f(out.get("stck_prpr")),
            change_pct=_f(out.get("prdy_ctrt")),
            volume=_i(out.get("acml_vol")),
            timestamp=datetime.now().strftime("%Y%m%d"),
        )

    async def get_nxt_quote(self, ticker: str) -> Quote:
        """NXT(넥스트레이드) 현재가 — 프리장(08:00~08:50)·애프터마켓(15:30~20:00) 시세.

        inquire-price에 시장코드 NX 사용(2026-06-08 프리장 실측 확인: prdy_ctrt가
        전일 KRX 종가 대비 NXT 등락률로 계산되어 옴). NXT 미체결/미지원 종목은 price=0."""
        data = await self._request(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            _TR["quote"],
            {"FID_COND_MRKT_DIV_CODE": "NX", "FID_INPUT_ISCD": ticker},
        )
        out = data.get("output", {})
        return Quote(
            ticker=ticker,
            name=str(out.get("bstp_kor_isnm", "")).strip(),
            price=_f(out.get("stck_prpr")),
            change_pct=_f(out.get("prdy_ctrt")),
            volume=_i(out.get("acml_vol")),
            timestamp=datetime.now().strftime("%Y%m%d"),
        )

    async def get_ohlcv(self, ticker: str, days: int = 100, end_date: str | None = None) -> list[Candle]:
        """일봉 조회. KIS는 1회 100건 제한 → days>100이면 기간 분할 다회 호출.

        end_date: 조회 종료일 YYYYMMDD (기본 오늘). 과거 역검증 시 지정.
        """
        end_dt = (
            datetime.strptime(end_date, "%Y%m%d") if end_date else datetime.now()
        )
        # 필요 봉수의 약 1.5배 달력일 + 여유 (주말·휴일 감안)
        need = days + 10
        all_candles: dict[str, Candle] = {}  # date → Candle (중복 제거)

        cursor_end = end_dt
        # 100건씩 최대 (days//100 + 2)회 호출
        max_calls = days // 100 + 2
        for _ in range(max_calls):
            seg_end = cursor_end.strftime("%Y%m%d")
            seg_start = (cursor_end - timedelta(days=150)).strftime("%Y%m%d")
            data = await self._request(
                "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                _TR["ohlcv_daily"],
                {
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": ticker,
                    "FID_INPUT_DATE_1": seg_start,
                    "FID_INPUT_DATE_2": seg_end,
                    "FID_PERIOD_DIV_CODE": "D",
                    "FID_ORG_ADJ_PRC": "0",
                },
            )
            rows = data.get("output2", []) or []
            seg_dates = []
            for r in rows:
                d = str(r.get("stck_bsop_date") or "")
                if not d:
                    continue
                seg_dates.append(d)
                if d not in all_candles:
                    all_candles[d] = Candle(
                        date=d,
                        open=_f(r.get("stck_oprc")),
                        high=_f(r.get("stck_hgpr")),
                        low=_f(r.get("stck_lwpr")),
                        close=_f(r.get("stck_clpr")),
                        volume=_i(r.get("acml_vol")),
                    )
            if len(all_candles) >= need or not seg_dates:
                break
            # 다음 구간: 이번 구간 가장 오래된 날짜 직전부터
            oldest = min(seg_dates)
            cursor_end = datetime.strptime(oldest, "%Y%m%d") - timedelta(days=1)

        candles = [all_candles[d] for d in sorted(all_candles)]  # 과거→최신
        return candles[-days:] if len(candles) > days else candles

    async def get_price_safe(self, ticker: str) -> float:
        """현재가 — get_quote 우선, 실패(inquire-price 500 장애 #484) 시 일봉 폴백.

        real 도메인 장중엔 일봉 마지막봉 close=현재가(미완성봉), 마감 후엔 당일 종가.
        quote가 종일 500이어도 자동매매·현황이 멈추지 않게 한다. 둘 다 실패 시 0.0."""
        try:
            q = await self.get_quote(ticker)
            if q.price > 0:
                return float(q.price)
        except Exception as exc:  # noqa: BLE001
            logger.info("price_fallback_to_ohlcv ticker=%s reason=%s", ticker, type(exc).__name__)
        try:
            candles = await self.get_ohlcv(ticker, days=3)
            if candles and candles[-1].close > 0:
                return float(candles[-1].close)
        except Exception as exc:  # noqa: BLE001
            logger.warning("price_safe_ohlcv_failed ticker=%s error=%s", ticker, exc)
        return 0.0

    async def get_today_minutes(self, ticker: str, day: str | None = None) -> list[dict]:
        """당일 1분봉(과거→현재) — 장중 흐름 분석용(#473/#474).

        inquire-time-itemchartprice(FHKST03010200, 검증 TR)를 FID_INPUT_HOUR_1을
        뒤로 밀며 09:00까지 페이징(1회 30봉, 당일만 INCU_YN=N). 정규장(09:00~15:30).
        반환 [{hhmm('HHMM'), open, high, low, close, volume}] 과거→현재. 실패/빈 시 [].
        §7: 페이지 간 분산 딜레이. HARD STOP은 전파(삼키지 않음)."""
        day = day or datetime.now().strftime("%Y%m%d")
        rows: dict[str, dict] = {}  # 'HHMMSS' → bar (당일만)
        cursor = "153000"
        prev_oldest: str | None = None
        for _ in range(40):
            params = {
                "FID_ETC_CLS_CODE": "", "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker, "FID_INPUT_HOUR_1": cursor,
                "FID_PW_DATA_INCU_YN": "N",  # 당일만
            }
            try:
                data = await self._request(
                    "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
                    _TR["ohlcv_minute"], params,
                )
            except KisHardStop:
                raise  # §7: 429/인증급변 → 전체 중단
            except Exception as exc:  # noqa: BLE001
                logger.warning("today_minutes_failed ticker=%s error=%s", ticker, exc)
                break
            out = data.get("output2", []) or []
            today = [r for r in out if str(r.get("stck_bsop_date", "")).strip() == day]
            if not today:
                break
            for r in today:
                h = str(r.get("stck_cntg_hour", "")).strip()
                if h and h not in rows:
                    rows[h] = r
            oldest = min(str(r.get("stck_cntg_hour", "")).strip() for r in today)
            if oldest == prev_oldest or oldest <= "090000":
                break
            prev_oldest = oldest
            cursor = oldest
            await asyncio.sleep(random.uniform(0.1, 0.25))  # §7 분산 딜레이

        bars: list[dict] = []
        for h in sorted(rows):  # 과거→현재
            r = rows[h]
            bar = {
                "hhmm": h[:4],
                "open": _f(r.get("stck_oprc")), "high": _f(r.get("stck_hgpr")),
                "low": _f(r.get("stck_lwpr")), "close": _f(r.get("stck_prpr")),
                "volume": _f(r.get("cntg_vol")),
            }
            if bar["open"] > 0 and bar["close"] > 0:
                bars.append(bar)
        return bars

    async def get_ranking(self, kind: RankingKind, top: int = 20) -> list[RankedStock]:
        if kind == RankingKind.VOLUME:
            return await self._ranking_volume(top)
        # CHANGE_PCT, TRADE_VALUE → 등락률 순위 API
        return await self._ranking_fluctuation(top)

    async def _ranking_volume(self, top: int) -> list[RankedStock]:
        data = await self._request(
            "/uapi/domestic-stock/v1/quotations/volume-rank",
            _TR["rank_volume"],
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_COND_SCR_DIV_CODE": "20171",
                "FID_INPUT_ISCD": "0000",       # 전체
                "FID_DIV_CLS_CODE": "0",
                "FID_BLNG_CLS_CODE": "0",        # 0:평균거래량
                "FID_TRGT_CLS_CODE": "111111111",
                "FID_TRGT_EXLS_CLS_CODE": "000000",
                "FID_INPUT_PRICE_1": "",
                "FID_INPUT_PRICE_2": "",
                "FID_VOL_CNT": "",
                "FID_INPUT_DATE_1": "",
            },
        )
        return self._parse_ranking(data.get("output", []), top)

    async def _ranking_fluctuation(self, top: int) -> list[RankedStock]:
        data = await self._request(
            "/uapi/domestic-stock/v1/ranking/fluctuation",
            _TR["rank_fluctuation"],
            {
                "fid_cond_mrkt_div_code": "J",
                "fid_cond_scr_div_code": "20170",
                "fid_input_iscd": "0000",
                "fid_rank_sort_cls_code": "0",   # 0:상승률
                "fid_input_cnt_1": "0",
                "fid_prc_cls_code": "0",
                "fid_input_price_1": "",
                "fid_input_price_2": "",
                "fid_vol_cnt": "",
                "fid_trgt_cls_code": "0",
                "fid_trgt_exls_cls_code": "0",
                "fid_div_cls_code": "0",
                "fid_rsfl_rate1": "",
                "fid_rsfl_rate2": "",
            },
        )
        return self._parse_ranking(data.get("output", []), top)

    async def get_nxt_overtime_gainers(self, top: int = 7, scan: int = 20) -> list[dict[str, Any]]:
        """NXT(넥스트레이드) 시간외 상위 상승률 — '정규장 종가 대비' 시간외(NXT) 변동률 기준.

        마감 후(NXT 애프터마켓 15:30~20:00)에 의미. KIS 등락률순위에 NX(넥스트레이드) 시장코드로
        후보를 받고(기본 prdy_ctrt=전일대비라 정규장 포함), 각 종목의 '정규장 종가'(J 현재가) 대비
        NXT 현재가 변동률을 직접 계산해 시간외 상승분만 추출·정렬한다(전역 §7: 종목간 분산 딜레이).
        반환: [{ticker, name, nxt_price, reg_close, overtime_pct}] overtime_pct 내림차순, 양수만.
        NXT 미지원/실패 시 빈 리스트(리포트 best-effort)."""
        data = await self._request(
            "/uapi/domestic-stock/v1/ranking/fluctuation", _TR["rank_fluctuation"],
            {
                "fid_cond_mrkt_div_code": "NX",  # 넥스트레이드(NXT) — 2026-06-05 실측 지원 확인
                "fid_cond_scr_div_code": "20170", "fid_input_iscd": "0000",
                "fid_rank_sort_cls_code": "0", "fid_input_cnt_1": "0", "fid_prc_cls_code": "0",
                "fid_input_price_1": "", "fid_input_price_2": "", "fid_vol_cnt": "",
                "fid_trgt_cls_code": "0", "fid_trgt_exls_cls_code": "0", "fid_div_cls_code": "0",
                "fid_rsfl_rate1": "", "fid_rsfl_rate2": "",
            },
        )
        rows = data.get("output", []) or []
        out: list[dict[str, Any]] = []
        for r in rows[:scan]:
            ticker = str(r.get("stck_shrn_iscd") or r.get("mksc_shrn_iscd") or "").strip()
            nxt = _f(r.get("stck_prpr"))
            if not ticker or nxt <= 0:
                continue
            try:  # 정규장 종가 = J(KRX) 현재가(마감 후엔 종가 확정값)
                q = await self._request(
                    "/uapi/domestic-stock/v1/quotations/inquire-price", _TR["quote"],
                    {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker},
                )
                reg = _f((q.get("output") or {}).get("stck_prpr"))
            except Exception as exc:  # noqa: BLE001
                logger.debug("nxt_regclose_failed ticker=%s error=%s", ticker, exc)
                continue
            if reg <= 0:
                continue
            overtime = (nxt - reg) / reg * 100
            if overtime > 0:  # 시간외 상승분만
                out.append({"ticker": ticker, "name": str(r.get("hts_kor_isnm", "")).strip(),
                            "nxt_price": nxt, "reg_close": reg, "overtime_pct": round(overtime, 2)})
            await asyncio.sleep(random.uniform(0.1, 0.25))
        out.sort(key=lambda x: x["overtime_pct"], reverse=True)
        logger.info("nxt_overtime_gainers scan=%d found=%d", min(scan, len(rows)), len(out))
        return out[:top]

    @staticmethod
    def _parse_ranking(rows: list[dict], top: int) -> list[RankedStock]:
        result: list[RankedStock] = []
        for i, r in enumerate(rows[:top], 1):
            ticker = str(r.get("mksc_shrn_iscd") or r.get("stck_shrn_iscd") or "").strip()
            if not ticker:
                continue
            result.append(RankedStock(
                rank=i,
                ticker=ticker,
                name=str(r.get("hts_kor_isnm", "")).strip(),
                price=_f(r.get("stck_prpr")),
                change_pct=_f(r.get("prdy_ctrt")),
                volume=_i(r.get("acml_vol")),
            ))
        return result

    # ── 시세분석: 업종 등락 / 투자자(외국인·기관) 순매수 ───────────────────────
    async def get_sector_prices(self, market: str = "K") -> list[dict[str, Any]]:
        """국내업종 구분별 전체시세 → [{code,name,index,change,change_pct,volume}].

        market: K(거래소/코스피), Q(코스닥), K2(코스피200). iscd는 0001(코스피)/1001(코스닥).
        """
        iscd = "1001" if market == "Q" else "0001"
        data = await self._request(
            "/uapi/domestic-stock/v1/quotations/inquire-index-category-price",
            _TR["sector_price"],
            {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": iscd,
             "FID_COND_SCR_DIV_CODE": "20214", "FID_MRKT_CLS_CODE": market,
             "FID_BLNG_CLS_CODE": "0"},
        )
        # output2 = 업종별 리스트(38행), output1 = 종합 요약 (probe 검증 2026-05-31)
        rows = data.get("output2") or []
        result = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            result.append({
                "code": str(r.get("bstp_cls_code", "")).strip(),
                "name": str(r.get("hts_kor_isnm", "")).strip(),
                "index": _f(r.get("bstp_nmix_prpr")),
                "change_pct": _f(r.get("bstp_nmix_prdy_ctrt")),
                "volume": _i(r.get("acml_vol")),
            })
        return result

    async def get_investor_net_buy(
        self, investor: str = "foreign", side: str = "buy", market: str = "0000",
    ) -> list[dict[str, Any]]:
        """외국인/기관 순매수(도) 상위 종목.

        investor: foreign(외국인)/inst(기관)/all(전체). side: buy(순매수)/sell(순매도).
        market: 0000전체/0001코스피/1001코스닥. 금액 기준 정렬.
        반환: [{ticker,name,price,change_pct,net_qty,frgn_net_value,orgn_net_value}].
        """
        etc = {"all": "0", "foreign": "1", "inst": "2"}.get(investor, "1")
        rank = "1" if side == "sell" else "0"
        data = await self._request(
            "/uapi/domestic-stock/v1/quotations/foreign-institution-total",
            _TR["foreign_inst_total"],
            {"FID_COND_MRKT_DIV_CODE": "V", "FID_COND_SCR_DIV_CODE": "16449",
             "FID_INPUT_ISCD": market, "FID_DIV_CLS_CODE": "1",
             "FID_RANK_SORT_CLS_CODE": rank, "FID_ETC_CLS_CODE": etc},
        )
        rows = data.get("output", []) or []
        result = []
        for r in rows:
            result.append({
                "ticker": str(r.get("mksc_shrn_iscd", "")).strip(),
                "name": str(r.get("hts_kor_isnm", "")).strip(),
                "price": _f(r.get("stck_prpr")),
                "change_pct": _f(r.get("prdy_ctrt")),
                "net_qty": _i(r.get("ntby_qty")),
                "frgn_net_value": _i(r.get("frgn_ntby_tr_pbmn")),  # 외국인 순매수 금액(백만)
                "orgn_net_value": _i(r.get("orgn_ntby_tr_pbmn")),  # 기관 순매수 금액(백만)
            })
        return result

    async def get_stock_investor_daily(self, ticker: str, days: int = 10) -> list[dict[str, Any]]:
        """종목별 일별 투자자 순매수 (개인/외국인/기관) — 최신순. 연속 순매수일 계산용.

        inquire-investor(FHKST01010900). 반환: [{date, prsn, frgn, orgn}] (순매수 수량).
        """
        data = await self._request(
            "/uapi/domestic-stock/v1/quotations/inquire-investor",
            "FHKST01010900",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker},
        )
        out = data.get("output") or []
        rows = []
        for r in out[:days]:
            rows.append({
                "date": str(r.get("stck_bsop_date", "")).strip(),
                "prsn": _i(r.get("prsn_ntby_qty")),
                "frgn": _i(r.get("frgn_ntby_qty")),
                "orgn": _i(r.get("orgn_ntby_qty")),
            })
        return rows

    # ── 확장: 잔고 / 체결내역 ─────────────────────────────────────────────────
    async def get_balance(self) -> list[dict[str, Any]]:
        """주식 잔고 조회 — 보유 종목 리스트.

        계좌번호는 'CANO(8자리)-ACNT_PRDT_CD(2자리)' 형식으로 분리.
        """
        cano, prdt = self._split_account()
        tr_id = _TR_ENV["balance"][self._env]
        data = await self._request(
            "/uapi/domestic-stock/v1/trading/inquire-balance",
            tr_id,
            {
                "CANO": cano,
                "ACNT_PRDT_CD": prdt,
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "",
                "INQR_DVSN": "02",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "00",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
        )
        holdings = data.get("output1", []) or []
        result = []
        for h in holdings:
            qty = _i(h.get("hldg_qty"))
            if qty == 0:
                continue
            result.append({
                "ticker": str(h.get("pdno", "")).strip(),
                "name": str(h.get("prdt_name", "")).strip(),
                "quantity": qty,
                "avg_price": _f(h.get("pchs_avg_pric")),
                "current_price": _f(h.get("prpr")),
                "eval_profit": _f(h.get("evlu_pfls_amt")),
                "profit_rate": _f(h.get("evlu_pfls_rt")),
            })
        return result

    async def get_trade_history(self, start: str, end: str) -> list[dict[str, Any]]:
        """체결내역 조회. 날짜 형식: YYYYMMDD.

        TradeHistoryRepo.upsert()용 dict 리스트 반환.
        """
        cano, prdt = self._split_account()
        tr_id = _TR_ENV["ccld"][self._env]
        data = await self._request(
            "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
            tr_id,
            {
                "CANO": cano,
                "ACNT_PRDT_CD": prdt,
                "INQR_STRT_DT": start,
                "INQR_END_DT": end,
                "SLL_BUY_DVSN_CD": "00",   # 00:전체 01:매도 02:매수
                "INQR_DVSN": "00",
                "PDNO": "",
                "CCLD_DVSN": "01",          # 01:체결
                "ORD_GNO_BRNO": "",
                "ODNO": "",
                "INQR_DVSN_3": "00",
                "INQR_DVSN_1": "",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
        )
        rows = data.get("output1", []) or []
        result = []
        for r in rows:
            qty = _i(r.get("tot_ccld_qty"))
            if qty == 0:
                continue
            sell_buy = str(r.get("sll_buy_dvsn_cd", "")).strip()
            trade_type = "SELL" if sell_buy == "01" else "BUY" if sell_buy == "02" else None
            if trade_type is None:
                continue
            result.append({
                "ticker": str(r.get("pdno", "")).strip(),
                "name": str(r.get("prdt_name", "")).strip(),
                "trade_type": trade_type,
                "price": _f(r.get("avg_prvs")),    # 체결평균가
                "quantity": qty,
                "trade_date": str(r.get("ord_dt", "")).strip(),
                "order_no": str(r.get("odno", "")).strip() or None,
            })
        return result

    def _split_account(self) -> tuple[str, str]:
        """계좌번호 → (CANO 8자리, ACNT_PRDT_CD 2자리)."""
        acct = self._account_no.replace("-", "").strip()
        if len(acct) >= 10:
            return acct[:8], acct[8:10]
        return acct, "01"

    async def close(self) -> None:
        pass  # httpx는 요청마다 client 생성 → 별도 close 불필요
