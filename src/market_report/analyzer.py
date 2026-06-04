"""Gemini 시장 분석기 — 마감 전/마감 후 모드별 프롬프트.

마감 전 (pre_close, 14:50):
  - 종가베팅 후보 5개 선정
  - 각 후보: 거래량 쏠림·테마 강세·뉴스 근거 종합
  - 면책 문구 필수

마감 후 (post_close, 16:30):
  - "왜 올랐나/내렸나" 한 문단
  - 강세 테마 Top 3 해설
  - 내일 관전 포인트 (있으면)

호출 한도: settings.ai_daily_call_limit (기본 100). 초과 시 빈 결과 반환.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from google import genai
from google.genai import types

from src.config.settings import get_settings
from src.market_report.models import MarketSnapshot, ReportMode

logger = logging.getLogger(__name__)

MODEL_NAME = "gemini-2.5-flash-lite"  # 빠르고 저렴 + 무료 일일 한도 여유 (flash 대비)

DISCLAIMER = (
    "※ 본 리포트는 공개 데이터 기반 참고용 정보입니다. "
    "투자 판단·매매 결정·결과 책임은 전적으로 본인에게 있습니다."
)


def _build_snapshot_context(snap: MarketSnapshot) -> str:
    """스냅샷을 Gemini 프롬프트용 한국어 텍스트로 직렬화."""
    lines: list[str] = []

    if snap.kospi:
        sign = "+" if snap.kospi.change_pct >= 0 else ""
        lines.append(
            f"KOSPI: {snap.kospi.value:,.2f} ({sign}{snap.kospi.change_pct:.2f}%)"
        )
    if snap.kosdaq:
        sign = "+" if snap.kosdaq.change_pct >= 0 else ""
        lines.append(
            f"KOSDAQ: {snap.kosdaq.value:,.2f} ({sign}{snap.kosdaq.change_pct:.2f}%)"
        )

    if snap.top_volume:
        lines.append("\n[거래량 상위 15]")
        for s in snap.top_volume[:15]:
            sign = "+" if s.change_pct >= 0 else ""
            lines.append(
                f"  {s.rank}. {s.name}({s.ticker}) "
                f"{s.price:,.0f}원 {sign}{s.change_pct:.2f}% "
                f"거래량 {s.volume:,}"
            )

    if snap.top_gainers:
        lines.append("\n[상승률 상위 10]")
        for s in snap.top_gainers[:10]:
            lines.append(
                f"  {s.rank}. {s.name}({s.ticker}) {s.price:,.0f}원 +{s.change_pct:.2f}%"
            )

    if snap.top_losers:
        lines.append("\n[하락률 상위 10]")
        for s in snap.top_losers[:10]:
            lines.append(
                f"  {s.rank}. {s.name}({s.ticker}) {s.price:,.0f}원 {s.change_pct:.2f}%"
            )

    if snap.top_themes:
        lines.append("\n[강세/약세 테마 Top 10]")
        for t in snap.top_themes[:10]:
            sign = "+" if t.change_pct >= 0 else ""
            leads = ", ".join(t.leading_stocks[:3]) if t.leading_stocks else "-"
            lines.append(f"  {t.rank}. {t.name} {sign}{t.change_pct:.2f}% [주도주: {leads}]")

    if snap.market_news:
        lines.append("\n[주요 시장 뉴스 헤드라인]")
        for i, n in enumerate(snap.market_news[:15], 1):
            src = f" [{n.source}]" if n.source else ""
            lines.append(f"  {i}. {n.title}{src}")

    return "\n".join(lines)


def _build_us_context(snap: MarketSnapshot) -> str:
    """미국 증시 스냅샷 → 프롬프트용 텍스트 (us_morning)."""
    lines: list[str] = []
    if snap.us_indices:
        lines.append("[미국 지수]")
        for q in snap.us_indices:
            lines.append(f"  {q['name']}: {q['price']:,.2f} ({q['change_pct']:+.2f}%)")
    if snap.us_bigtech:
        lines.append("\n[빅테크/주요 종목]")
        for q in snap.us_bigtech:
            lines.append(f"  {q['name']} {q['change_pct']:+.2f}%")
    if snap.us_sectors:
        lines.append("\n[섹터 ETF 등락]")
        for q in snap.us_sectors:
            lines.append(f"  {q['name']} {q['change_pct']:+.2f}%")
    if getattr(snap, "us_news", None):
        lines.append("\n[미국 시장 뉴스 헤드라인]")
        for n in snap.us_news[:10]:
            src = f" ({n['source']})" if n.get("source") else ""
            lines.append(f"  - {n['title']}{src}")
    return "\n".join(lines)


def _us_morning_prompt(snap: MarketSnapshot, context: str) -> str:
    return f"""당신은 미국 증시 전문 애널리스트입니다. 미국장 마감 직후, 한국 투자자를 위한
