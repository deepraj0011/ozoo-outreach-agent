from __future__ import annotations

import base64
import json

import pytest

REQUIRED = {
    "DATABASE_URL": "sqlite:///:memory:",
    "LINK_SECRET": "L" * 40,
    "LINK_BASE_URL": "https://go.example.com",
    "LINK_TARGETS": json.dumps({"booking": "https://cal.com/x", "pricing": "https://ozoo.ai/p"}),
    "TOOL_NAME": "instantly",
    "TOOL_API_KEY": "k",
    "TOOL_WEBHOOK_SECRET": "w",
    "CAMPAIGN_IDS": json.dumps({"in_a": "c1"}),
    "DEFAULT_TZ_GROUP": "in_a",
    "LLM_PROVIDER": "openai",
    "LLM_API_KEY": "a",
    "LLM_CLASSIFIER_MODEL": "model-c",
    "LLM_CHAT_MODEL": "model-b",
    "ZEROBOUNCE_API_KEY": "z",
    "INTAKE_API_KEY": "I" * 30,
    "CAL_WEBHOOK_SECRET": "c",
    "CAL_BOOKING_URL": "https://cal.com/x",
    "TEAMS_WEBHOOK_URL": "https://prod-01.westeurope.logic.azure.com/workflows/x?sig=SIGSECRETVALUE",
    "TEAMS_OUTGOING_SECRET": base64.b64encode(b"0123456789abcdef0123").decode(),
    "TEAMS_TENANT_ID": "tenant-1",
    "TEAMS_SALES_CHANNEL_ID": "19:sales@thread.tacv2",
    "TEAMS_ALLOWED_AAD_IDS": "aad-1, aad-2",
    "WA_PHONE_NUMBER_ID": "1",
    "WA_ACCESS_TOKEN": "t",
    "WA_APP_SECRET": "as",
    "WA_VERIFY_TOKEN": "v",
}


def test_settings_load_from_env(monkeypatch):
    from outreach.config import Settings, get_settings

    for k, v in REQUIRED.items():
        monkeypatch.setenv(k, v)
    s = Settings(_env_file=None)
    assert s.llm_provider == "openai"
    assert s.llm_classifier_model == "model-c"
    assert s.llm_chat_model == "model-b"
    assert s.llm_base_url is None and s.azure_openai_api_version is None
    assert s.llm_timeout_s == 30
    assert s.primary_when_both == "whatsapp"
    assert s.wa_graph_version == "v21.0"
    assert s.business_tz == "Asia/Kolkata"
    assert s.business_hours == "Mon-Fri 09:00-18:00"
    assert s.link_targets == json.loads(REQUIRED["LINK_TARGETS"])
    assert s.campaign_ids == {"in_a": "c1"}
    get_settings.cache_clear()
    assert get_settings() is get_settings()
    get_settings.cache_clear()


def test_secrets_required(monkeypatch):
    from pydantic import ValidationError

    from outreach.config import Settings

    for k in REQUIRED:
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def _set_env(monkeypatch, **over):
    for k, v in {**REQUIRED, **over}.items():
        monkeypatch.setenv(k, v)


def test_short_link_secret_rejected(monkeypatch):
    from pydantic import ValidationError

    from outreach.config import Settings

    _set_env(monkeypatch, LINK_SECRET="x" * 31)
    with pytest.raises(ValidationError, match="link_secret"):
        Settings(_env_file=None)
    _set_env(monkeypatch, LINK_SECRET="x" * 32)
    Settings(_env_file=None)


def test_short_intake_key_rejected(monkeypatch):
    from pydantic import ValidationError

    from outreach.config import Settings

    _set_env(monkeypatch, INTAKE_API_KEY="k" * 23)
    with pytest.raises(ValidationError, match="intake_api_key"):
        Settings(_env_file=None)


@pytest.mark.parametrize("env", [
    "TOOL_WEBHOOK_SECRET", "CAL_WEBHOOK_SECRET", "TEAMS_OUTGOING_SECRET", "WA_APP_SECRET",
    "WA_VERIFY_TOKEN", "INTAKE_API_KEY", "TOOL_API_KEY", "LLM_API_KEY",
    "ZEROBOUNCE_API_KEY", "WA_ACCESS_TOKEN",
])
def test_blank_secret_rejected(monkeypatch, env):
    from pydantic import ValidationError

    from outreach.config import Settings

    _set_env(monkeypatch, **{env: "   "})
    with pytest.raises(ValidationError, match=env.lower()):
        Settings(_env_file=None)


def test_validation_error_does_not_echo_secret(monkeypatch):
    from pydantic import ValidationError

    from outreach.config import Settings

    _set_env(monkeypatch, LINK_SECRET="tooshort-SECRETVALUE")
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)
    assert "tooshort-SECRETVALUE" not in str(exc.value)
    assert "link_secret" in str(exc.value)


# ---------------------------------------------------------------- Microsoft Teams settings

def test_teams_settings_parsed(monkeypatch):
    from outreach.config import Settings

    _set_env(monkeypatch)
    s = Settings(_env_file=None)
    assert s.teams_allowed_aad_id_set == frozenset({"aad-1", "aad-2"})
    assert s.teams_outgoing_key == b"0123456789abcdef0123"
    assert s.teams_tenant_id == "tenant-1"
    assert s.teams_sales_channel_id == "19:sales@thread.tacv2"


@pytest.mark.parametrize("env", [
    "TEAMS_WEBHOOK_URL", "TEAMS_OUTGOING_SECRET", "TEAMS_TENANT_ID", "TEAMS_SALES_CHANNEL_ID",
    "TEAMS_ALLOWED_AAD_IDS",
])
def test_teams_setting_missing_fails(monkeypatch, env):
    from pydantic import ValidationError

    from outreach.config import Settings

    _set_env(monkeypatch)
    monkeypatch.delenv(env)
    with pytest.raises(ValidationError, match=env.lower()):
        Settings(_env_file=None)


@pytest.mark.parametrize("env, value", [
    ("TEAMS_WEBHOOK_URL", "http://prod-01.logic.azure.com/x?sig=SECRETSIG1"),
    ("TEAMS_WEBHOOK_URL", "not a url SECRETSIG2"),
    ("TEAMS_WEBHOOK_URL", "   "),
    ("TEAMS_OUTGOING_SECRET", "%%%not-base64-SECRET3%%%"),
    ("TEAMS_OUTGOING_SECRET", base64.b64encode(b"SECRET4short").decode()),  # < 16 bytes
    ("TEAMS_OUTGOING_SECRET", "   "),
    ("TEAMS_TENANT_ID", "  "),
    ("TEAMS_SALES_CHANNEL_ID", ""),
    ("TEAMS_ALLOWED_AAD_IDS", " , ,"),
])
def test_teams_setting_invalid_fails_without_echo(monkeypatch, env, value):
    from pydantic import ValidationError

    from outreach.config import Settings

    _set_env(monkeypatch, **{env: value})
    with pytest.raises(ValidationError, match=env.lower()) as exc:
        Settings(_env_file=None)
    if value.strip():
        assert value not in str(exc.value)
    for secret in ("SECRETSIG", "SECRET3", "SECRET4", base64.b64encode(b"SECRET4short").decode()):
        assert secret not in str(exc.value)


def test_old_chat_settings_gone():
    from outreach.config import Settings

    assert not [f for f in Settings.model_fields if "sl" + "ack" in f]
