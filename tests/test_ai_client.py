from types import SimpleNamespace

import httpx
import pytest
import respx

from src.analysis import ai_client
from src.analysis.ai_client import KieClient, has_ai_key, make_client, parse_kie_text
from src.market_report.analyzer import _maybe_trip_quota, reset_quota_breaker


def _ok(content: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def test_make_client_prefers_kie() -> None:
    assert isinstance(make_client(SimpleNamespace(kie_api_key="k", gemini_api_key="g")), KieClient)
    assert has_ai_key(SimpleNamespace(kie_api_key="k", gemini_api_key=""))
    assert not has_ai_key(SimpleNamespace(gemini_api_key=""))


def test_parse_strips_json_fence_and_hard_stops_on_body_code() -> None:
    assert parse_kie_text(_ok('```json\n{"a":1}\n```'), json_mode=True) == '{"a":1}'
    with pytest.raises(RuntimeError, match="RESOURCE_EXHAUSTED"):
        parse_kie_text({"code": 402, "msg": "credits insufficient"})


@respx.mock
def test_generate_content_maps_config_and_returns_text() -> None:
    route = respx.post(ai_client.KIE_CHAT_URL).mock(return_value=httpx.Response(200, json=_ok('{"x":1}')))
    cfg = SimpleNamespace(temperature=0.3, response_mime_type="application/json", max_output_tokens=None)
    resp = KieClient("k").models.generate_content(model="gemini-2.5-flash-lite", contents="hi", config=cfg)
    assert resp.text == '{"x":1}'
    body = route.calls[0].request.read().decode()
    assert '"json_object"' in body and ai_client.KIE_MODEL in body


@respx.mock
def test_http_402_trips_quota_breaker() -> None:
    respx.post(ai_client.KIE_CHAT_URL).mock(return_value=httpx.Response(402, json={}))
    reset_quota_breaker()
    with pytest.raises(RuntimeError) as ei:
        KieClient("k").models.generate_content(model="m", contents="hi")
    assert _maybe_trip_quota(ei.value)
    reset_quota_breaker()
