from __future__ import annotations

import base64
import json
from datetime import timedelta

from outreach.config import MailboxConfig, Settings
from outreach.models import Alert, Event
from outreach.workspace.connectors import connectors
from tests.conftest import NOW
from tests.fakes import make_settings

GMAIL = dict(app_env="local", email_mode="gmail", gmail_address="a@ozoo.ai", gmail_app_password="x" * 16)


def _by(settings, session):
    return {c["id"]: c for c in connectors(settings, session, NOW)}


def test_order_and_groups(session):
    items = connectors(make_settings(), session, NOW)
    assert [c["id"] for c in items] == ["gmail", "mailboxes", "instantly", "cal", "google_calendar",
                                        "outlook_calendar", "whatsapp", "teams", "llm", "verify"]
    assert [c["group"] for c in items] == ["email"] * 3 + ["calendar"] * 3 + ["messaging"] * 4
    for c in items:
        assert set(c) == {"id", "group", "name", "account", "status", "detail", "basis", "setup"}
        assert set(c["setup"]) == {"steps", "env_keys"}


def test_gmail_mode_marks_only_gmail_active(session):
    by = _by(make_settings(**GMAIL), session)
    assert by["gmail"]["status"] == "active" and by["gmail"]["account"] == "a@ozoo.ai"
    assert by["mailboxes"]["status"] == "available" and by["instantly"]["status"] == "available"


def test_instantly_active_in_real_mode(session):
    by = _by(make_settings(), session)
    assert by["instantly"]["status"] == "active"
    assert by["gmail"]["status"] == "available"


def test_local_test_mode_has_no_active_email_card(session):
    items = connectors(make_settings(app_env="local", email_mode="fake"), session, NOW)
    assert all(c["status"] != "active" for c in items[:3])
    assert "Local test mode: emails go to Mailpit or memory" in items[0]["detail"]


def test_recent_mailbox_alert_needs_attention(session):
    session.add(Alert(kind="mailbox_unhealthy", text="x", created_at=NOW - timedelta(hours=1)))
    session.flush()
    assert _by(make_settings(**GMAIL), session)["gmail"]["status"] == "attention"


def test_old_or_resolved_mailbox_alert_ignored(session):
    session.add(Alert(kind="mailbox_paused", text="x", created_at=NOW - timedelta(hours=25)))
    session.add(Alert(kind="mailbox_paused", text="x", created_at=NOW - timedelta(hours=1),
                      resolved_at=NOW))
    session.flush()
    assert _by(make_settings(**GMAIL), session)["gmail"]["status"] == "active"


def test_cal_active_after_a_booking_event(session):
    session.add(Event(channel="cal", kind="booked", occurred_at=NOW - timedelta(days=2),
                      received_at=NOW - timedelta(days=2)))
    session.flush()
    cal = _by(make_settings(), session)["cal"]
    assert cal["status"] == "active" and "Last booking" in cal["detail"]


def test_cal_available_without_booking(session):
    cal = _by(make_settings(), session)["cal"]
    assert cal["status"] == "available" and cal["detail"] == "No booking received yet"


def test_calendars_are_coming_soon(session):
    by = _by(make_settings(), session)
    for key in ("google_calendar", "outlook_calendar"):
        assert by[key]["status"] == "coming_soon"
        assert by[key]["setup"] == {"steps": ["In-app sign-in is coming in the next update."],
                                    "env_keys": []}


def test_messaging_status_follows_mode(session):
    real = _by(make_settings(), session)
    assert all(real[k]["status"] == "active" for k in ("whatsapp", "teams", "llm", "verify"))
    fake = _by(make_settings(app_env="local"), session)
    assert all(fake[k]["status"] == "available" and "Local test mode" in fake[k]["detail"]
               for k in ("whatsapp", "teams", "llm", "verify"))


def test_teams_attention_on_stuck_alert(session):
    session.add(Alert(kind="reply", text="x", posted_at=None, created_at=NOW - timedelta(minutes=15)))
    session.flush()
    assert _by(make_settings(alerts_mode="real"), session)["teams"]["status"] == "attention"


def test_teams_ignores_integration_errors_and_fresh_alerts(session):
    session.add(Alert(kind="integration_error", text="x", created_at=NOW - timedelta(minutes=15)))
    session.add(Alert(kind="reply", text="x", created_at=NOW - timedelta(minutes=2)))
    session.flush()
    assert _by(make_settings(alerts_mode="real"), session)["teams"]["status"] == "active"


def test_account_is_non_secret_identity(session):
    by = _by(make_settings(), session)
    assert by["whatsapp"]["account"] == "Phone number set"
    assert by["teams"]["account"] == "Sales channel set"
    assert by["llm"]["account"] == "anthropic"


def test_env_keys_are_real_settings_fields(session):
    azure = make_settings(llm_mode="real", llm_provider="azure_openai", llm_api_key="k",
                          llm_base_url="https://x.openai.azure.com", azure_openai_api_version="2024-06-01")
    for s in (make_settings(), azure):
        for c in connectors(s, session, NOW):
            for key in c["setup"]["env_keys"]:
                assert key.isupper() and key.lower() in Settings.model_fields, key


def _llm_keys(session, **kw):
    by = {c["id"]: c for c in connectors(make_settings(**kw), session, NOW)}
    return by["llm"]["setup"]["env_keys"]


def test_llm_card_lists_the_key_and_azure_extras(session):
    azure = _llm_keys(session, llm_mode="real", llm_provider="azure_openai", llm_api_key="k",
                      llm_base_url="https://x.openai.azure.com", azure_openai_api_version="2024-06-01")
    assert {"LLM_API_KEY", "LLM_BASE_URL", "AZURE_OPENAI_API_VERSION"} <= set(azure)
    anth = _llm_keys(session, llm_mode="real", llm_provider="anthropic", llm_api_key="k")
    assert "LLM_API_KEY" in anth and "LLM_BASE_URL" not in anth
    compat = _llm_keys(session, llm_mode="real", llm_provider="openai_compatible",
                       llm_base_url="https://x.example.com/v1")
    assert "LLM_BASE_URL" in compat and "AZURE_OPENAI_API_VERSION" not in compat


def test_no_secret_reaches_the_payload(session):
    secret = "SENTINEL-SECRET-91f3"
    s = make_settings(gmail_app_password=secret, tool_api_key=secret, wa_access_token=secret,
                      cal_webhook_secret=secret, zerobounce_api_key=secret,
                      wa_phone_number_id=secret, wa_verify_token=secret, teams_webhook_url="https://x.test/" + secret,
                      llm_api_key=secret, reacher_secret=secret, teams_outgoing_secret=base64.b64encode((secret * 3).encode()).decode(), wa_app_secret=secret,
                      email_mode="mailbox", mailboxes=[MailboxConfig(address="a@x.test", password=secret)])
    payload = json.dumps(connectors(s, session, NOW))
    assert secret not in payload and base64.b64encode((secret * 3).encode()).decode() not in payload
    g = make_settings(**{**GMAIL, "gmail_app_password": secret})
    assert secret not in json.dumps(connectors(g, session, NOW))
