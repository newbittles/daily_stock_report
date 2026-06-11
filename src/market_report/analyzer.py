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

# ── Gemini 일일 한도(429) 차단기 (사용자 2026-06-10) ──────────────────────────
# 무료 한도(20/일) 소진 시 한 리포트 런에서 종목·테마·수급 요약마다 3회 재시도+긴 백오프가
# 누적돼 렌더·발송이 10분+ 지연·중단(행)되는 문제 방지. 첫 429 RESOURCE_EXHAUSTED 감지 시
# 차단기를 트립 → 이후 AI 호출은 즉시 폴백(스킵). run_full 시작 시 reset_quota_breaker()로 초기화.
_QUOTA_STATE = {"tripped": False}


def reset_quota_breaker() -> None:
    """새 리포트 런 시작 시 차단기 초기화(한도 회복 재평가)."""
    _QUOTA_STATE["tripped"] = False


def quota_blocked() -> bool:
    """차단기 트립 상태(한도 소진으로 AI 스킵 중)면 True."""
    return _QUOTA_STATE["tripped"]


def _maybe_trip_quota(exc: Exception) -> bool:
    """예외가 Gemini 일일 한도(429 RESOURCE_EXHAUSTED)면 차단기 트립. 트립이면 True."""
    s = str(exc)
    if "RESOURCE_EXHAUSTED" in s or "exceeded your current quota" in s:
        if not _QUOTA_STATE["tripped"]:
            logger.warning("gemini_quota_breaker_tripped — 이후 AI 호출 스킵(폴백 유지)")
        _QUOTA_STATE["tripped"] = True
        return True
    return False

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
  "summary": "미국장 시장 전반 종합 요약 2-3문장 (지수 등락 + 주도 섹터/업종 + 핵심 이슈·키워드). 전략 나열이 아닌 '시황 종합 의견'으로.",
  "why_moved": "미국장이 왜 이렇게 움직였나 3-4문장 (강세/약세 섹터·빅테크·매크로·금리·뉴스 근거)",
  "theme_commentary": "오늘 미국 강세/약세 섹터·업종 흐름 종합 해설 + 뉴스 맥락 3-4문장, 마지막에 한국장 시사점 1문장(예: 미국 반도체 강세 → 한국 반도체 주목)"
}}

수치는 아래 주어진 값만 사용하고 지어내지 마세요. 뉴스는 모르면 언급하지 마세요.
요약은 개별 전략(A/B/C/D) 해설이 아니라 '시장·업종·뉴스 전반의 종합 의견'으로 작성하세요.

데이터:
{context}

JSON만 출력하고 다른 설명은 추가하지 마세요."""


def _us_premarket_prompt(snap: MarketSnapshot, context: str) -> str:
    return f"""당신은 미국 증시 전문 애널리스트입니다. 지금은 미국 정규장 개장 전 '프리장(pre-market)' 시간입니다.
아래 데이터에서 **종목·섹터 등락률은 프리장 기준**, 지수는 직전 정규장 마감 기준입니다.
한국 투자자를 위해 현재 프리장 상황을 다음 **JSON 형식**으로 해설하세요:

{{
  "summary": "지금 프리장 시장 전반 종합 2-3문장 (강세/약세 분위기 + 주도 섹터/업종 + 핵심 이슈). 전략 나열 아닌 시황 종합 의견으로.",
  "why_moved": "직전 정규장 마감 후 주요 뉴스·이슈가 무엇이었고 그 결과 프리장에서 어떤 섹터/종목이 상승·하락 중인지 3-4문장",
  "theme_commentary": "프리장 강세/약세 섹터·업종 흐름 종합 해설 + 뉴스 맥락 3-4문장, 마지막에 한국장 시사점 1문장"
}}

수치는 아래 값만 사용하고 지어내지 마세요. 뉴스가 불확실하면 일반적 맥락으로만 설명하세요.
요약은 개별 전략 해설이 아니라 '시장·업종·뉴스 전반의 종합 의견'으로 작성하세요.

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
    if snap.mode in ("us_morning", "us_premarket", "us_intraday", "us_afterhours"):
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


