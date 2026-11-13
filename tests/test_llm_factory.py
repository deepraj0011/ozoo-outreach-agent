"""make_llm picks the provider from settings; LLM settings validate without echoing values;
business logic stays provider-neutral."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from outreach.llm import LLMClient, make_llm
from outreach.llm.anthropic_provider import AnthropicLLM
from outreach.llm.factory import MAX_RETRIES
from outreach.llm.fake import FakeLLM
from outreach.llm.openai_provider import OpenAILLM
from tests.fakes import make_settings
from tests.llm_http import no_retry_sleep  # noqa: F401 - fixture

SRC = Path(__file__).resolve().parents[1] / "src" / "outreach"
KEY = "sk-FACTORY-SECRET-999"


def test_anthropic_selected():
    llm = make_llm(make_settings(llm_provider="anthropic", llm_api_key=KEY, llm_timeout_s=12.5))
    assert isinstance(llm, AnthropicLLM)
    client = llm._client
    assert client.api_key == KEY
    assert str(client.base_url).rstrip("/") == "https://api.anthropic.com"
    assert client.max_retries == MAX_RETRIES == 2
    assert client.timeout == 12.5


def test_openai_selected_with_default_base_url():
    llm = make_llm(make_settings(llm_provider="openai", llm_api_key=KEY))
    assert isinstance(llm, OpenAILLM)
    from openai import AzureOpenAI, OpenAI

    assert type(llm._client) is OpenAI and not isinstance(llm._client, AzureOpenAI)
    assert str(llm._client.base_url).rstrip("/") == "https://api.openai.com/v1"
    assert llm._client.max_retries == 2
    assert llm._client.timeout == 30


def test_openai_ignores_openai_base_url_env(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://evil.example.com/v1")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://evil.example.com")
    assert "evil" not in str(make_llm(make_settings(llm_provider="openai"))._client.base_url)
    assert "evil" not in str(make_llm(make_settings(llm_provider="anthropic"))._client.base_url)


def test_azure_selected():
    from openai import AzureOpenAI

    llm = make_llm(make_settings(llm_provider="azure_openai", llm_api_key=KEY,
                                 llm_base_url="https://res.openai.azure.com",
                                 azure_openai_api_version="2024-10-21"))
    assert isinstance(llm, OpenAILLM)
    assert isinstance(llm._client, AzureOpenAI)
    assert llm._client.max_retries == 2


def test_openai_compatible_selected():
    llm = make_llm(make_settings(llm_provider="openai_compatible", llm_api_key="",
                                 llm_base_url="http://localhost:11434/v1"))
    assert isinstance(llm, OpenAILLM)
    assert str(llm._client.base_url).rstrip("/") == "http://localhost:11434/v1"
    assert llm._client.max_retries == 2


def test_providers_satisfy_protocol():
    assert isinstance(FakeLLM({}), LLMClient)
    assert isinstance(make_llm(make_settings()), LLMClient)


# ---------------------------------------------------------------- config validation

@pytest.mark.parametrize("over, field", [
    (dict(llm_provider="azure_openai", azure_openai_api_version="2024-10-21"), "llm_base_url"),
    (dict(llm_provider="azure_openai", llm_base_url="https://res.openai.azure.com"), "azure_openai_api_version"),
    (dict(llm_provider="openai_compatible"), "llm_base_url"),
    (dict(llm_provider="openai_compatible", llm_base_url="   "), "llm_base_url"),
    (dict(llm_provider="anthropic", llm_api_key="  "), "llm_api_key"),
    (dict(llm_provider="openai", llm_api_key=""), "llm_api_key"),
    (dict(llm_provider="azure_openai", llm_api_key="", llm_base_url="https://r.openai.azure.com",
          azure_openai_api_version="v"), "llm_api_key"),
    (dict(llm_classifier_model="  "), "llm_classifier_model"),
    (dict(llm_chat_model=""), "llm_chat_model"),
    (dict(llm_timeout_s=0), "llm_timeout_s"),
])
def test_llm_config_errors(over, field):
    with pytest.raises(ValidationError, match=field):
        make_settings(**over)


@pytest.mark.parametrize("provider, url", [
    ("openai_compatible", "http://ollama-SECRET.example.com/v1"),   # public host over http
    ("openai_compatible", "http://8.8.8.8/v1"),
    ("openai_compatible", "ftp://localhost/SECRET"),
    ("openai_compatible", "not a url SECRET"),
    ("azure_openai", "http://localhost:8080/SECRET"),                 # http only for self-hosted
    ("anthropic", "http://127.0.0.1/SECRET"),
    ("openai", "https:///SECRET"),
    # link-local and cloud metadata: rejected for every provider and scheme
    ("anthropic", "https://169.254.169.254/SECRET"),
    ("openai", "https://169.254.1.1/SECRET"),
    ("openai_compatible", "http://169.254.169.254/SECRET"),
    ("openai_compatible", "https://169.254.169.254/SECRET"),
    ("openai_compatible", "http://[fe80::1]/SECRET"),
    ("azure_openai", "https://[fe80::1%25eth0]/SECRET"),
    ("openai", "https://[fd00:ec2::254]/SECRET"),
    ("openai_compatible", "http://[fd00:ec2::254]/SECRET"),
    ("openai_compatible", "http://[::ffff:169.254.169.254]/SECRET"),
    ("openai_compatible", "http://2852039166/SECRET"),                # 169.254.169.254 as one number
    ("openai_compatible", "http://0xa9.0xfe.0xa9.0xfe/SECRET"),
    ("azure_openai", "https://metadata.google.internal/SECRET"),
    ("openai_compatible", "http://metadata.google.internal./SECRET"),
    ("openai_compatible", "http://METADATA/SECRET"),
    ("anthropic", "https://metadata/SECRET"),
])
def test_bad_base_url_rejected_without_echo(provider, url):
    with pytest.raises(ValidationError, match="llm_base_url") as ei:
        make_settings(llm_provider=provider, llm_base_url=url, azure_openai_api_version="2024-10-21")
    assert "SECRET" not in str(ei.value)
    assert url not in str(ei.value)


@pytest.mark.parametrize("url", [
    "http://localhost:11434/v1", "http://127.0.0.1:8000/v1", "http://10.0.0.5/v1", "http://192.168.1.2:11434",
    "http://172.16.3.4/v1", "http://[::1]:8000/v1", "http://[fd12:3456::1]:8000/v1", "http://ollama:11434/v1", "http://vllm.internal/v1",
    "https://api.groq.com/openai/v1", "https://generativelanguage.googleapis.com/v1beta/openai/",
])
def test_self_hosted_http_allowed_for_openai_compatible(url):
    assert make_settings(llm_provider="openai_compatible", llm_base_url=url).llm_base_url == url.strip()


def test_missing_provider_or_models_required(monkeypatch):
    from outreach.config import Settings

    base = make_settings().model_dump()
    for field in ("llm_provider", "llm_classifier_model", "llm_chat_model"):
        values = {k: v for k, v in base.items() if k != field}
        with pytest.raises(ValidationError, match=field):
            Settings(_env_file=None, **values)


def test_config_error_never_echoes_key():
    with pytest.raises(ValidationError) as ei:
        make_settings(llm_provider="azure_openai", llm_api_key=KEY)
    assert KEY not in str(ei.value)


def test_old_claude_settings_gone():
    from outreach.config import Settings

    for old in ("anthropic_api_key", "classifier_model", "chatbot_model"):
        assert old not in Settings.model_fields


# ---------------------------------------------------------------- provider-neutral business logic

@pytest.mark.parametrize("name", ["classifier.py", "chatbot.py", "handoff.py", "actions.py", "rules.py"])
def test_business_logic_has_no_vendor_names(name):
    text = (SRC / name).read_text(encoding="utf-8").lower()
    assert "anthropic" not in text and "openai" not in text


@pytest.mark.parametrize("name", ["classifier.py", "chatbot.py"])
def test_prompts_do_not_name_a_model_vendor(name):
    assert not re.search(r"\bclaude\b", (SRC / name).read_text(encoding="utf-8"), re.IGNORECASE)


def test_sdk_loggers_capped_after_lazy_import():
    import logging

    for name in ("anthropic", "openai"):
        logging.getLogger(name).setLevel(logging.DEBUG)
    make_llm(make_settings(llm_provider="anthropic"))
    make_llm(make_settings(llm_provider="openai"))
    for name in ("anthropic", "openai", "httpx2", "httpcore2"):
        assert logging.getLogger(name).level == logging.WARNING, name


def test_network_guard_blocks_real_sdk_client(no_retry_sleep, monkeypatch):
    """conftest's autouse guard: a real client (no injected http_client) never opens a socket."""
    import socket

    from outreach.llm import LLMError

    connects = []
    monkeypatch.setattr(socket.socket, "connect", lambda self, *a: connects.append(a))
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: connects.append(a))
    for provider, over in (("anthropic", {}), ("openai", {}),
                           ("openai_compatible", {"llm_base_url": "https://llm.example.net/v1"})):
        llm = make_llm(make_settings(llm_provider=provider, **over))
        with pytest.raises(LLMError):
            llm.structured(system="s", messages=[{"role": "user", "content": "hi"}], tool_name="t",
                           tool_description="d", schema={"type": "object", "properties": {}},
                           max_tokens=5, model="m")
    assert connects == []


def test_network_guard_is_active():
    import httpx2

    with pytest.raises(RuntimeError, match="network disabled in tests"):
        httpx2.Client().get("https://example.com/")
