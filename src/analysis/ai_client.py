"""AI 클라이언트 선택 — KIE_API_KEY 가 있으면 Kie(OpenAI 호환 chat), 없으면 Google Gemini.

호출부는 `client.models.generate_content(model=, contents=, config=)` → `.text` 그대로 쓴다.
Gemini 선결제 크레딧 소진(402, 2026-11) 대체로 Kie 를 붙였다(사용자 2026-11-08 "kie 키 연결해").
Kie 401·402·403·429 는 메시지에 RESOURCE_EXHAUSTED 를 실어 analyzer 차단기가 트립되게 한다
(Hard Stop — 그 실행에선 AI 를 더 부르지 않고 결정론 폴백).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

KIE_CHAT_URL = "https://api.kie.ai/v1/chat/completions"
KIE_MODEL = "gemini-3-6-flash-openai"
_STOP = {401: "auth", 402: "credit", 403: "auth", 429: "rate_limit"}


@dataclass
class _Resp:
    text: str


def _stop(code: int) -> RuntimeError:
    return RuntimeError(f"RESOURCE_EXHAUSTED kie {_STOP[code]} code={code}")


def _strip_fence(s: str) -> str:
    m = re.match(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", s, re.S)
    return m.group(1) if m else s


class _KieModels:
    def __init__(self, api_key: str) -> None:
        self._key = api_key

    def generate_content(self, *, model: str, contents: str, config: Any = None) -> _Resp:
        body: dict[str, Any] = {
            "model": KIE_MODEL,  # 호출부 model(gemini 이름)은 무시
            "messages": [{"role": "user", "content": str(contents)}],
            "max_tokens": int(getattr(config, "max_output_tokens", None) or 8192),
        }
        temp = getattr(config, "temperature", None)
        if temp is not None:
            body["temperature"] = float(temp)
        json_mode = getattr(config, "response_mime_type", None) == "application/json"
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        r = httpx.post(KIE_CHAT_URL, json=body, timeout=180,
                       headers={"Authorization": f"Bearer {self._key}"})
        if r.status_code in _STOP:
            raise _stop(r.status_code)
        r.raise_for_status()
        return _Resp(parse_kie_text(r.json(), json_mode))


def parse_kie_text(data: Any, json_mode: bool = False) -> str:
    """Kie chat 응답 → 본문 텍스트. HTTP 200 에 {"code":402} 를 싣는 경우도 Hard Stop."""
    code = data.get("code") if isinstance(data, dict) else None
    if isinstance(code, int) and code in _STOP:
        raise _stop(code)
    choices = (data or {}).get("choices") or []
    if not choices:
        raise RuntimeError(f"kie 응답 이상: {str(data)[:200]}")
    text = (choices[0].get("message") or {}).get("content") or ""
    if isinstance(text, list):
        text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
    return _strip_fence(text) if json_mode else text


class KieClient:
    def __init__(self, api_key: str) -> None:
        self.models = _KieModels(api_key)


def has_ai_key(settings: Any) -> bool:
    return bool(getattr(settings, "kie_api_key", "") or getattr(settings, "gemini_api_key", ""))


def make_client(settings: Any) -> Any:
    if getattr(settings, "kie_api_key", ""):
        return KieClient(settings.kie_api_key)
    from google import genai

    return genai.Client(api_key=settings.gemini_api_key)