def _intraday_flow_context(snap: MarketSnapshot) -> str:
    """주요 종목 장중 분봉 흐름(flow_desc)을 프롬프트용 텍스트로(#473/#474). 없으면 ''."""
    rows: list[str] = []
    seen: set[str] = set()
    for r in ((snap.prev_top3_status or []) + (snap.hot_stocks or [])
              + (snap.holdings_status or []) + (snap.prev_candidates_status or [])):
        tk = str(r.get("ticker", ""))
        d = r.get("flow_desc")
        if d and tk not in seen:
            seen.add(tk)
            rows.append(f"  {r.get('name', '')}: {d}")
    return ("\n[주요 종목 장중 분봉 흐름]\n" + "\n".join(rows)) if rows else ""


async def summarize_midday(snap: MarketSnapshot) -> str:
    """장중(정오) 한줄 코멘트 — 지수·수급·강세테마 + 주요종목 장중 흐름 기반 1~3문장.

    Gemini 1회 시도, 실패/키없음 시 결정론 폴백(_fallback_summary). 종목 추천·매수의견 금지.
    """
    settings = get_settings()
    fallback = _fallback_summary(snap)
    if not settings.gemini_api_key or quota_blocked():
        return fallback

    context = _build_snapshot_context(snap) + _intraday_flow_context(snap)
    prompt = (
        "다음은 오늘 한국 증시 '장중' 스냅샷이다. 지금까지의 흐름을 "
        "2~3문장으로 간결히 코멘트하라(지수 방향·수급·강세테마 중심). "
        "지수 약세/강세만 말하지 말고, '주요 종목 장중 분봉 흐름'이 있으면 "
        "급락 후 반등·고점 후 하락처럼 눈에 띄는 종목의 장중 궤적을 1문장 포함하라. "
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
    if not settings.gemini_api_key or quota_blocked():  # 키없음/한도소진 → 결정론 폴백(사용자 2026-06-10)
        snap.summary = _fallback_summary(snap)
        snap.why_moved = ""
        snap.theme_commentary = ""
        snap.candidate_picks = []
        return snap

    if snap.mode == "us_premarket":
        context = _build_us_context(snap)
        prompt = _us_premarket_prompt(snap, context)
    elif snap.mode in ("us_morning", "us_intraday", "us_afterhours"):
        # us_intraday(장중)·us_afterhours(애프터 리뷰)도 미국 컨텍스트·프롬프트 사용
        # (안 그러면 KR 프롬프트로 빠져 '코스피 요약'이 나옴, 버그)
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
            if _maybe_trip_quota(exc):  # 일일 한도 → 재시도 중단(폭풍 방지)
                break

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
    # 전날 추천 Top3·종가베팅 후보는 change_pct가 없고 today_pct가 '오늘 등락'이다(폴백).
    chg_raw = p.get("change_pct")
    if chg_raw is None:
        chg_raw = p.get("today_pct")
    chg = float(chg_raw or 0)
    lead = "(테마 주도주)" if p.get("is_theme_leader") else ""
    move = _move_label(chg, p.get("cross_signal"))
    return (f"- {tk} {p.get('name', '')} | 오늘 {chg:+.1f}% {move} "
            f"| 테마 {p.get('theme', '-') or '-'}{lead}")


def _supply_3d_line(rows: list[dict]) -> str:
    """종목 일자별 투자자 순매수(수량) → '💰 수급: 외인 ▲3일·기관 ▼1일·개인 ▲2일' 한 줄. 순수 함수.

    rows: [{date, prsn, frgn, orgn}] 최신순(KIS get_stock_investor_daily, 순매수 수량).
    0인 날(미체결·휴장 등) 제외 후, 가장 최근 완료일 기준 같은 부호 연속일수(몇일 연속 순매수/매도)를
    계산해 표기(사용자 2026-06-11). 데이터 없으면 빈 문자열.
    """
    valid = [r for r in rows if any(r.get(k) for k in ("prsn", "frgn", "orgn"))]
    if not valid:
        return ""

    def _streak(key: str) -> str:
        first = valid[0].get(key, 0) or 0
        if first == 0:
            return ""
        pos = first > 0
        n = 0
        for r in valid:
            v = r.get(key, 0) or 0
            if v == 0 or (v > 0) != pos:
                break
            n += 1
        arrow = "▲순매수" if pos else "▼순매도"
        return f"{arrow}{n}일" if n >= 2 else arrow

    parts = []
    for key, label in (("frgn", "외인"), ("orgn", "기관"), ("prsn", "개인")):
        s = _streak(key)
        if s:
            parts.append(f"{label} {s}")
    return ("💰 수급(최근): " + " · ".join(parts)) if parts else ""


async def summarize_stocks(
    snap: MarketSnapshot, extra_pools: list[list[dict]] | None = None,
) -> None:
    """Top3 + 전략스크린 종목별 AI 요약을 1회 배치 호출로 사전 생성.

    정적 리포트(GitHub Pages)에 임베드할 종목별 요약을 미리 만들어 각 dict에
    'ai_summary'를 추가한다. 클릭 시 실시간 호출(키 노출) 대신 사전 생성 방식.
    종목 수만큼 호출하지 않고 한 프롬프트에 모아 1회만 호출(한도·시간 절약).
    실패/키 없음/한도 시 빈 문자열 → 프론트에서 버튼 미표시.

    extra_pools: 기본 풀(top3·screen·e·surge) 외에 추가로 요약을 붙일 종목 리스트들.
    장중 리포트가 hot_stocks·전날Top3·종가베팅·보유종목에도 호재뉴스·공시를 달기 위해 사용
    (사용자 2026-06-10). 각 리스트의 dict는 ticker·name 키를 가져야 하며, 결과는 동일 dict에
    'ai_summary'로 주입된다. None이면 기존 동작(마감 리포트)과 동일.
    """
    import asyncio
    import random

    settings = get_settings()
    if not settings.gemini_api_key or quota_blocked():
        return

    # 요약 대상 풀(기본 + 추가). put-back도 동일 풀을 재사용해 일관성 유지.
    pools: list[list[dict]] = [
        snap.top3 or [], snap.screen_picks or [], snap.e_picks or [], snap.surge_picks or [],
        snap.supply_driven_picks or [],   # 🏦 H 수급 주도도 AI요약(📰호재뉴스·📋공시, 사용자 2026-06-11)
    ]
    for ep in (extra_pools or []):
        if ep:
            pools.append(ep)

    # 대상 종목 수집 (ticker 중복 1회)
    targets: dict[str, dict] = {}
    for src_list in pools:
        for p in src_list:
            tk = str(p.get("ticker", "")).strip()
            if tk and tk not in targets:
                targets[tk] = p
    if not targets:
        return

    # 종목별 최근 뉴스 + 공시(DART) 조회 — 호재뉴스·공시 유무를 '사실 기반'으로 명시(환각 방지).
    # 조회 성공+없음=없음, 조회실패/매핑없음=확인불가(거짓 '없음' 방지). 동시 조회로 시간 절약.
    from src.market_report.scrapers.stock_news import fetch_stock_news
    dart_key = settings.dart_api_key

    async def _stock_ctx(tk: str, name: str):
        try:
            news = await fetch_stock_news(name, top=3)
        except Exception:  # noqa: BLE001
            news = []
        disc = None
        if dart_key:
            try:
                from src.datasource.dart import fetch_recent_disclosures
                disc = await fetch_recent_disclosures(tk, dart_key, days=10, top=5)
            except Exception:  # noqa: BLE001
                disc = None
        return tk, news, disc

    _ctx_res = await asyncio.gather(
        *[_stock_ctx(tk, p.get("name", "")) for tk, p in targets.items()], return_exceptions=True)
    stock_ctx: dict[str, tuple] = {r[0]: (r[1], r[2]) for r in _ctx_res if isinstance(r, tuple)}

    # 종목별 최근 수급(개인/외인/기관 순매수 연속일) — KIS inquire-investor(사실 데이터, AI 아님). 사용자 2026-06-11.
    # ai_summary에 한 줄 덧붙임. 동시성 제한·실패 graceful(없으면 줄 생략, 리포트 안 깨짐).
    supply_lines: dict[str, str] = {}
    try:
        from src.datasource.kis.adapter import KisAdapter
        _adapter = KisAdapter(settings.kis_app_key, settings.kis_app_secret,
                              settings.kis_account_no, settings.kis_env)
        _sup_sem = asyncio.Semaphore(6)

        async def _sup(tk: str) -> tuple[str, str]:
            async with _sup_sem:
                try:
                    rows = await _adapter.get_stock_investor_daily(tk, days=10)
                except Exception:  # noqa: BLE001
                    rows = []
                await asyncio.sleep(random.uniform(0.1, 0.3))  # 전역 §7 분산
                return tk, _supply_3d_line(rows)

        _sup_res = await asyncio.gather(*[_sup(tk) for tk in targets], return_exceptions=True)
        supply_lines = {r[0]: r[1] for r in _sup_res if isinstance(r, tuple) and r[1]}
    except Exception as exc:  # noqa: BLE001
        logger.warning("supply_3d_fetch_failed error=%s", exc)

    def _ctx_block(tk: str, name: str) -> str:
        news, disc = stock_ctx.get(tk, ([], None))
        nlines = "; ".join(n.title for n in (news or [])[:3]) if news else "최근 종목 뉴스 없음"
        if disc is None:
            dlines = "확인 불가"
        elif not disc:
            dlines = "최근 공시 없음"
        else:
            dlines = "; ".join(f"{d['date'][4:6]}/{d['date'][6:8]} {d['title']}" for d in disc[:5])
        return f"{tk} {name}\n  뉴스: {nlines}\n  공시: {dlines}"

    stock_ctx_blob = "\n".join(_ctx_block(tk, p.get("name", "")) for tk, p in targets.items())

    # 컨텍스트: 강세 테마(주도주) + 시장 뉴스 → "왜 올랐나/내렸나" 추론 근거
    theme_blob = "\n".join(
        f"- {t.name} {t.change_pct:+.1f}% [주도주 {', '.join(t.leading_stocks[:3]) or '-'}]"
        for t in (snap.top_themes or [])[:10])
    news_blob = "\n".join(f"- {n.title}" for n in (snap.market_news or [])[:15])

    blob = "\n".join(_summary_target_line(tk, p) for tk, p in targets.items())

    prompt = (
        "다음은 오늘 한국 증시에서 시그널이 포착된 종목들이다. 각 종목이 "
        "**오늘 왜 그렇게 움직였는지** 를 1~2문장으로 간단히 요약하라.\n"
        "★맨 앞줄에 '🏢 {회사 주요사업 한 줄}'을 먼저 적어라(예: 현대모비스→'🏢 자동차부품 제조', "
        "삼성전자→'🏢 반도체·스마트폰 제조'). 네이버 증권·공시에서 확인되는 잘 알려진 주요 업종/사업만 "
        "사실대로 적고, 불확실하면 업종 분류만 간단히(절대 지어내지 말 것).\n"
        "- ▲상승 종목: 왜 올랐는지(강세 이유)를 설명.\n"
        "- ▼하락·조정 종목: 왜 하락(또는 조정)했는지 설명. 특히 '상승 후 하락 전환'이면 "
        "차익실현·과열 부담·고점 매도 등 하락 사유를 짚어라.\n"
        "근거는 ①주요 뉴스 ②소속 테마 ③주도주 여부 중심으로. "
        "진입가·손절가·매수추천은 절대 언급하지 말 것(그건 따로 표시됨). "
        "뉴스에 근거가 없으면 테마·수급 맥락으로 설명하되, 사실을 지어내지 말 것. "
        "숫자(가격·금액)는 천단위 콤마로 표기(예: 66,000원).\n"
        "★요약 끝에 반드시 줄을 바꿔 두 항목을 덧붙여라(아래 [종목별 최근 뉴스·공시]에 주어진 것만 사용, "
        "지어내기 절대 금지):\n"
        "  📰 호재뉴스: {해당 종목 뉴스에 호재성 내용 있으면 한 줄 요약 / 뉴스가 '최근 종목 뉴스 없음'이면 '없음'}\n"
        "  📋 공시: {공시 목록 있으면 핵심 1~2건 제목 요약 / '최근 공시 없음'이면 '없음' / '확인 불가'이면 '확인 불가'}\n"
        "공시 항목은 주어진 '공시:' 값에 근거해서만 적되, 유상증자·전환사채 등은 악재성, 수주(공급계약)·자사주취득 등은 호재성으로 톤을 반영하라.\n\n"
        f"[오늘 강세 테마]\n{theme_blob}\n\n"
        f"[시장 뉴스 헤드라인]\n{news_blob}\n\n"
        f"[종목별 최근 뉴스·공시]\n{stock_ctx_blob}\n\n"
        f"[대상 종목]\n{blob}\n\n"
        '반드시 JSON으로만 답하라(값은 위 형식의 줄바꿈 포함 문자열): '
        '{"종목코드": "🏢 회사 주요사업 한 줄\\n왜 올랐는지(또는 왜 하락했는지) 1~2문장\\n📰 호재뉴스: …\\n📋 공시: …", ...}'
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
            if _maybe_trip_quota(exc):
                break

    if not isinstance(data, dict):
        logger.error("stock_summary_failed — 종목 요약 생성 실패")
        return

    def _put(p: dict) -> None:
        tk = str(p.get("ticker", "")).strip()
        s = data.get(tk, "")
        if isinstance(s, (dict, list)):
            s = json.dumps(s, ensure_ascii=False)
        s = str(s or "").strip()
        sup = supply_lines.get(tk)  # 최근 수급(개인/외인/기관 연속) 사실 한 줄 덧붙임(사용자 2026-06-11)
        if s and sup:
            s = f"{s}\n{sup}"
        p["ai_summary"] = s

    for lst in pools:
        for p in lst:
            _put(p)
    logger.info("stock_summary_ok n=%d supply=%d", len(targets), len(supply_lines))


async def summarize_us_stocks(snap: MarketSnapshot) -> None:
    """미국 리포트 종목별 AI 요약(ai_summary)을 1회 배치 호출로 사전 생성(사용자 2026-06-05 #309).

    us_top3 ∪ 전략그룹 ∪ 섹터/테마 대장 ∪ E/급등초입 종목을 symbol 기준 중복제거해 한 프롬프트로
    '왜 움직였나' 1~2문장(한국어) 요약 → 각 dict에 ai_summary. 실패/키없음/한도 시 빈 문자열(버튼 미표시).
    """
    import asyncio
    import random

    settings = get_settings()
    if not settings.gemini_api_key or quota_blocked():
        return

    pools: list[list[dict]] = [
        snap.us_top3 or [], snap.us_theme_leaders or [], snap.us_sector_leaders or [],
        snap.e_picks or [], snap.surge_picks or [],
        snap.support_picks or [], snap.coil_picks or [],          # F·G(미국, 사용자 2026-06-10)
        getattr(snap, "us_premarket_top", None) or [],            # 프리장 급등 TOP5
        getattr(snap, "us_screen_ranked", None) or [],            # 🇺🇸미국 종목 스크리닝(템플릿 렌더 대상, 사용자 2026-06-11)
    ]
    for g in (snap.us_screen_groups or []):
        pools.append(g.get("picks", []))
    targets: dict[str, dict] = {}
    for lst in pools:
        for p in lst:
            sym = str(p.get("symbol", "")).strip()
            if sym and sym not in targets:
                targets[sym] = p
    if not targets:
        return

    # 종목별 최근 뉴스(yfinance) — 호재/악재를 '사실 기반'으로(환각 방지). 동시성 제한으로 yfinance 스로틀 회피.
    from src.datasource.us.fdr_source import fetch_us_stock_news
    _sem = asyncio.Semaphore(8)

    async def _news_ctx(sym: str) -> tuple[str, list]:
        async with _sem:
            try:
                ns = await fetch_us_stock_news(sym, top=3)
            except Exception:  # noqa: BLE001
                ns = []
            await asyncio.sleep(random.uniform(0.1, 0.3))  # 전역 §7 분산 딜레이
            return sym, ns

    _res = await asyncio.gather(*[_news_ctx(s) for s in targets], return_exceptions=True)
    news_ctx: dict[str, list] = {r[0]: r[1] for r in _res if isinstance(r, tuple)}

    def _stock_news_block(sym: str, name: str) -> str:
        ns = news_ctx.get(sym) or []
        nl = "; ".join(n.get("title", "") for n in ns[:3]) if ns else "최근 종목 뉴스 없음"
        return f"{sym} {name}\n  뉴스: {nl}"

    stock_news_blob = "\n".join(_stock_news_block(s, p.get("name", "")) for s, p in targets.items())

    sec_blob = "\n".join(f"- {s.get('name', '')} {s.get('change_pct', 0):+.1f}%"
                         for s in (snap.us_sectors or [])[:8])
    news_blob = "\n".join(f"- {n.get('title', '')}" for n in (snap.us_news or [])[:12])
    blob = "\n".join(
        f"{sym}: {p.get('name', '')} | 섹터 {p.get('sector') or p.get('theme', '')} | "
        f"등락 {p.get('change_pct', 0):+.1f}% | {p.get('reason', '')}"
        for sym, p in targets.items())

    prompt = (
        "다음은 미국 증시에서 시그널이 포착된 종목들이다. 각 종목이 **직전 정규장에서 왜 그렇게 "
        "움직였는지**를 한국어 1~2문장으로 요약하라.\n"
        "★맨 앞줄에 '🏢 {회사 주요사업 한 줄}'을 먼저 적어라(예: 엔비디아→'🏢 AI 반도체(GPU) 설계', "
        "애플→'🏢 아이폰·맥 등 IT 기기 제조'). 잘 알려진 주요 사업/업종만 사실대로, 불확실하면 업종만(지어내기 금지).\n"
        "- ▲상승: 강세 이유. ▼하락: 하락/조정 사유(차익실현·과열·실적·매크로 등).\n"
        "근거는 ①주요 뉴스 ②소속 섹터/테마 중심. 진입가·손절·매수추천 언급 금지. "
        "사실을 지어내지 말 것. 숫자(가격·금액)는 천단위 콤마로 표기(예: 1,200달러·66,000원).\n"
        "★요약 끝에 반드시 줄을 바꿔 한 항목을 덧붙여라(아래 [종목별 최근 뉴스]에 주어진 것만 사용, 지어내기 금지):\n"
        "  📰 호재/악재: {해당 종목 뉴스에 호재(실적호조·신제품·수주 등)/악재(실적부진·소송·규제 등) 있으면 "
        "호재인지 악재인지 톤 반영해 한 줄 / 뉴스가 '최근 종목 뉴스 없음'이면 '없음'}\n\n"
        f"[강세 섹터]\n{sec_blob}\n\n[미국 시장 뉴스]\n{news_blob}\n\n"
        f"[종목별 최근 뉴스]\n{stock_news_blob}\n\n[대상 종목]\n{blob}\n\n"
        '반드시 JSON 객체(딕셔너리) 하나로만 답하라. key=심볼 문자열, value=문자열 하나(아래 형식, '
        '줄바꿈 \\n 포함). 배열·중첩객체 금지: '
        '{"심볼": "🏢 회사 주요사업 한 줄\\n왜 올랐는지/하락했는지 1~2문장\\n📰 호재/악재: …", ...}'
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
                    response_mime_type="application/json", temperature=0.3,
                    max_output_tokens=16384),  # 종목多+뉴스로 출력이 길어 truncation 방지(사용자 2026-06-11)
            )
            parsed = json.loads(response.text or "{}")
            if isinstance(parsed, dict) and parsed:
                data = parsed
                break
            # 모델이 가끔 배열·빈값 반환 → 배열이면 dict로 보정 시도, 아니면 재시도
            if isinstance(parsed, list):
                _coerced = {}
                for it in parsed:
                    if isinstance(it, dict):
                        _sym = str(it.get("심볼") or it.get("symbol") or it.get("ticker") or "").strip()
                        _val = it.get("요약") or it.get("summary") or it.get("value")
                        if _sym and isinstance(_val, str):
                            _coerced[_sym] = _val
                if _coerced:
                    data = _coerced
                    break
            logger.warning("us_stock_summary_nondict attempt=%d type=%s preview=%.180s",
                           attempt, type(parsed).__name__, str(response.text))
        except Exception as exc:  # noqa: BLE001
            logger.warning("us_stock_summary_attempt_failed attempt=%d error=%s", attempt, exc)
            if _maybe_trip_quota(exc):
                break

    if not isinstance(data, dict) or not data:
        logger.error("us_stock_summary_failed — 미국 종목 요약 생성 실패")
        return

    for lst in pools:
        for p in lst:
            sym = str(p.get("symbol", "")).strip()
            s = data.get(sym, "")
            if isinstance(s, (dict, list)):
                s = json.dumps(s, ensure_ascii=False)
            if s:
                p["ai_summary"] = str(s).strip()
    logger.info("us_stock_summary_ok n=%d", len(targets))


async def translate_us_news(snap: MarketSnapshot) -> None:
    """미국 시장 뉴스 헤드라인 한국어 번역(1회 배치, 사용자 #394). 각 dict에 title_ko. 실패 시 원문 유지.

    비용 최소화: 헤드라인 일괄 1회 호출(flash-lite). 키없음/한도/실패 시 생략(원문 영어 표시)."""
    settings = get_settings()
    news = snap.us_news or []
    if not settings.gemini_api_key or quota_blocked() or not news:
        return
    items = "\n".join(f"{i}: {n.get('title', '')}" for i, n in enumerate(news[:15]) if n.get("title"))
    if not items:
        return
    prompt = ("다음 미국 증시 뉴스 헤드라인을 자연스러운 한국어로 번역하라(고유명사·티커는 유지). "
              'JSON으로만: {"0":"번역",...}.\n\n' + items)
    try:
        client = genai.Client(api_key=settings.gemini_api_key)
        resp = client.models.generate_content(
            model=MODEL_NAME, contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2))
        data = json.loads(resp.text or "{}")
        if isinstance(data, dict):
            for i, n in enumerate(news):
                t = data.get(str(i))
                if t and isinstance(t, str):
                    n["title_ko"] = t.strip()
            logger.info("us_news_translate_ok n=%d", len(data))
    except Exception as exc:  # noqa: BLE001
        logger.warning("us_news_translate_failed error=%s", exc)