아침 요약을 작성합니다. 아래 데이터로 다음을 **반드시 JSON 형식**으로 출력하세요:

{{
  "summary": "미국장 1-2문장 요약 (지수 등락 + 핵심 키워드)",
  "why_moved": "미국장이 왜 이렇게 움직였나 2-3문장 (강세 섹터·빅테크·매크로 근거)",
  "theme_commentary": "오늘 강세 섹터/테마 해설 + 한국장 시사점 2-3문장 (예: 미국 반도체 강세 → 한국 반도체 주목)"
}}

수치는 아래 주어진 값만 사용하고 지어내지 마세요. 뉴스는 모르면 언급하지 마세요.

데이터:
{context}

JSON만 출력하고 다른 설명은 추가하지 마세요."""


def _us_premarket_prompt(snap: MarketSnapshot, context: str) -> str:
    return f"""당신은 미국 증시 전문 애널리스트입니다. 지금은 미국 정규장 개장 전 '프리장(pre-market)' 시간입니다.
아래 데이터에서 **종목·섹터 등락률은 프리장 기준**, 지수는 직전 정규장 마감 기준입니다.
한국 투자자를 위해 현재 프리장 상황을 다음 **JSON 형식**으로 해설하세요:

{{
  "summary": "지금 프리장 분위기 1-2문장 (강세/약세 + 핵심 키워드)",
  "why_moved": "직전 정규장 마감 후 주요 뉴스·이슈가 무엇이었고 그 결과 프리장에서 어떤 섹터/종목이 상승·하락 중인지 3-4문장",
  "theme_commentary": "프리장 강세/약세 섹터 해설 + 한국장 시사점 2-3문장"
}}

수치는 아래 값만 사용하고 지어내지 마세요. 뉴스가 불확실하면 일반적 맥락으로만 설명하세요.

데이터:
{context}

JSON만 출력하고 다른 설명은 추가하지 마세요."""


def _pre_close_prompt(snap: MarketSnapshot, context: str) -> str:
    theme_names = [t.name for t in snap.top_themes[:10]]
    return f"""당신은 한국 주식 시장 전문 애널리스트입니다. 지금 시각은 장 마감 40분 전 (14:50).
사용자는 종가베팅 전략을 사용합니다: 마감 직전 거래량이 쏠리거나, 과매도 후 반등 신호가 보이는 종목을 매수.

아래 14:50 시점 데이터를 보고 다음을 출력하세요. **반드시 JSON 형식**으로:

{{
  "summary": "오늘 장 분위기 1-2문장 요약 (예: '코스피 약세 속 2차전지 강세 지속')",
  "why_moved": "왜 이런 흐름인지 2-3문장 설명 (수급·테마·이벤트 근거)",
  "theme_commentary": "강세/약세 테마 전체에 대한 짧은 종합 해설 (2-3문장)",
  "theme_reasons": {{
    "테마명1": "이 테마가 왜 오늘 강한지/약한지 — 뉴스·매크로 이슈·실적·정책 근거 (1-2문장, 구체적이고 사실 기반)",
    "테마명2": "...",
    "...": "..."
  }},
  "candidate_picks": [
    {{
      "ticker": "6자리 종목코드",
      "name": "종목명",
      "theme": "이 종목이 속한 테마명 (위 강세 테마 Top 10 중 하나)",
      "theme_peers": [
        {{"name": "동반 상승 중인 같은 테마 종목명", "change_pct": 등락률_숫자}}
        // 같은 테마의 동반 종목 2~4개 (위 데이터의 상승률 상위에서 찾기, 등락률 정확히)
      ],
      "rationale": "왜 종가베팅 후보인지 — 거래량/등락/테마/뉴스 근거 (2-3문장)",
      "risk": "주의할 위험 요인 (1문장)"
    }}
    // 정확히 5개
  ]
}}

