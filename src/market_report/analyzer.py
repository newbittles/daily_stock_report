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

MODEL_NAME = "gemini-2.5-flash"  # 빠르고 저렴

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


def _pre_close_prompt(snap: MarketSnapshot, context: str) -> str:
    return f"""당신은 한국 주식 시장 전문 애널리스트입니다. 지금 시각은 장 마감 40분 전 (14:50).
사용자는 종가베팅 전략을 사용합니다: 마감 직전 거래량이 쏠리거나, 과매도 후 반등 신호가 보이는 종목을 매수.

아래 14:50 시점 데이터를 보고 다음을 출력하세요. **반드시 JSON 형식**으로:

{{
  "summary": "오늘 장 분위기 1-2문장 요약 (예: '코스피 약세 속 2차전지 강세 지속')",
  "why_moved": "왜 이런 흐름인지 2-3문장 설명 (수급·테마·이벤트 근거)",
  "theme_commentary": "가장 강한 테마 2-3개에 대한 짧은 해설 (왜 강한지, 지속 가능성)",
  "candidate_picks": [
    {{
      "ticker": "6자리 종목코드",
      "name": "종목명",
      "rationale": "왜 종가베팅 후보인지 — 거래량/등락/테마/뉴스 근거 (2-3문장)",
      "risk": "주의할 위험 요인 (1문장)"
    }}
    // 정확히 5개
  ]
}}

선정 기준:
- 거래량 상위 또는 상승률 상위에 있으면서 강세 테마에 속한 종목
- 또는 과매도(-3%~-10%)인데 거래량 급증 + 강세 테마 → 반등 후보
- ETF·인버스·레버리지는 제외 (실제 종목만)
- 5개 모두 서로 다른 테마/특성에서 선정 (다양성)

데이터:
{context}

JSON만 출력하고 다른 설명은 추가하지 마세요."""


def _post_close_prompt(snap: MarketSnapshot, context: str) -> str:
    return f"""당신은 한국 주식 시장 전문 애널리스트입니다. 지금은 장 마감 후 (16:30).
오늘 시장을 정리하고 내일 관전 포인트를 제시하세요. **반드시 JSON 형식**으로:

{{
  "summary": "오늘 장 1-2문장 요약 (지수 등락 + 핵심 키워드)",
  "why_moved": "오늘 시장이 이렇게 움직인 이유 (3-4문장, 수급/테마/이벤트 근거)",
  "theme_commentary": "오늘 강했던 테마 Top 3 해설 — 각 테마별로 왜 강했고 지속 가능성은? (각 2-3문장)",
  "tomorrow_watchpoints": [
    "내일 주목할 포인트 1 (1문장)",
    "내일 주목할 포인트 2 (1문장)",
    "내일 주목할 포인트 3 (1문장)"
  ]
}}

데이터:
{context}

JSON만 출력하고 다른 설명은 추가하지 마세요."""


async def analyze(snap: MarketSnapshot) -> MarketSnapshot:
    """Gemini로 시장 분석. snap을 mutate해서 반환.

    실패 시 빈 요약·후보로 채워서 반환 (리포트 자체는 발송 가능하도록).
    """
    settings = get_settings()
    context = _build_snapshot_context(snap)

    if snap.mode == "pre_close":
        prompt = _pre_close_prompt(snap, context)
    else:
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
        snap.summary = "AI 분석을 일시적으로 사용할 수 없습니다. 아래 데이터를 직접 참고하세요."
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

    if snap.mode == "pre_close":
        picks_raw = data.get("candidate_picks", [])
        # 검증: 각 항목에 ticker, name, rationale 있는지
        valid_picks = []
        for p in picks_raw:
            if not isinstance(p, dict):
                continue
            if not all(k in p for k in ("ticker", "name", "rationale")):
                continue
            valid_picks.append({
                "ticker": str(p["ticker"]).strip(),
                "name": str(p["name"]).strip(),
                "rationale": str(p["rationale"]).strip(),
                "risk": str(p.get("risk", "")).strip(),
            })
        snap.candidate_picks = valid_picks
    else:
        # 마감 후: tomorrow_watchpoints를 candidate_picks 자리에 보관 (재사용)
        snap.candidate_picks = [
            {"watchpoint": w} for w in data.get("tomorrow_watchpoints", [])
            if isinstance(w, str)
        ]

    logger.info(
        "gemini_analyze_ok mode=%s picks=%d summary_len=%d",
        snap.mode, len(snap.candidate_picks), len(snap.summary)
    )
    return snap
