"""미국 종목 한국어 표시명 — 큐레이션 매핑(ticker → 한글명).

FDR은 영문명만 제공하므로(2026-06-03 실측), 잘 알려진 종목만 한국어로 표시하고
나머지는 영문명으로 폴백한다(사용자 합의 2026-06-04: known→한국어, unknown→영문).
키는 FDR 심볼 기준(BRKB 등). 점진적으로 확충한다.

스크리닝은 거래대금 상위 대형주 위주라 아래 큐레이션으로 대부분 커버된다.
"""
from __future__ import annotations

# FDR 심볼 → 한국어 표시명. 거래대금 상위에 자주 잡히는 대형/인기주 위주.
US_NAME_KO: dict[str, str] = {
    # 빅테크 / 반도체
    "NVDA": "엔비디아", "AAPL": "애플", "MSFT": "마이크로소프트",
    "GOOGL": "알파벳(구글)", "GOOG": "알파벳(구글)", "AMZN": "아마존",
    "META": "메타", "TSLA": "테슬라", "AVGO": "브로드컴", "AMD": "AMD",
    "NFLX": "넷플릭스", "INTC": "인텔", "QCOM": "퀄컴", "MU": "마이크론",
    "TXN": "텍사스인스트루먼트", "ADI": "아날로그디바이스", "AMAT": "어플라이드머티리얼즈",
    "LRCX": "램리서치", "KLAC": "KLA", "MRVL": "마벨", "MCHP": "마이크로칩",
    "ON": "온세미", "NXPI": "NXP반도체", "MPWR": "모놀리식파워", "WDC": "웨스턴디지털",
    "STX": "시게이트", "SNDK": "샌디스크", "SMCI": "슈퍼마이크로", "ARM": "ARM홀딩스",
    "ASML": "ASML", "TSM": "TSMC",
    # 소프트웨어 / 클라우드
    "ORCL": "오라클", "CRM": "세일즈포스", "ADBE": "어도비", "CSCO": "시스코",
    "IBM": "IBM", "NOW": "서비스나우", "INTU": "인튜이트", "PANW": "팔로알토네트웍스",
    "SNPS": "시놉시스", "CDNS": "케이던스", "PLTR": "팔란티어", "CRWD": "크라우드스트라이크",
    "ZS": "지스케일러", "NET": "클라우드플레어", "DDOG": "데이터독", "SNOW": "스노우플레이크",
    "APP": "애플로빈", "SHOP": "쇼피파이", "DELL": "델", "HPQ": "HP", "MSTR": "마이크로스트래티지",
    # 인터넷 / 소비재
    "UBER": "우버", "ABNB": "에어비앤비", "BKNG": "부킹홀딩스", "SBUX": "스타벅스",
    "MCD": "맥도날드", "NKE": "나이키", "KO": "코카콜라", "PEP": "펩시코",
    "PG": "P&G", "COST": "코스트코", "WMT": "월마트", "HD": "홈디포",
    "LOW": "로우스", "DIS": "디즈니", "CMCSA": "컴캐스트", "TMUS": "T모바일",
    "ROKU": "로쿠", "RBLX": "로블록스", "DKNG": "드래프트킹스",
    # 금융
    "JPM": "JP모건", "BAC": "뱅크오브아메리카", "WFC": "웰스파고", "GS": "골드만삭스",
    "MS": "모건스탠리", "C": "씨티그룹", "BRKB": "버크셔해서웨이", "V": "비자",
    "MA": "마스터카드", "AXP": "아메리칸익스프레스", "PYPL": "페이팔", "HOOD": "로빈후드",
    "COIN": "코인베이스", "SCHW": "찰스슈왑", "BLK": "블랙록",
    # 헬스케어
    "LLY": "일라이릴리", "UNH": "유나이티드헬스", "JNJ": "존슨앤존슨", "ABBV": "애브비",
    "MRK": "머크", "PFE": "화이자", "TMO": "써모피셔", "ABT": "애보트", "AMGN": "암젠",
    # 산업재 / 에너지 / 자동차
    "F": "포드", "GM": "제너럴모터스", "BA": "보잉", "CAT": "캐터필러",
    "GE": "GE에어로스페이스", "XOM": "엑슨모빌", "CVX": "셰브론",
    "RIVN": "리비안", "LCID": "루시드", "GLW": "코닝",
    # 주요 ETF·종목 한국어명 (서학개미 자금흐름 표시용, 사용자 #394)
    "SOXX": "반도체 ETF(SOXX)", "SMH": "반도체 ETF(SMH)", "SOXL": "반도체 3X(SOXL)",
    "SOXS": "반도체 인버스 3X(SOXS)", "QQQ": "나스닥100 ETF(QQQ)", "QQQM": "나스닥100 ETF(QQQM)",
    "QLD": "나스닥100 2X(QLD)", "VOO": "S&P500 ETF(VOO)", "SCHD": "미국배당 ETF(SCHD)",
    "JEPQ": "나스닥 커버드콜(JEPQ)", "TQQQ": "나스닥100 3X(TQQQ)",
    "MDB": "몽고DB", "HPE": "HPE", "WOLF": "울프스피드", "NXT": "넥스트래커",
    "NASA": "우주항공 ETF(NASA)", "DRAM": "메모리/DRAM ETF(DRAM)",
}


def korean_name(symbol: str, fallback: str = "") -> str:
    """FDR 심볼 → 한국어 표시명. 큐레이션 → 네이버 캐시(DB) → 영문명(폴백) 순."""
    if symbol in US_NAME_KO:
        return US_NAME_KO[symbol]
    from src.datasource.us.names_db import cached_name
    return cached_name(symbol) or fallback or symbol


