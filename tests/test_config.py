from __future__ import annotations

import json

import pytest

REQUIRED = {
    "DATABASE_URL": "sqlite:///:memory:",
    "LINK_SECRET": "s",
    "LINK_BASE_URL": "https://go.example.com",
    "LINK_TARGETS": json.dumps({"booking": "https://cal.com/x", "pricing": "https://ozoo.ai/p"}),
    "TOOL_NAME": "instantly",
    "TOOL_API_KEY": "k",
    "TOOL_WEBHOOK_SECRET": "w",
    "CAMPAIGN_IDS": json.dumps({"in_a": "c1"}),
    "DEFAULT_TZ_GROUP": "in_a",
    "ANTHROPIC_API_KEY": "a",
    "ZEROBOUNCE_API_KEY": "z",
    "INTAKE_API_KEY": "i",
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
