"""Local run mode settings (Task 13): per-service fake/real modes, production guard, timings."""
from __future__ import annotations

import base64
from datetime import timedelta

import pytest
from pydantic import ValidationError

from outreach.config import Settings
from tests.fakes import make_settings

MODES = ("email_mode", "whatsapp_mode", "llm_mode", "alerts_mode", "verify_mode")

# What scripts/local_setup writes: only the generated secrets and local ids; every provider
# credential is blank.
LOCAL = dict(
    app_env="local",
    database_url="sqlite://",
    link_secret="s" * 43,
    link_base_url="http://127.0.0.1:8000",
    link_targets={"booking": "https://example.com/book", "pricing": "https://example.com/pricing"},
    tool_name="instantly",
    tool_webhook_secret="tool-secret",
    campaign_ids={"default": "local-campaign"},
    default_tz_group="default",
    intake_api_key="i" * 43,
    cal_webhook_secret="cal-secret",
    cal_booking_url="https://example.com/book",
    teams_outgoing_secret=base64.b64encode(b"x" * 32).decode(),
    teams_tenant_id="local-tenant",
    teams_sales_channel_id="19:local@thread.tacv2",
    teams_allowed_aad_ids="local-salesperson",
    wa_app_secret="wa-app-secret",
    wa_verify_token="wa-verify",
)


def local(**over) -> Settings:
    return Settings(_env_file=None, **{**LOCAL, **over})


def test_local_defaults_every_service_to_fake_with_blank_credentials():
    s = local()
    assert [getattr(s, m) for m in MODES] == ["fake"] * 5
    assert s.tool_api_key == "" and s.zerobounce_api_key == "" and s.wa_access_token == ""
    assert s.llm_provider is None and s.teams_webhook_url == ""


def test_production_is_the_default_and_defaults_every_service_to_real():
    s = make_settings()
    assert s.app_env == "production"
    assert [getattr(s, m) for m in MODES] == ["real"] * 5


@pytest.mark.parametrize("mode", MODES)
def test_fake_mode_never_allowed_in_production(mode):
    with pytest.raises(ValidationError, match="fake"):
        make_settings(**{mode: "fake"})


def test_production_rejects_time_scale():
    with pytest.raises(ValidationError, match="time_scale"):
        make_settings(time_scale=60)


@pytest.mark.parametrize("mode,missing", [
    ("email_mode", "tool_api_key"),
    ("verify_mode", "zerobounce_api_key"),
    ("whatsapp_mode", "wa_access_token"),
    ("whatsapp_mode", "wa_phone_number_id"),
    ("alerts_mode", "teams_webhook_url"),
    ("llm_mode", "llm_provider"),
])
def test_real_mode_requires_its_credentials(mode, missing):
    with pytest.raises(ValidationError, match=missing):
        local(**{mode: "real"})


def test_mixing_real_llm_with_fake_email():
    s = local(llm_mode="real", llm_provider="openai_compatible", llm_base_url="http://localhost:11434/v1",
              llm_classifier_model="llama3", llm_chat_model="llama3")
    assert s.llm_mode == "real" and s.email_mode == "fake"


def test_real_llm_still_runs_the_provider_checks():
    with pytest.raises(ValidationError, match="llm_api_key"):
        local(llm_mode="real", llm_provider="openai", llm_classifier_model="m", llm_chat_model="m")


def test_real_llm_needs_model_names():
    with pytest.raises(ValidationError, match="llm_classifier_model"):
        local(llm_mode="real", llm_provider="openai", llm_api_key="k", llm_chat_model="m")


def test_blank_mode_from_env_means_default(monkeypatch):
    s = local(email_mode="", alerts_mode="real", teams_webhook_url="https://x.example.com/hook?sig=1")
    assert s.email_mode == "fake" and s.alerts_mode == "real"


def test_generated_secrets_still_required_locally():
    with pytest.raises(ValidationError, match="link_secret"):
        local(link_secret="")
    with pytest.raises(ValidationError, match="wa_app_secret"):
        local(wa_app_secret=" ")


def test_production_secret_checks_unchanged():
    with pytest.raises(ValidationError, match="tool_api_key"):
        make_settings(tool_api_key=" ")
    with pytest.raises(ValidationError, match="teams_webhook_url"):
        make_settings(teams_webhook_url="http://insecure.example.com")


def test_timings_default_to_the_spec_values():
    s = make_settings()
    assert s.delay("click_settle") == timedelta(seconds=30)
    assert s.delay("booking_nudge_after") == timedelta(minutes=30)
    assert s.delay("wa_reminder_after") == timedelta(hours=48)
    assert s.delay("wa_no_response_after") == timedelta(hours=48)
    assert s.delay("end_of_sequence_wait") == timedelta(days=5)


def test_time_scale_and_overrides_locally():
    s = local(time_scale=60, wa_reminder_after_s=120)
    assert s.delay("wa_reminder_after") == timedelta(seconds=2)
    assert s.delay("end_of_sequence_wait") == timedelta(days=5) / 60


def test_time_scale_must_be_positive():
    with pytest.raises(ValidationError):
        local(time_scale=0)


DELAYS = ("click_settle_s", "booking_nudge_after_s", "wa_reminder_after_s", "wa_no_response_after_s",
          "end_of_sequence_wait_s")


@pytest.mark.parametrize("field", DELAYS)
def test_production_refuses_a_delay_shorter_than_the_default(field):
    with pytest.raises(ValidationError, match=field):
        make_settings(**{field: 1})


@pytest.mark.parametrize("field", DELAYS)
def test_production_allows_a_longer_delay_and_local_allows_shorter(field):
    longer = make_settings(**{field: 10 * 86400})
    assert getattr(longer, field) == 10 * 86400
    assert getattr(local(**{field: 1}), field) == 1