async def summarize_flows(snap: MarketSnapshot) -> None:
    """최근 일주일 시장 수급(개인/기관/외국인) 흐름 AI 요약 → snap.flows_summary (사용자 #313/#316).

    연속 순매수/순매도·전일대비·전주대비를 결정론으로 계산(1차 근거) → AI가 2~3문장 narrate.
    AI 실패/키없음 시 결정론 팩트 문장 폴백. KR 전용(pre/post).
    """
    from src.market_report.flows_history import compute_flow_stats, load_flows_series

    series = load_flows_series(10)
    stats = compute_flow_stats(series) if series else {}
    if not stats:
        return

    inv_ko = {"personal": "개인", "foreign": "외국인", "institution": "기관"}
    mk_ko = {"kospi": "코스피", "kosdaq": "코스닥"}

    def _eok(v) -> str:
        return f"{v:+,}억" if v is not None else "—"

    lines: list[str] = []
    for mk in ("kospi", "kosdaq"):
        for inv in ("foreign", "institution", "personal"):
            s = stats.get(f"{mk}_{inv}")
            if not s:
                continue
            stk = s["streak"]
            stk_str = (f"{abs(stk)}일 연속 순{'매수' if stk > 0 else '매도'}" if stk else "혼조")
            lines.append(
                f"{mk_ko[mk]} {inv_ko[inv]}: 당일 {_eok(s['today'])}({stk_str}), "
                f"전일 {_eok(s['prev'])}, 전주(5일전) {_eok(s['week_ago'])}, 최근5일합 {_eok(s['week_sum'])}")
    fact_blob = "\n".join(lines)
    snap.flows_summary = " · ".join(lines[:4])  # 기본 = 결정론 팩트(외인·기관 우선)

    settings = get_settings()
    if not settings.gemini_api_key or quota_blocked() or not fact_blob:
        return
    prompt = (
        "다음은 최근 거래일 한국 증시 투자자별 순매수(억원, +매수/−매도) 데이터다.\n"
        "외국인·기관 수급을 중심으로 최근 일주일 흐름을 2~3문장으로 요약하라.\n"
        "반드시 포함: ①연속 순매수/순매도(며칠째인지) ②전일대비·전주대비 증감 흐름(늘었나/줄었나·매수전환/매도전환).\n"
        "매수추천·개별종목 언급 금지. 숫자는 천단위 콤마. 사실만, 지어내지 말 것.\n\n"
        f"{fact_blob}"
    )
    try:
        client = genai.Client(api_key=settings.gemini_api_key)
        resp = client.models.generate_content(
            model=MODEL_NAME, contents=prompt,
            config=types.GenerateContentConfig(temperature=0.3))
        txt = (resp.text or "").strip()
        if txt:
            snap.flows_summary = txt
        logger.info("flows_summary_ok len=%d", len(snap.flows_summary))
    except Exception as exc:  # noqa: BLE001
        logger.warning("flows_summary_failed error=%s — 결정론 폴백 유지", exc)


async def summarize_themes(snap: MarketSnapshot) -> None:
    """강세 테마별 '왜 올랐나' 1~2줄 요약 → 각 ThemeRank.description.

    뉴스·정책/정치 이슈·기대감·대장주 움직임을 근거로(예: 보험 → 정책 기대감).
    1회 배치 호출. 키 없음/실패 시 description 미설정(렌더 측에서 생략).
    """
    import asyncio
    import random

    settings = get_settings()
    if not settings.gemini_api_key or quota_blocked() or not snap.top_themes:
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
    if not settings.gemini_api_key or quota_blocked():
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
