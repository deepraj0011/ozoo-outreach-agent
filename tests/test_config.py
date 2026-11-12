from __future__ import annotations

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
    "ANTHROPIC_API_KEY": "a",
    "ZEROBOUNCE_API_KEY": "z",
    "INTAKE_API_KEY": "I" * 30,
    "CAL_WEBHOOK_SECRET": "c",
    "CAL_BOOKING_URL": "https://cal.com/x",
    "SLACK_WEBHOOK_URL": "https://hooks.slack.com/x",
    "SLACK_SIGNING_SECRET": "ss",
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
    assert s.classifier_model == "claude-sonnet-5-5"
    assert s.chatbot_model == "claude-sonnet-5-5"
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
    "TOOL_WEBHOOK_SECRET", "CAL_WEBHOOK_SECRET", "SLACK_SIGNING_SECRET", "WA_APP_SECRET",
    "WA_VERIFY_TOKEN", "INTAKE_API_KEY", "TOOL_API_KEY", "ANTHROPIC_API_KEY",
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
