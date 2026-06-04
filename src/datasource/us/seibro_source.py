"""서학개미(한국인) 미국주식 종목별 순매수 — 예탁결제원 SEIBro.

SEIBro "종목별내역(주식TOP50)"(BIP_CNTS10013V) 화면의 데이터 endpoint를 직접 호출한다.
브라우저/쿠키/로그인 없이 순수 HTTP POST(application/xml)로 깨끗한 XML이 온다(2026-06-05 실측).
data.go.kr 무료 API는 국가별 합계 '건수'만 줘서 종목 분리가 안 되지만(폐기), SEIBro는
**ISIN별 매수/매도/순매수 결제금액(USD)**을 TOP50까지, 기간 지정해 제공한다.

설계 의도: 미국 스크리닝(FDR/yfinance)과 별개의 '수급' 신호. 마이크론·ARM 같은
서학개미 집중 매수 종목을 조기 포착하는 배지/섹션용. domain 아님(외부 어댑터).

외부호출 안전(전역 §7 / 프로젝트 §6): 단일 일1회 호출이라 부하는 낮지만 랜덤 딜레이·
재시도3·지수백오프·HARD STOP(429/비정상 상태코드)을 적용하고, 당일 결과를 캐시한다.

endpoint 스펙(2026-06-05 Playwright로 실측 캡처):
  POST https://seibro.or.kr/websquare/engine/proworks/callServletService.jsp
  body(XML): <reqParam action="getImptFrcurStkSetlAmtList"
                       task="ksd.safe.bip.cnts.OvsSec.process.OvsSecIsinPTask">
    S_TYPE=2(결제금액 기준) · S_COUNTRY=US(미국) · D_TYPE=4(순매수결제)
    START_DT/END_DT(YYYYMMDD) · PG_START/PG_END(순위 범위)
  resp(XML): <vector result="N"><data><result> RNUM·NATION_NM·ISIN·KOR_SECN_NM(영문명)·
    SUM_FRSEC_BUY_AMT·SUM_FRSEC_SELL_AMT·SUM_FRSEC_TOT_AMT·SUM_FRSEC_NET_BUY_AMT </result>...
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_ENDPOINT = "https://seibro.or.kr/websquare/engine/proworks/callServletService.jsp"
_W2XPATH = "/IPORTAL/user/ovsSec/BIP_CNTS10013V.xml"
_REFERER = f"https://seibro.or.kr/websquare/control.jsp?w2xPath={_W2XPATH}&menuNo=921"

# S_TYPE: 결제금액=2(보관금액=1) / D_TYPE: 매수=1·매도=2·매수+매도=3·순매수=4 / S_COUNTRY: 미국=US
_BODY_TMPL = (
    '<reqParam action="getImptFrcurStkSetlAmtList" '
    'task="ksd.safe.bip.cnts.OvsSec.process.OvsSecIsinPTask">'
    '<MENU_NO value="921"/>'
    '<CMM_BTN_ABBR_NM value="total_search,openall,print,hwp,word,pdf,seach,"/>'
    f'<W2XPATH value="{_W2XPATH}"/>'
    '<PG_START value="1"/><PG_END value="{top}"/>'
    '<START_DT value="{start}"/><END_DT value="{end}"/>'
    '<S_TYPE value="2"/><S_COUNTRY value="US"/><D_TYPE value="4"/>'
    "</reqParam>"
)

_HEADERS = {
    "Content-Type": 'application/xml; charset="UTF-8"',
    "Accept": "application/xml",
    "Referer": _REFERER,
    "submissionid": "submission_getImptFrcurStkSetlAmtList",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    ),
}

_CACHE = Path(__file__).resolve().parents[3] / "data" / "seibro_netbuy_cache.json"


@dataclass(frozen=True)
class SeibroNetBuy:
    """서학개미 미국 종목별 순매수 1행 (금액 단위: USD)."""

    rank: int
    isin: str
    name_en: str       # SEIBro KOR_SECN_NM (실제로는 영문 종목명)
    buy_amt: float     # 매수 결제금액
    sell_amt: float    # 매도 결제금액
    net_buy_amt: float # 순매수 결제금액(매수-매도) — 핵심 지표


def _int(v: str | None) -> float:
    try:
        return float(int(v)) if v not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def parse_netbuy_xml(xml_bytes: bytes) -> list[SeibroNetBuy]:
    """SEIBro 응답 XML → list[SeibroNetBuy] (순위 오름차순). 순수 함수(테스트 용이)."""
    out: list[SeibroNetBuy] = []
    root = ET.fromstring(xml_bytes)
    for r in root.findall(".//result"):
        d = {c.tag: c.get("value") for c in r}
        rnum = d.get("RNUM")
        isin = d.get("ISIN")
        if not isin:  # 헤더/빈 행 가드
            continue
        out.append(SeibroNetBuy(
            rank=int(rnum) if rnum and rnum.isdigit() else 0,
            isin=isin,
            name_en=(d.get("KOR_SECN_NM") or "").strip(),
            buy_amt=_int(d.get("SUM_FRSEC_BUY_AMT")),
            sell_amt=_int(d.get("SUM_FRSEC_SELL_AMT")),
            net_buy_amt=_int(d.get("SUM_FRSEC_NET_BUY_AMT")),
        ))
    out.sort(key=lambda x: x.rank or 10**9)
    return out


def prev_trading_day(end: date | None = None) -> str:
    """가장 최근 거래일(주말 스킵) YYYYMMDD — '전일' 단일일 순매수 조회용.

    공휴일은 미반영(거래소 캘린더 없이) → 공휴일이면 빈 결과가 나오고 배지는 생략(best-effort).
    """
    d = end or (date.today() - timedelta(days=1))
    while d.weekday() >= 5:  # 토(5)·일(6)
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def lookback_range(trading_days: int = 5, end: date | None = None) -> tuple[str, str]:
    """최근 N거래일 누적 조회용 (START_DT, END_DT) YYYYMMDD.

    SEIBro는 결제(T+2~3) 기준이라 최신 영업일이 다소 지연될 수 있다 → END=어제 기본.
    거래일 정밀 계산 대신 주말 여유(×1.6+3일)로 넉넉히 잡는다(SEIBro가 구간 합산하므로 무해).
    """
    end = end or (date.today() - timedelta(days=1))
    start = end - timedelta(days=int(trading_days * 1.6) + 3)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def _fetch_sync(start_dt: str, end_dt: str, top: int) -> list[SeibroNetBuy]:
    """동기 — SEIBro POST(재시도3·지수백오프·HARD STOP). 전역 §7 / 프로젝트 §6."""
    body = _BODY_TMPL.format(start=start_dt, end=end_dt, top=top).encode("utf-8")
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            resp = requests.post(_ENDPOINT, data=body, headers=_HEADERS, timeout=15)
        except requests.Timeout as exc:
            last_err = exc
            logger.warning("seibro_timeout attempt=%d/3", attempt + 1)
        except requests.RequestException as exc:
            last_err = exc
            logger.warning("seibro_req_error attempt=%d/3 error=%s", attempt + 1, exc)
        else:
            # HARD STOP — 429/비정상 상태코드는 자동 재시도 금지(§7)
            if resp.status_code in (429, 423, 503):
                logger.warning("seibro_hard_stop status=%d — 자동 재시도 중단", resp.status_code)
                return []
            if resp.status_code != 200:
                logger.warning("seibro_bad_status status=%d", resp.status_code)
                return []
            try:
                rows = parse_netbuy_xml(resp.content)
                logger.info("seibro_ok rows=%d range=%s~%s", len(rows), start_dt, end_dt)
                return rows
            except ET.ParseError as exc:
                last_err = exc
                logger.warning("seibro_parse_error attempt=%d/3 error=%s", attempt + 1, exc)
        if attempt < 2:  # 지수 백오프 + 랜덤(고정 딜레이 금지)
            time.sleep(random.uniform(5.0 * (2 ** attempt), 10.0 * (2 ** attempt)))
    logger.warning("seibro_give_up error=%s", last_err)
    return []


def _load_cache(key: str) -> list[SeibroNetBuy] | None:
    try:
        if _CACHE.exists():
            c = json.loads(_CACHE.read_text(encoding="utf-8"))
            if c.get("date") == date.today().isoformat() and c.get("key") == key:
                return [SeibroNetBuy(**r) for r in c.get("rows", [])]
    except Exception as exc:  # noqa: BLE001
        logger.debug("seibro_cache_read_failed error=%s", exc)
    return None


def _save_cache(key: str, rows: list[SeibroNetBuy]) -> None:
    try:
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE.write_text(
            json.dumps({"date": date.today().isoformat(), "key": key,
                        "rows": [asdict(r) for r in rows]}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("seibro_cache_write_failed error=%s", exc)


async def fetch_us_net_buy(
    trading_days: int = 5, top: int = 50, use_cache: bool = True,
    *, start_dt: str | None = None, end_dt: str | None = None,
) -> list[SeibroNetBuy]:
    """서학개미 미국 종목별 순매수 TOP (순매수 내림차순).

    start_dt/end_dt(YYYYMMDD) 둘 다 주면 그 구간, 아니면 최근 N거래일 누적.
    use_cache: 같은 날·같은 구간·top 조회는 캐시(`data/seibro_netbuy_cache.json`) 재사용.
    실패/HARD STOP 시 빈 리스트(리포트는 best-effort로 섹션 생략).
    """
    if not (start_dt and end_dt):
        start_dt, end_dt = lookback_range(trading_days)
    key = f"{start_dt}_{end_dt}_{top}"
    if use_cache:
        cached = _load_cache(key)
        if cached is not None:
            return cached
    rows = await asyncio.to_thread(_fetch_sync, start_dt, end_dt, top)
    if rows:
        rows.sort(key=lambda x: x.net_buy_amt, reverse=True)
        if use_cache:
            _save_cache(key, rows)
    return rows