theme_reasons 작성 규칙:
- 위 데이터의 강세/약세 테마 Top 10 중 **상위 5~7개** 테마에 대해 작성
- 키는 정확히 다음 중에서 선택 (오탈자 금지): {theme_names}
- 시장 뉴스에서 단서를 적극 활용 (예: "AI 반도체 사이클 기대감", "전기차 보조금 정책")
- 추측보다는 데이터 기반 추론

선정 기준 (candidate_picks):
- 거래량 상위 또는 상승률 상위에 있으면서 강세 테마에 속한 종목
- 또는 과매도(-3%~-10%)인데 거래량 급증 + 강세 테마 → 반등 후보
- ETF·인버스·레버리지는 제외 (실제 종목만)
- 5개 모두 서로 다른 테마/특성에서 선정 (다양성)
- ticker는 반드시 데이터에 등장한 6자리 코드만 사용 (창작 금지)
- theme는 정확히 다음 중에서 선택: {theme_names}
- theme_peers의 등락률은 데이터에 명시된 값만 사용 (창작 금지, 없으면 빈 배열)

데이터:
{context}

JSON만 출력하고 다른 설명은 추가하지 마세요."""


def _post_close_prompt(snap: MarketSnapshot, context: str) -> str:
    theme_names = [t.name for t in snap.top_themes[:10]]
    return f"""당신은 한국 주식 시장 전문 애널리스트입니다. 지금은 장 마감 후 (16:30).
오늘 시장을 정리하고 내일 관전 포인트를 제시하세요. **반드시 JSON 형식**으로:

{{
  "summary": "오늘 장 1-2문장 요약 (지수 등락 + 핵심 키워드)",
  "why_moved": "오늘 시장이 이렇게 움직인 이유 (3-4문장, 수급/테마/이벤트 근거)",
  "theme_commentary": "오늘 시장 흐름 전반 해설 (2-3문장)",
  "theme_reasons": {{
    "테마명1": "왜 오늘 이 테마가 강했나/약했나 — 뉴스·매크로·정책 근거 (1-2문장)",
    "테마명2": "...",
    "...": "..."
  }},
  "tomorrow_watchpoints": [
    "내일 주목할 포인트 1 (1문장)",
    "내일 주목할 포인트 2 (1문장)",
    "내일 주목할 포인트 3 (1문장)"
  ]
}}

theme_reasons 작성 규칙:
- 위 데이터의 강세/약세 테마 Top 10 중 **상위 5~7개** 테마에 대해 작성
- 키는 정확히 다음 중에서 선택 (오탈자 금지): {theme_names}
- 시장 뉴스에서 단서를 적극 활용

데이터:
{context}

