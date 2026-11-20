"""Settings view for the Settings screen: plain-language, read-only, never a secret."""
from __future__ import annotations

import json

from outreach.workspace.settings_view import settings_view

from .fakes import make_settings


def _rows(view, section_id):
    section = next(s for s in view["sections"] if s["id"] == section_id)
    return {r["label"]: r["value"] for r in section["rows"]}


def test_sending_section_reads_business_hours():
    view = settings_view(make_settings(business_hours="Mon-Fri 09:30-17:30"))
    rows = {r["label"]: r["value"] for r in view["sections"][0]["rows"]}
    assert view["sections"][0]["id"] == "sending"
    assert rows["Working days"] == ["Mon", "Tue", "Wed", "Thu", "Fri"]
    assert rows["Working hours"] == "09:30 to 17:30"


def test_settings_view_has_no_secret():
    secret = "SENTINEL-SECRET-91f3"
    s = make_settings(gmail_app_password=secret, cal_webhook_secret=secret, tool_api_key=secret)
    assert secret not in json.dumps(settings_view(s))


def test_daily_limit_omitted_without_mailboxes():
    rows = _rows(settings_view(make_settings()), "sending")
    assert "Daily limit per mailbox" not in rows
    assert rows["Main channel when both are allowed"] in ("whatsapp", "email")


def test_alerts_and_safety_rows():
    view = settings_view(make_settings(app_env="local", alerts_mode="fake"))
    assert _rows(view, "alerts")["Alerts go to"] == "Console (local test)"
    safety = _rows(view, "safety")
    assert isinstance(safety["Only alert sales after a real visit"], bool)
    assert isinstance(safety["Confirm clicks with a script check"], bool)


def test_team_shows_only_a_count_never_ids():
    s = make_settings(teams_allowed_aad_ids="aad-one-xyz,aad-two-xyz,aad-three-xyz")
    view = settings_view(s)
    assert _rows(view, "team")["Sales people who can reply from Teams"] == "3 people"
    assert "aad-one-xyz" not in json.dumps(view)
