from __future__ import annotations

import json
import logging

import httpx
import respx
from sqlalchemy import select

from outreach.alerts import SlackAlerter
from outreach.models import Alert as AlertRow
from tests.conftest import NOW, make_lead

URL = "https://hooks.slack.com/services/x"


def alerter(**kw):
    return SlackAlerter(URL, sleep=lambda s: None, clock=lambda: NOW, **kw)


@respx.mock
def test_send_writes_row_and_posts(session):
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    lead = make_lead(session, name="Asha Rao", company="Acme")
    alerter().send("hot_lead", "hot lead", lead, session=session)
    row = session.scalars(select(AlertRow)).one()
    assert (row.lead_id, row.kind, row.text, row.created_at) == (lead.id, "hot_lead", "hot lead", NOW)
    payload = json.loads(route.calls[0].request.content)
    assert payload == {"text": f"[hot_lead] lead #{lead.id} Asha Rao @ Acme: hot lead"}


@respx.mock
def test_session_from_lead_when_not_passed(session):
    respx.post(URL).mock(return_value=httpx.Response(200))
    lead = make_lead(session)
    alerter().send("question", "q", lead)
    assert session.scalars(select(AlertRow)).one().lead_id == lead.id


@respx.mock
def test_slack_failure_never_raises_and_no_pii_logged(session, caplog):
    respx.post(URL).mock(side_effect=httpx.ConnectError("down"))
    lead = make_lead(session, name="Secret Name", email="secret@acme.com")
    with caplog.at_level(logging.DEBUG):
        alerter().send("booked", "meeting booked", lead, session=session)
    assert session.scalars(select(AlertRow)).one().kind == "booked"
    assert "Secret Name" not in caplog.text and "secret@acme.com" not in caplog.text


@respx.mock
def test_slack_5xx_logged_not_raised(session):
    respx.post(URL).mock(return_value=httpx.Response(500))
    alerter().send("unmatched_reply", "from x", None, session=session)
    assert session.scalars(select(AlertRow)).one().lead_id is None


@respx.mock
def test_unmatched_lead_text(session):
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    alerter().send("unmatched_reply", "reply from unknown", None, session=session)
    assert json.loads(route.calls[0].request.content)["text"] == "[unmatched_reply] no lead: reply from unknown"
