"""FDR ↔ yfinance 미국 심볼 정규화.

FDR S&P500 listing은 듀얼클래스 B주를 구분자 없이 준다(BRKB·BFB, 2026-06-03 실측).
yfinance는 대시 형식(BRK-B·BF-B)을 요구해 그대로 넘기면 OHLCV가 빈다 → 2종목 누락.
명시 매핑 + 닷(.)→대시(-) 일반 규칙으로 양방향 변환한다.

스크리닝 파이프라인은 전 구간을 **FDR 심볼**로 키잉하므로, yfinance 호출 경계에서만
요청 직전 to_yf_symbol(), 결과 수신 시 원래 FDR 키로 되돌린다(to_fdr_symbol()).
"""
from __future__ import annotations

# FDR(구분자 없음) → yfinance(대시). 실측 기반(S&P500: BRKB, BFB만 비표준).
# 그 외 듀얼클래스(FOXA/FOX/NWSA/NWS/GOOGL/GOOG)는 양쪽 동일 → 매핑 불필요.
_FDR_TO_YF: dict[str, str] = {
    "BRKB": "BRK-B",
    "BFB": "BF-B",
}
_YF_TO_FDR: dict[str, str] = {v: k for k, v in _FDR_TO_YF.items()}


def to_yf_symbol(sym: str) -> str:
    """FDR 심볼 → yfinance 심볼. 명시 매핑 우선, 없으면 닷→대시, 그 외 원본."""
    if sym in _FDR_TO_YF:
        return _FDR_TO_YF[sym]
    if "." in sym:  # 일부 소스의 BRK.B 형태 대비(일반 규칙)
        return sym.replace(".", "-")
    return sym


def to_fdr_symbol(sym: str) -> str:
    """yfinance 심볼 → FDR 심볼. 다운로드 결과를 파이프라인 원래 키로 되돌림."""
    return _YF_TO_FDR.get(sym, sym)