JSON만 출력하고 다른 설명은 추가하지 마세요."""


def _fallback_summary(snap: MarketSnapshot) -> str:
    """AI 실패 시 스냅샷 수치로 만드는 결정론적 요약 (요약이 빈 채로 발송되지 않도록).

    간헐적 Gemini 실패(쿼터·503·휴장일 등)에도 리포트가 최소한의 시장 요약을
    담도록 보장. 'AI 분석 불가' 단순 메시지 대신 실제 지수·테마 수치를 제공한다.
    """
    parts: list[str] = []
    if snap.mode in ("us_morning", "us_premarket"):
        for q in (snap.us_indices or [])[:2]:
            parts.append(f"{q.get('name', '')} {q.get('price', 0):,.0f}({q.get('change_pct', 0):+.2f}%)")
        secs = [q.get("name", "") for q in (snap.us_sectors or [])[:3] if q.get("name")]
        head = " · ".join(parts) if parts else "미국 증시"
        return head + (f". 강세 섹터: {', '.join(secs)}." if secs else ".")
    for idx, label in ((snap.kospi, "코스피"), (snap.kosdaq, "코스닥")):
        if idx:
            parts.append(f"{label} {idx.value:,.1f}({idx.change_pct:+.2f}%)")
    themes = snap.leading_themes[:3] or [t.name for t in (snap.top_themes or [])[:3]]
    head = " · ".join(parts) if parts else "국내 증시"
    return head + (f". 강세 테마: {', '.join(themes)}." if themes else ".")


async def summarize_midday(snap: MarketSnapshot) -> str:
    """장중(정오) 한줄 코멘트 — 지수·수급·강세테마 기반 1~2문장.

    Gemini 1회 시도, 실패/키없음 시 결정론 폴백(_fallback_summary). 종목 추천·매수의견 금지.
    """
    settings = get_settings()
    fallback = _fallback_summary(snap)
    if not settings.gemini_api_key:
        return fallback

    context = _build_snapshot_context(snap)
    prompt = (
        "다음은 오늘 한국 증시 '장중(정오)' 스냅샷이다. 지금까지의 흐름을 "
        "1~2문장으로 간결히 코멘트하라(지수 방향·수급·강세테마 중심). "
        "종목 추천·매수의견·목표가는 절대 쓰지 말 것. 사실을 지어내지 말 것.\n\n"
        f"{context}"
    )
    try:
        client = genai.Client(api_key=settings.gemini_api_key)
        resp = client.models.generate_content(
            model=MODEL_NAME, contents=prompt,
            config=types.GenerateContentConfig(temperature=0.3),
        )
        text = (resp.text or "").strip()
        return text or fallback
    except Exception as exc:  # noqa: BLE001
        logger.warning("midday_summary_failed error=%s", exc)
        return fallback


async def analyze(snap: MarketSnapshot) -> MarketSnapshot:
    """Gemini로 시장 분석. snap을 mutate해서 반환.

    실패 시 빈 요약·후보로 채워서 반환 (리포트 자체는 발송 가능하도록).
    """
    settings = get_settings()

    if snap.mode == "us_premarket":
        context = _build_us_context(snap)
        prompt = _us_premarket_prompt(snap, context)
    elif snap.mode == "us_morning":
        context = _build_us_context(snap)
        prompt = _us_morning_prompt(snap, context)
    elif snap.mode == "pre_close":
        context = _build_snapshot_context(snap)
        prompt = _pre_close_prompt(snap, context)
    else:
        context = _build_snapshot_context(snap)
        prompt = _post_close_prompt(snap, context)

    import asyncio
    import random

    client = genai.Client(api_key=settings.gemini_api_key)
    data: dict[str, Any] | None = None
    last_exc: Exception | None = None

    # 503/일시 오류 대비 3회 재시도 (지수 백오프)
    for attempt in range(3):
        try:
            if attempt > 0:
                wait = random.uniform(2 * (2 ** (attempt - 1)), 5 * (2 ** (attempt - 1)))
                logger.info("gemini_retry mode=%s attempt=%d wait=%.1fs", snap.mode, attempt, wait)
                await asyncio.sleep(wait)

            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.3,
                ),
            )
            raw = response.text or "{}"
            data = json.loads(raw)
            break
        except Exception as exc:
            last_exc = exc
            logger.warning("gemini_attempt_failed mode=%s attempt=%d error=%s", snap.mode, attempt, exc)

    if data is None:
        logger.error("gemini_analyze_failed mode=%s error=%s", snap.mode, last_exc)
        # 결정론적 폴백 — 'AI 분석 불가' 단순 메시지 대신 지수·테마 수치 요약 제공
        snap.summary = _fallback_summary(snap)
        snap.why_moved = ""
        snap.theme_commentary = ""
        snap.candidate_picks = []
        return snap

    def _to_text(value: Any) -> str:
        """str/dict/list 모두 안전하게 텍스트화."""
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            return "\n\n".join(_to_text(v) for v in value if v)
        if isinstance(value, dict):
            # 예: {"MLCC": "...", "2차전지": "..."} → "MLCC: ...\n\n2차전지: ..."
            return "\n\n".join(f"▸ {k}: {_to_text(v)}" for k, v in value.items())
        return str(value).strip()

    snap.summary = _to_text(data.get("summary"))
    snap.why_moved = _to_text(data.get("why_moved"))
    snap.theme_commentary = _to_text(data.get("theme_commentary"))

    # 테마별 근거 매핑 → ThemeRank.reason 채움
    theme_reasons_raw = data.get("theme_reasons", {})
    if isinstance(theme_reasons_raw, dict):
        # 키 정규화 (공백·괄호 변형 대응)
        norm = {k.strip().lower(): v for k, v in theme_reasons_raw.items()}
        for t in snap.top_themes:
            key = t.name.strip().lower()
            reason = norm.get(key)
            if not reason:
                # 부분 매칭 (테마명에 괄호가 있는 경우 대비)
                for k, v in norm.items():
                    if k in key or key in k:
                        reason = v
                        break
            t.reason = _to_text(reason) if reason else ""

    if snap.mode == "pre_close":
        picks_raw = data.get("candidate_picks", [])
        valid_picks = []
        for p in picks_raw:
            if not isinstance(p, dict):
                continue
            if not all(k in p for k in ("ticker", "name", "rationale")):
                continue

            # theme_peers 정규화: [{"name", "change_pct"}, ...]
            peers_raw = p.get("theme_peers", [])
            peers = []
            if isinstance(peers_raw, list):
                for peer in peers_raw[:5]:
                    if not isinstance(peer, dict):
                        continue
                    pname = str(peer.get("name", "")).strip()
                    try:
                        pct = float(peer.get("change_pct", 0))
                    except (TypeError, ValueError):
                        pct = 0.0
                    if pname:
                        peers.append({"name": pname, "change_pct": pct})

            valid_picks.append({
                "ticker": str(p["ticker"]).strip(),
                "name": str(p["name"]).strip(),
                "theme": str(p.get("theme", "")).strip(),
                "theme_peers": peers,
                "rationale": str(p["rationale"]).strip(),
                "risk": str(p.get("risk", "")).strip(),
            })
        snap.candidate_picks = valid_picks
    elif snap.mode == "post_close":
        # 마감 후: tomorrow_watchpoints를 candidate_picks 자리에 보관 (재사용)
        snap.candidate_picks = [
            {"watchpoint": w} for w in data.get("tomorrow_watchpoints", [])
            if isinstance(w, str)
        ]
    # us_morning: summary/why_moved/theme_commentary만 사용 (candidate 없음)

    logger.info(
        "gemini_analyze_ok mode=%s picks=%d summary_len=%d",
        snap.mode, len(snap.candidate_picks), len(snap.summary)
    )
    return snap


def _move_label(change_pct: float, cross_signal: str | None = None) -> str:
    """등락률(+크로스신호) → 방향 라벨. AI가 상승/하락 사유를 맞게 쓰도록 힌트.

    CORRECTION(조정시작=상승 후 하락 전환)은 오늘 등락 부호와 무관히 '조정'으로 본다
    ('잘 오르다 폭락'한 종목의 하락 사유 설명을 유도).
    """
    if cross_signal == "CORRECTION":
        return "▼조정(상승 후 하락 전환)"
    if change_pct < 0:
        return "▼하락"
    if change_pct > 0:
        return "▲상승"
    return "보합"


def _summary_target_line(tk: str, p: dict) -> str:
    """AI 종목요약 프롬프트용 한 줄(종목·등락·방향·테마·주도주). 순수 함수."""
    chg = float(p.get("change_pct", 0) or 0)
    lead = "(테마 주도주)" if p.get("is_theme_leader") else ""
    move = _move_label(chg, p.get("cross_signal"))
    return (f"- {tk} {p.get('name', '')} | 오늘 {chg:+.1f}% {move} "
            f"| 테마 {p.get('theme', '-') or '-'}{lead}")


async def summarize_stocks(snap: MarketSnapshot) -> None:
    """Top3 + 전략스크린 종목별 AI 요약을 1회 배치 호출로 사전 생성.

    정적 리포트(GitHub Pages)에 임베드할 종목별 요약을 미리 만들어 각 dict에
    'ai_summary'를 추가한다. 클릭 시 실시간 호출(키 노출) 대신 사전 생성 방식.
    종목 수만큼 호출하지 않고 한 프롬프트에 모아 1회만 호출(한도·시간 절약).
    실패/키 없음/한도 시 빈 문자열 → 프론트에서 버튼 미표시.
    """
    import asyncio
    import random

    settings = get_settings()
    if not settings.gemini_api_key:
        return

    # 대상 종목 수집 (top3 ∪ screen_picks, ticker 중복 1회)
    targets: dict[str, dict] = {}
    for src_list in ((snap.top3 or []), snap.screen_picks or []):
        for p in src_list:
            tk = str(p.get("ticker", "")).strip()
            if tk and tk not in targets:
                targets[tk] = p
    if not targets:
        return

    # 컨텍스트: 강세 테마(주도주) + 시장 뉴스 → "왜 올랐나/내렸나" 추론 근거
    theme_blob = "\n".join(
        f"- {t.name} {t.change_pct:+.1f}% [주도주 {', '.join(t.leading_stocks[:3]) or '-'}]"
        for t in (snap.top_themes or [])[:10])
    news_blob = "\n".join(f"- {n.title}" for n in (snap.market_news or [])[:15])

    blob = "\n".join(_summary_target_line(tk, p) for tk, p in targets.items())

    prompt = (
        "다음은 오늘 한국 증시에서 시그널이 포착된 종목들이다. 각 종목이 "
        "**오늘 왜 그렇게 움직였는지** 를 1~2문장으로 간단히 요약하라.\n"
        "- ▲상승 종목: 왜 올랐는지(강세 이유)를 설명.\n"
        "- ▼하락·조정 종목: 왜 하락(또는 조정)했는지 설명. 특히 '상승 후 하락 전환'이면 "
        "차익실현·과열 부담·고점 매도 등 하락 사유를 짚어라.\n"
        "근거는 ①주요 뉴스 ②소속 테마 ③주도주 여부 중심으로. "
        "진입가·손절가·매수추천은 절대 언급하지 말 것(그건 따로 표시됨). "
        "뉴스에 근거가 없으면 테마·수급 맥락으로 설명하되, 사실을 지어내지 말 것.\n\n"
        f"[오늘 강세 테마]\n{theme_blob}\n\n"
        f"[시장 뉴스 헤드라인]\n{news_blob}\n\n"
        f"[대상 종목]\n{blob}\n\n"
        '반드시 JSON으로만 답하라: {"종목코드": "왜 올랐는지(또는 왜 하락했는지) 1~2문장", ...}'
    )

    client = genai.Client(api_key=settings.gemini_api_key)
    data: dict[str, Any] | None = None
    for attempt in range(3):
        try:
            if attempt > 0:
                await asyncio.sleep(random.uniform(2 * (2 ** (attempt - 1)), 5 * (2 ** (attempt - 1))))
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", temperature=0.3),
            )
            data = json.loads(response.text or "{}")
            break
        except Exception as exc:  # noqa: BLE001
            logger.warning("stock_summary_attempt_failed attempt=%d error=%s", attempt, exc)

    if not isinstance(data, dict):
        logger.error("stock_summary_failed — 종목 요약 생성 실패")
        return

    def _put(p: dict) -> None:
        tk = str(p.get("ticker", "")).strip()
        s = data.get(tk, "")
        if isinstance(s, (dict, list)):
            s = json.dumps(s, ensure_ascii=False)
        p["ai_summary"] = str(s or "").strip()

    for p in (snap.top3 or []):
        _put(p)
    for p in (snap.screen_picks or []):
        _put(p)
    logger.info("stock_summary_ok n=%d", len(targets))


async def summarize_themes(snap: MarketSnapshot) -> None:
    """강세 테마별 '왜 올랐나' 1~2줄 요약 → 각 ThemeRank.description.

    뉴스·정책/정치 이슈·기대감·대장주 움직임을 근거로(예: 보험 → 정책 기대감).
    1회 배치 호출. 키 없음/실패 시 description 미설정(렌더 측에서 생략).
    """
    import asyncio
    import random

    settings = get_settings()
    if not settings.gemini_api_key or not snap.top_themes:
        return

    targets = snap.top_themes[:6]
    theme_blob = "\n".join(
        f"- {t.name} {t.change_pct:+.1f}% [주도주 {', '.join(t.leading_stocks[:3]) or '-'}]"
        for t in targets)
    news_blob = "\n".join(f"- {n.title}" for n in (snap.market_news or [])[:15])

    prompt = (
        "다음은 오늘 한국 증시의 강세 테마와 시장 뉴스다. 각 테마가 **오늘 왜 강세인지** "
        "1~2문장으로 요약하라. 근거는 ①관련 뉴스 ②정책·정치 이슈나 기대감(예: 보험 → 정책 "
        "기대감) ③대장주(주도주) 움직임 중심으로. 뉴스에 근거가 없으면 수급·순환매 맥락으로 "
        "설명하되 사실을 지어내지 말 것. 매수추천·목표가는 쓰지 말 것.\n\n"
        f"[오늘 강세 테마]\n{theme_blob}\n\n"
        f"[시장 뉴스 헤드라인]\n{news_blob}\n\n"
        '반드시 JSON으로만 답하라: {"테마명": "왜 강세인지 1~2문장", ...}'
    )

    client = genai.Client(api_key=settings.gemini_api_key)
    data: dict[str, Any] | None = None
    for attempt in range(3):
        try:
            if attempt > 0:
                await asyncio.sleep(random.uniform(2 * (2 ** (attempt - 1)), 5 * (2 ** (attempt - 1))))
            response = client.models.generate_content(
                model=MODEL_NAME, contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", temperature=0.3),
            )
            data = json.loads(response.text or "{}")
            break
        except Exception as exc:  # noqa: BLE001
            logger.warning("theme_summary_attempt_failed attempt=%d error=%s", attempt, exc)

    if not isinstance(data, dict):
        logger.error("theme_summary_failed — 테마 요약 생성 실패")
        return

    for t in targets:
        s = data.get(t.name, "")
        if isinstance(s, (dict, list)):
            s = json.dumps(s, ensure_ascii=False)
        t.description = str(s or "").strip()
    logger.info("theme_summary_ok n=%d", len(targets))


_HOLD_STATE_KO = {
    "BREAKDOWN": "추세붕괴", "STOP60": "60선이탈", "STOP20": "20선이탈",
    "ADD": "눌림목(추가매수후보)", "HOLD": "추세양호(홀딩)", "NEUTRAL": "관망", "UNKNOWN": "데이터부족",
}


def _holdings_fallback(rows: list[dict]) -> str:
    """보유종목 AI 요약 실패/키 없음 시 상태 카운트 기반 결정론적 코멘트."""
    counts: dict[str, int] = {}
    for r in rows:
        st = r.get("state", "UNKNOWN")
        counts[st] = counts.get(st, 0) + 1
    seg = [f"{_HOLD_STATE_KO.get(st, st)} {n}종목"
           for st, n in sorted(counts.items()) if n]
    risk = counts.get("BREAKDOWN", 0) + counts.get("STOP60", 0) + counts.get("STOP20", 0)
    tail = " 손절선 이탈 종목은 분할 대응을 검토하세요." if risk else " 추세 이탈 종목은 없습니다."
    return f"보유 {len(rows)}종목 — " + ", ".join(seg) + "." + tail


async def summarize_holdings(snap: MarketSnapshot) -> None:
    """보유종목 전체에 대한 AI 종합 코멘트 → snap.holdings_summary.

    개별 종목 진단(state/cross_signal)을 종합해 '지금 무엇을 홀드/익절/손절 검토할지'
    2~3문장 코멘트를 생성. 실패/키 없음/한도 시 결정론적 폴백(상태 카운트)으로 대체.
    """
    import asyncio
    import random

    rows = snap.holdings_status or []
    if not rows:
        return

    settings = get_settings()
    if not settings.gemini_api_key:
        snap.holdings_summary = _holdings_fallback(rows)
        return

    lines = []
    for r in rows:
        st = _HOLD_STATE_KO.get(r.get("state", "UNKNOWN"), r.get("state", ""))
        cs = {"PULLBACK": "단기눌림", "CORRECTION": "조정시작"}.get(r.get("cross_signal"), "")
        lines.append(f"- {r.get('name', '')} {r.get('profit_rate', 0):+.1f}% | {st}"
                     + (f" | {cs}" if cs else "") + f" | {r.get('reason', '')}")
    blob = "\n".join(lines)
    prompt = (
        "다음은 사용자의 보유종목 진단 결과다. 전체 포트폴리오 관점에서 "
        "지금 무엇을 홀드/익절/손절 검토하면 좋을지 2~3문장으로 종합 코멘트하라.\n"
        "개별 진입가·목표가·매수추천은 하지 말 것. 상태(추세·손절선·눌림)에 근거해 "
        "차분하게 요약하고, 사실을 지어내지 말 것. 면책은 따로 표시되니 생략.\n\n"
        f"[보유종목 진단]\n{blob}\n\n"
        '반드시 JSON으로만 답하라: {"summary": "2~3문장 종합 코멘트"}'
    )

    client = genai.Client(api_key=settings.gemini_api_key)
    for attempt in range(3):
        try:
            if attempt > 0:
                await asyncio.sleep(random.uniform(2 * (2 ** (attempt - 1)), 5 * (2 ** (attempt - 1))))
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", temperature=0.3),
            )
            data = json.loads(response.text or "{}")
            text = str(data.get("summary", "")).strip() if isinstance(data, dict) else ""
            if text:
                snap.holdings_summary = text
                logger.info("holdings_summary_ok n=%d", len(rows))
                return
        except Exception as exc:  # noqa: BLE001
            logger.warning("holdings_summary_attempt_failed attempt=%d error=%s", attempt, exc)

    snap.holdings_summary = _holdings_fallback(rows)
    logger.info("holdings_summary_fallback n=%d", len(rows))
