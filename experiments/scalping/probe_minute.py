"""KIS 분봉 데이터 가용 범위 실측 프로브 (experiments — 메이저 코드 비침투).

목적: 분봉 백테스트가 가능한지 결정하는 단 하나의 질문에 답한다.
  → "KIS 분봉 API는 당일치만 주는가, 아니면 과거일까지 페이징되는가?"

방법: inquire-time-itemchartprice(FHKST03010200, 검증된 TR)를
  FID_INPUT_HOUR_1(기준시각)을 뒤로 밀며 반복 호출 → 돌아온 봉의
  날짜(stck_bsop_date)·시각 분포를 집계. 날짜가 1개면 '당일만',
  여러 개면 '과거일 포함'.

안전: 어댑터 _request 재사용(랜덤딜레이·재시도3·HARD STOP 내장, 전역 §7).
실행: python experiments/scalping/probe_minute.py [종목코드]
"""
from __future__ import annotations

import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.config.settings import get_settings
from src.datasource.kis.adapter import KisAdapter, _TR

ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
MAX_PAGES = 25  # 30봉×25 = 약 750봉 ≈ 당일(09:00~15:30=약 390분봉)을 충분히 덮음


async def probe(adapter: KisAdapter, ticker: str) -> None:
    print(f"\n=== 분봉 프로브: {ticker} ===")
    print(f"엔드포인트: {ENDPOINT}")
    print(f"TR_ID: {_TR['ohlcv_minute']}\n")

    seen_dates: Counter[str] = Counter()
    all_bars: list[tuple[str, str]] = []  # (date, hour)
    cursor_hour = "153000"  # 장 마감 기준에서 역방향 페이징 시작
    prev_oldest = None

    for page in range(MAX_PAGES):
        params = {
            "FID_ETC_CLS_CODE": "",
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker,
            "FID_INPUT_HOUR_1": cursor_hour,
            "FID_PW_DATA_INCU_YN": "Y",  # 과거 데이터 포함 요청
        }
        try:
            data = await adapter._request(ENDPOINT, _TR["ohlcv_minute"], params)
        except Exception as exc:  # noqa: BLE001 — 프로브는 실패 사유를 그대로 보고
            print(f"[page {page}] 요청 실패: {type(exc).__name__}: {exc}")
            break

        rows = data.get("output2", []) or []
        if not rows:
            print(f"[page {page}] hour={cursor_hour} → 빈 응답 (종료)")
            break

        page_dates = []
        page_hours = []
        for r in rows:
            d = str(r.get("stck_bsop_date") or "").strip()
            h = str(r.get("stck_cntg_hour") or "").strip()
            if d:
                seen_dates[d] += 1
                page_dates.append(d)
            if h:
                page_hours.append(h)
            all_bars.append((d, h))

        oldest_hour = min(page_hours) if page_hours else cursor_hour
        dmin, dmax = (min(page_dates), max(page_dates)) if page_dates else ("?", "?")
        print(
            f"[page {page}] hour={cursor_hour} → {len(rows)}봉 "
            f"날짜 {dmin}~{dmax} 시각 {oldest_hour}~{max(page_hours) if page_hours else '?'}"
        )

        # 더 이상 과거로 안 가면(같은 시각 반복) 종료
        if oldest_hour == prev_oldest:
            print("  └ 시각이 더 내려가지 않음 → 종료")
            break
        prev_oldest = oldest_hour
        cursor_hour = oldest_hour  # 다음 페이지: 이번 최古 시각 이전

    print("\n--- 결과 요약 ---")
    print(f"총 수집 봉: {len(all_bars)}")
    print(f"고유 날짜 수: {len(seen_dates)}")
    for d in sorted(seen_dates):
        print(f"  {d}: {seen_dates[d]}봉")

    if len(seen_dates) <= 1:
        print("\n[판정] 분봉은 '당일(최근 1영업일)'만 제공됨 → 회고 백테스트 불가.")
        print("       → 당일 분봉 누적 저장 방식 또는 사용자 제공 데이터 필요.")
    else:
        oldest, newest = min(seen_dates), max(seen_dates)
        print(f"\n[판정] 분봉이 과거일까지 제공됨: {oldest} ~ {newest} ({len(seen_dates)}영업일)")
        print("       → 이 범위만큼은 즉시 백테스트 시드로 사용 가능.")


async def main() -> None:
    ticker = sys.argv[1] if len(sys.argv) > 1 else "005930"  # 기본 삼성전자(고유동)
    if not (ticker.isdigit() and len(ticker) == 6):
        print(f"종목코드는 6자리 숫자여야 합니다: {ticker!r}")
        return
    s = get_settings()
    if not s.kis_app_key or not s.kis_app_secret:
        print("KIS_APP_KEY/KIS_APP_SECRET 미설정 — .env 확인 필요.")
        return
    adapter = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)
    print(f"KIS env={s.kis_env}")
    await probe(adapter, ticker)


if __name__ == "__main__":
    asyncio.run(main())
