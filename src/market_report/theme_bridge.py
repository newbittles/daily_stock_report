"""미국 강세 섹터 → 한국 테마 브릿지 (us_morning 시초 Top3용).

미국장 강세 섹터(us_sectors, +1.0%↑ 필터 — Q1)를 한국 테마 키워드로 매핑한다.
한국 종목의 테마가 이 키워드를 포함하면 '미국 모멘텀 연동' 가중(us_boost)을 받는다.
하이브리드(U3): 룰 매핑표(결정론) + A/B/C/D 시그널 필터 + AI 코멘트(별도).

design: docs/02-design/features/us-morning-report.design.md §5
"""
from __future__ import annotations

# 미국 섹터 ETF 표시명(fdr_source.US_SECTORS) → 한국 테마 키워드들
US_TO_KR_THEME: dict[str, list[str]] = {
    "반도체": ["반도체", "HBM", "반도체장비", "파운드리", "메모리", "시스템반도체"],
    "기술/IT": ["AI", "인공지능", "소프트웨어", "인터넷", "데이터센터", "클라우드", "IT"],
    "에너지": ["정유", "에너지", "가스", "석유"],
    "금융": ["은행", "증권", "금융", "보험"],
    "헬스케어/바이오": ["제약", "바이오", "헬스케어", "의료"],
    "경기소비재": ["유통", "소비재", "화장품", "의류"],
    "방산/우주항공": ["방산", "우주항공", "항공"],
    "태양광/신재생": ["태양광", "신재생", "풍력", "수소"],
    "2차전지/리튬": ["2차전지", "전기차", "리튬", "배터리"],
}


def strong_kr_keywords(us_sectors: list[dict], threshold: float = 1.0) -> set[str]:
    """미국 강세 섹터(>=threshold%) → 매핑된 한국 테마 키워드 집합 (Q1: 기본 +1.0%)."""
    kws: set[str] = set()
    for q in us_sectors:
        if q.get("change_pct", 0) >= threshold:
            kws.update(US_TO_KR_THEME.get(q.get("name", ""), []))
    return kws


def us_theme_match(kr_theme: str, us_keywords: set[str]) -> bool:
    """한국 종목 테마가 미국 강세 키워드를 포함하는가."""
    if not kr_theme or not us_keywords:
        return False
    return any(kw in kr_theme for kw in us_keywords)


def matched_us_sector(kr_theme: str, us_sectors: list[dict], threshold: float = 1.0) -> str:
    """이 종목 테마와 연결된 미국 강세 섹터명 (추천이유 표기용). 없으면 ''."""
    for q in us_sectors:
        if q.get("change_pct", 0) < threshold:
            continue
        if any(kw in (kr_theme or "") for kw in US_TO_KR_THEME.get(q.get("name", ""), [])):
            return q.get("name", "")
    return ""
