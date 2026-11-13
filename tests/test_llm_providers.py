"""Contract tests for the LLM adapters: the real SDK clients built by make_llm talk to respx
mocks of the real HTTP endpoints (Anthropic /v1/messages, OpenAI /v1/chat/completions, Azure
/openai/deployments/{d}/chat/completions?api-version=..., and a custom base_url)."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

import httpx
import pytest
import respx

from outreach.llm import LLMBadOutput, LLMError, LLMTruncated, LLMUnavailable, make_llm
from tests.fakes import make_settings
from tests.llm_http import mock_client, no_retry_sleep  # noqa: F401 - fixture

KEY = "sk-SECRET-KEY-123456"
SYSTEM = "SYSTEM-PROMPT-TEXT rules"
USER_TEXT = "PROSPECT-MESSAGE-TEXT hello"
SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {"label": {"type": "string"}, "note": {"type": ["string", "null"]}},
    "required": ["label"],
    "additionalProperties": False,
}

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
AZURE_ENDPOINT = "https://ozoo-res-secret.openai.azure.com"
AZURE_VERSION = "2024-10-21"
AZURE_URL = f"{AZURE_ENDPOINT}/openai/deployments/dep-classifier/chat/completions"
COMPAT_BASE = "https://llm-gw-secret.example.net/v1"
COMPAT_URL = f"{COMPAT_BASE}/chat/completions"


def call(llm: Any, model: str = "dep-classifier") -> Dict[str, Any]:
    return llm.structured(system=SYSTEM, messages=[{"role": "user", "content": USER_TEXT}],
                          tool_name="classify_reply", tool_description="Record it.", schema=SCHEMA,
                          max_tokens=321, model=model)


# ---------------------------------------------------------------- Anthropic

def anthropic_llm(router: respx.Router) -> Any:
    return make_llm(make_settings(llm_provider="anthropic", llm_api_key=KEY), http_client=mock_client(router))


def anthropic_body(content: Optional[list] = None, stop_reason: str = "tool_use") -> Dict[str, Any]:
    if content is None:
        content = [{"type": "tool_use", "id": "toolu_1", "name": "classify_reply", "input": {"label": "x"}}]
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "m", "content": content,
            "stop_reason": stop_reason, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}


def test_anthropic_happy_path_and_request_shape():
    router = respx.Router(assert_all_called=False)
    route = router.post(ANTHROPIC_URL).respond(200, json=anthropic_body(content=[
        {"type": "text", "text": "ok"},
        {"type": "tool_use", "id": "toolu_1", "name": "classify_reply", "input": {"label": "interested"}}]))
    assert call(anthropic_llm(router), model="model-a") == {"label": "interested"}
    req = route.calls.last.request
    assert KEY in (req.headers.get("x-api-key"), req.headers.get("authorization", "").removeprefix("Bearer "))
    body = json.loads(req.content)
    assert body["model"] == "model-a"
    assert body["max_tokens"] == 321
    assert body["system"] == SYSTEM
    assert body["messages"] == [{"role": "user", "content": USER_TEXT}]
    assert body["tools"] == [{"name": "classify_reply", "description": "Record it.", "input_schema": SCHEMA}]
    assert body["tool_choice"] == {"type": "tool", "name": "classify_reply"}


@pytest.mark.parametrize("body, exc", [
    (anthropic_body(stop_reason="max_tokens"), LLMTruncated),
    (anthropic_body(content=[{"type": "text", "text": "interested"}], stop_reason="end_turn"), LLMBadOutput),
    (anthropic_body(content=[], stop_reason="refusal"), LLMBadOutput),
    (anthropic_body(content=[{"type": "tool_use", "id": "t", "name": "other_tool", "input": {}}]), LLMBadOutput),
    (anthropic_body(content=[{"type": "tool_use", "id": "t", "name": "classify_reply", "input": "nope"}]),
     LLMBadOutput),
])
def test_anthropic_bad_outputs(body, exc):
    router = respx.Router(assert_all_called=False)
    router.post(ANTHROPIC_URL).respond(200, json=body)
    with pytest.raises(exc):
        call(anthropic_llm(router))


def test_anthropic_500_after_retries(no_retry_sleep):
    router = respx.Router(assert_all_called=False)
    route = router.post(ANTHROPIC_URL).respond(500, json={"type": "error", "error": {"type": "api_error",
                                                                                     "message": "boom"}})
    with pytest.raises(LLMUnavailable):
        call(anthropic_llm(router))
    assert route.call_count == 3  # first try + 2 retries


def test_anthropic_timeout(no_retry_sleep):
    router = respx.Router(assert_all_called=False)
    route = router.post(ANTHROPIC_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(LLMUnavailable):
        call(anthropic_llm(router))
    assert route.call_count == 3


def test_anthropic_401_is_unavailable_without_key():
    router = respx.Router(assert_all_called=False)
    router.post(ANTHROPIC_URL).respond(401, json={"type": "error", "error": {
        "type": "authentication_error", "message": f"invalid x-api-key {KEY}"}})
    with pytest.raises(LLMUnavailable) as ei:
        call(anthropic_llm(router))
    assert KEY not in str(ei.value) and "401" in str(ei.value)


# ---------------------------------------------------------------- OpenAI family

OPENAI_FAMILY = {
    "openai": (dict(llm_provider="openai"), OPENAI_URL, None),
    "azure_openai": (dict(llm_provider="azure_openai", llm_base_url=AZURE_ENDPOINT,
                          azure_openai_api_version=AZURE_VERSION), AZURE_URL, {"api-version": AZURE_VERSION}),
    "openai_compatible": (dict(llm_provider="openai_compatible", llm_base_url=COMPAT_BASE), COMPAT_URL, None),
}


@pytest.fixture(params=list(OPENAI_FAMILY))
def family(request):
    over, url, params = OPENAI_FAMILY[request.param]
    router = respx.Router(assert_all_called=False)

    def route(**kw: Any) -> Any:
        return router.post(url, params=params) if params else router.post(url)

    llm = make_llm(make_settings(llm_api_key=KEY, **over), http_client=mock_client(router))
    return request.param, llm, route


def oa_body(arguments: Any = '{"label": "interested", "note": null}', finish: str = "tool_calls",
            tool_calls: Any = "default", refusal: Optional[str] = None, name: str = "classify_reply") -> Dict[str, Any]:
    if tool_calls == "default":
        tool_calls = [{"id": "call_1", "type": "function", "function": {"name": name, "arguments": arguments}}]
    return {"id": "c1", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "finish_reason": finish,
                         "message": {"role": "assistant", "content": None, "refusal": refusal,
                                     "tool_calls": tool_calls}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


def test_openai_family_happy_path_and_request_shape(family):
    name, llm, route = family
    r = route().respond(200, json=oa_body())
    assert call(llm) == {"label": "interested", "note": None}  # arguments JSON string parsed
    req = r.calls.last.request
    if name == "azure_openai":
        assert req.headers["api-key"] == KEY
    else:
        assert req.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(req.content)
    assert body["messages"] == [{"role": "system", "content": SYSTEM}, {"role": "user", "content": USER_TEXT}]
    assert body["tools"] == [{"type": "function", "function": {
        "name": "classify_reply", "description": "Record it.", "parameters": SCHEMA}}]
    assert body["tool_choice"] == {"type": "function", "function": {"name": "classify_reply"}}
    if name == "openai_compatible":
        assert body["max_tokens"] == 321 and "max_completion_tokens" not in body
    else:  # OpenAI / Azure: max_tokens is deprecated and rejected by reasoning models
        assert body["max_completion_tokens"] == 321 and "max_tokens" not in body
    assert body["model"] == "dep-classifier"
    assert "strict" not in json.dumps(body["tools"])


def test_openai_family_arguments_object_accepted(family):
    # some OpenAI-compatible servers return arguments as an object instead of a JSON string
    _name, llm, route = family
    route().respond(200, json=oa_body(arguments={"label": "ooo"}))
    assert call(llm) == {"label": "ooo"}


@pytest.mark.parametrize("body, exc", [
    (oa_body(finish="length"), LLMTruncated),
    (oa_body(tool_calls=None, finish="stop"), LLMBadOutput),          # no tool call
    (oa_body(tool_calls=[], finish="stop"), LLMBadOutput),
    (oa_body(tool_calls=None, refusal="I can't help with that.", finish="stop"), LLMBadOutput),  # refusal
    (oa_body(arguments='{"label": "inter'), LLMBadOutput),            # invalid JSON
    (oa_body(arguments='["interested"]'), LLMBadOutput),              # JSON, not an object
    (oa_body(arguments="null"), LLMBadOutput),
    (oa_body(name="other_tool"), LLMBadOutput),
])
def test_openai_family_bad_outputs(family, body, exc):
    _name, llm, route = family
    route().respond(200, json=body)
    with pytest.raises(exc):
        call(llm)


def test_openai_family_500_after_retries(family, no_retry_sleep):
    _name, llm, route = family
    r = route().respond(500, json={"error": {"message": "boom", "type": "server_error"}})
    with pytest.raises(LLMUnavailable):
        call(llm)
    assert r.call_count == 3


def test_openai_family_429_after_retries(family, no_retry_sleep):
    _name, llm, route = family
    r = route().respond(429, json={"error": {"message": "slow down", "type": "rate_limit"}})
    with pytest.raises(LLMUnavailable):
        call(llm)
    assert r.call_count == 3


def test_openai_family_timeout(family, no_retry_sleep):
    _name, llm, route = family
    r = route().mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(LLMUnavailable):
        call(llm)
    assert r.call_count == 3


def test_openai_family_401_is_unavailable_without_key(family):
    _name, llm, route = family
    route().respond(401, json={"error": {"message": f"Incorrect API key provided: {KEY}",
                                         "type": "invalid_request_error", "code": "invalid_api_key"}})
    with pytest.raises(LLMUnavailable) as ei:
        call(llm)
    assert KEY not in str(ei.value) and "401" in str(ei.value)


def test_openai_family_400_is_llm_error(family):
    _name, llm, route = family
    route().respond(400, json={"error": {"message": f"bad model, prompt was {USER_TEXT}"}})
    with pytest.raises(LLMError) as ei:
        call(llm)
    assert USER_TEXT not in str(ei.value)


# ---------------------------------------------------------------- logs and error strings

SECRETS = (KEY, "SYSTEM-PROMPT-TEXT", "PROSPECT-MESSAGE-TEXT", "ozoo-res-secret", "llm-gw-secret")


@pytest.mark.parametrize("provider", ["anthropic", *OPENAI_FAMILY])
@pytest.mark.parametrize("status", [200, 401, 500, 503])
def test_no_key_url_or_text_in_logs_or_errors(provider, status, caplog, no_retry_sleep):
    caplog.set_level(logging.DEBUG)
    router = respx.Router(assert_all_called=False)
    if provider == "anthropic":
        over, url, params = dict(llm_provider="anthropic"), ANTHROPIC_URL, None
        ok = anthropic_body()
    else:
        over, url, params = OPENAI_FAMILY[provider]
        ok = oa_body()
    route = router.post(url, params=params) if params else router.post(url)
    err = {"error": {"message": f"echo {KEY} {USER_TEXT} {SYSTEM}", "type": "x"}}
    route.respond(status, json=ok if status == 200 else err)
    llm = make_llm(make_settings(llm_api_key=KEY, **over), http_client=mock_client(router))
    raised = ""
    try:
        call(llm)
    except LLMError as exc:
        raised = f"{exc!s} {exc!r}"
    assert (status == 200) == (raised == "")
    for secret in SECRETS:
        assert secret not in caplog.text, secret
        assert secret not in raised, secret


def test_error_hierarchy():
    for cls in (LLMTruncated, LLMBadOutput, LLMUnavailable):
        assert issubclass(cls, LLMError)
    assert issubclass(LLMError, Exception)
