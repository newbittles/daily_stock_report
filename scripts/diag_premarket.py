"""프리장(08:0x) 시세 진단 — #469 지수·Top3 시초 0% 문제 실측.

NXT 프리장 시간(08:00~08:50)에 실행해야 의미 있음:
1) KIS inquire-price J vs NX 응답 비교 (삼성전기 009150)
2) 네이버 지수 페이지 프리장 상태 (now_value / change_value_and_rate)
일회성 진단 — 검증 후 결과는 커밋 메시지/코드 주석에 반영.
"""
import asyncio
import sys

sys.path.insert(0, ".")


async def main() -> None:
    from src.config.settings import get_settings
    from src.datasource.kis.adapter import KisAdapter, _TR

    s = get_settings()
    a = KisAdapter(s.kis_app_key, s.kis_app_secret, s.kis_account_no, s.kis_env)

    for mkt in ("J", "NX"):
        try:
            d = await a._request(
                "/uapi/domestic-stock/v1/quotations/inquire-price", _TR["quote"],
                {"FID_COND_MRKT_DIV_CODE": mkt, "FID_INPUT_ISCD": "009150"},
            )
            o = d.get("output") or {}
            print(f"[{mkt}] 삼성전기 stck_prpr={o.get('stck_prpr')} prdy_ctrt={o.get('prdy_ctrt')} "
                  f"stck_sdpr={o.get('stck_sdpr')} prdy_vrss={o.get('prdy_vrss')} "
                  f"rprs_mrkt_kor_name={o.get('rprs_mrkt_kor_name')}")
        except Exception as exc:  # noqa: BLE001
            print(f"[{mkt}] FAILED: {exc}")

    # 네이버 지수 페이지 프리장 상태
    from bs4 import BeautifulSoup

    from src.market_report.scrapers.naver import BASE, fetch
    for code in ("KOSPI", "KOSDAQ"):
        html = await fetch(f"{BASE}/sise/sise_index.naver?code={code}", encoding="euc-kr")
        soup = BeautifulSoup(html, "lxml")
        nv = soup.find(id="now_value")
        ch = soup.find(id="change_value_and_rate")
        print(f"[naver {code}] now_value={nv.get_text(strip=True) if nv else None!r} "
              f"change={ch.get_text(' ', strip=True) if ch else None!r}")

    # FDR 직전 거래일 종가 확인 (전일 등락률 대체 소스 검증)
    try:
        import FinanceDataReader as fdr
        df = fdr.DataReader("KS11").tail(3)
        print("[fdr KS11]")
        print(df[["Close"]].to_string())
    except Exception as exc:  # noqa: BLE001
        print(f"[fdr] FAILED: {exc}")


asyncio.run(main())