# GICS Industry(세부 업종) → 한국어 테마. FDR Sector는 죄다 'Information Technology'라
# 거칠어서(2026-06-04 사용자), Industry를 한국어 테마로 매핑해 세분화한다. 미매핑은 영문 폴백.
US_INDUSTRY_KO: dict[str, str] = {
    # IT / 반도체 / 소프트웨어
    "Semiconductors": "반도체",
    "Semiconductor Materials & Equipment": "반도체장비",
    "Application Software": "소프트웨어",
    "Systems Software": "시스템소프트웨어",
    "Internet Services & Infrastructure": "클라우드/인프라",
    "Interactive Media & Services": "인터넷/플랫폼",
    "Interactive Home Entertainment": "게임",
    "Communications Equipment": "통신장비",
    "Technology Hardware, Storage & Peripherals": "IT하드웨어",
    "Electronic Equipment & Instruments": "전자장비",
    "Electronic Components": "전자부품",
    "Electronic Manufacturing Services": "EMS(전자제조)",
    "IT Consulting & Other Services": "IT서비스",
    "Data Processing & Outsourced Services": "데이터/아웃소싱",
    "Transaction & Payment Processing Services": "결제",
    "Technology Distributors": "IT유통",
    "Consumer Electronics": "가전",
    "Financial Exchanges & Data": "거래소/금융데이터",
    # 통신/미디어
    "Integrated Telecommunication Services": "통신",
    "Wireless Telecommunication Services": "무선통신",
    "Cable & Satellite": "케이블/위성",
    "Movies & Entertainment": "미디어/엔터",
    "Broadcasting": "방송",
    "Advertising": "광고",
    "Publishing": "출판",
    # 헬스케어
    "Pharmaceuticals": "제약",
    "Biotechnology": "바이오",
    "Health Care Equipment": "의료기기",
    "Health Care Supplies": "의료용품",
    "Health Care Services": "헬스케어서비스",
    "Health Care Technology": "헬스케어테크",
    "Health Care Distributors": "의약품유통",
    "Health Care Facilities": "병원/시설",
    "Managed Health Care": "건강보험",
    "Life Sciences Tools & Services": "생명과학도구",
    # 금융
    "Diversified Banks": "은행",
    "Regional Banks": "지방은행",
    "Investment Banking & Brokerage": "투자은행/증권",
    "Asset Management & Custody Banks": "자산운용",
    "Consumer Finance": "소비자금융",
    "Property & Casualty Insurance": "손해보험",
    "Life & Health Insurance": "생명/건강보험",
    "Multi-line Insurance": "종합보험",
    "Insurance Brokers": "보험중개",
    "Reinsurance": "재보험",
    # 소비재
    "Automobile Manufacturers": "자동차",
    "Automotive Parts & Equipment": "자동차부품",
    "Automotive Retail": "자동차유통",
    "Restaurants": "외식",
    "Hotels, Resorts & Cruise Lines": "호텔/레저",
    "Casinos & Gaming": "카지노/게이밍",
    "Apparel, Accessories & Luxury Goods": "의류/명품",
    "Apparel Retail": "의류유통",
    "Footwear": "신발",
    "Broadline Retail": "종합소매",
    "Home Improvement Retail": "홈데코/건자재유통",
    "Consumer Staples Merchandise Retail": "필수소비재유통",
    "Packaged Foods & Meats": "식품",
    "Soft Drinks & Non-alcoholic Beverages": "음료",
    "Brewers": "주류",
    "Distillers & Vintners": "주류",
    "Tobacco": "담배",
    "Household Products": "생활용품",
    "Personal Care Products": "퍼스널케어",
    "Leisure Products": "레저용품",
    # 산업재
    "Aerospace & Defense": "항공우주/방산",
    "Industrial Machinery & Supplies & Components": "산업기계",
    "Construction Machinery & Heavy Transportation Equipment": "건설기계",
    "Construction & Engineering": "건설/엔지니어링",
    "Electrical Components & Equipment": "전기장비",
    "Heavy Electrical Equipment": "중전기",
    "Building Products": "건자재",
    "Industrial Conglomerates": "복합산업",
    "Passenger Airlines": "항공",
    "Rail Transportation": "철도",
    "Air Freight & Logistics": "항공물류",
    "Trading Companies & Distributors": "상사/유통",
    "Research & Consulting Services": "컨설팅",
    "Human Resource & Employment Services": "인력서비스",
    # 에너지/소재/유틸리티
    "Integrated Oil & Gas": "석유/가스",
    "Oil & Gas Exploration & Production": "석유개발(E&P)",
    "Oil & Gas Equipment & Services": "유전서비스",
    "Oil & Gas Refining & Marketing": "정유",
    "Oil & Gas Storage & Transportation": "에너지인프라",
    "Electric Utilities": "전력",
    "Multi-Utilities": "종합유틸리티",
    "Independent Power Producers & Energy Traders": "발전/전력거래",
    "Specialty Chemicals": "특수화학",
    "Commodity Chemicals": "화학",
    "Industrial Gases": "산업가스",
    "Fertilizers & Agricultural Chemicals": "비료/농화학",
    "Steel": "철강",
    "Copper": "구리",
    "Gold": "금",
    "Construction Materials": "건설소재",
}


def us_theme(sector: str = "", industry: str = "") -> str:
    """미국 종목 표시 테마 — GICS Industry를 한국어로 세분 매핑.

    매핑 있으면 한국어 테마, 없으면 영문 Industry(섹터보다 세분), 그것도 없으면 sector.
    큐레이션 watchlist는 sector/industry가 이미 한국어 테마라 그대로 통과.
    """
    if industry:
        if industry in US_INDUSTRY_KO:
            return US_INDUSTRY_KO[industry]
        return industry  # 미매핑 GICS(영문)도 'Information Technology'보다는 세분
    return sector or ""
