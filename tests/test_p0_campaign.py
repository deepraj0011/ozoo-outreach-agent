"""P0 campaign Start / Pause: nothing is handed to the sender before Start."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from outreach.models import Event, P0Campaign
from outreach.p0 import campaign, flow

from .conftest import NOW
from .test_p0_console import build
from .test_p0_flow import S, clock, rows, start, tool, tool_status  # noqa: F401

LATER = NOW + timedelta(days=2)


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(campaign, "FRESH_RUNNING", False)


def test_nothing_goes_before_start_then_waiting_leads_start_at_start(session, tool, fresh):
    lead = start(session, tool)
    assert not campaign.is_running(session)
    assert not flow.dispatch_due(session, lead, tool, NOW, settings=S)
    assert [r.status for r in rows(session, lead)] == ["planned"] * 4

    out = campaign.start(session, tool, "pm", LATER, settings=S)

    assert out["running"] and out["started_at"] == LATER.isoformat() and out["leads"] == 1
    assert rows(session, lead)[0].due_at == LATER  # day 0 moved to the start, no backlog
    assert flow.dispatch_due(session, lead, tool, LATER, settings=S)
    ev = session.scalars(select(Event).where(Event.kind == "p0_campaign")).one()
    assert ev.data == {"action": "start", "by": "pm", "leads": 1}


def test_send_now_still_works_before_start(session, tool, fresh):
    lead = start(session, tool)

    assert flow.send_now(session, lead, tool, "pm", NOW, settings=S)
    assert rows(session, lead)[0].status == "handed"
    assert not campaign.is_running(session)  # the campaign row was made before the hand-off


def test_upgraded_system_that_already_sent_keeps_running(session, tool, fresh):
    lead = start(session, tool)
    rows(session, lead)[0].status, rows(session, lead)[0].handed_at = "handed", NOW
    session.flush()

    assert campaign.is_running(session)
    assert session.get(P0Campaign, campaign.CAMPAIGN_ID).started_at == NOW


def test_pause_holds_the_sender_and_start_releases_it(session, tool):
    lead = start(session, tool)
    assert flow.dispatch_due(session, lead, tool, NOW, settings=S)

    campaign.pause(session, tool, "pm", NOW + timedelta(minutes=1))
    assert tool_status(tool, lead) == "paused"
    rows(session, lead)[0].status = "sent"
    assert not flow.dispatch_due(session, lead, tool, LATER, settings=S)

    campaign.start(session, tool, "pm", LATER, settings=S)
    assert tool_status(tool, lead) == "active"
    email2 = rows(session, lead)[1].due_at  # a lead that had an email sent keeps its own day 0
    assert email2 > LATER and flow.dispatch_due(session, lead, tool, email2, settings=S)


def test_start_and_pause_routes_and_board(tmp_path, fresh):
    client, _ = build(tmp_path)
    assert client.get("/demo/api/p0/board").json()["campaign"]["running"] is False

    r = client.post("/demo/api/p0/campaign/start", json={"by": "pm"})
    assert r.status_code == 200 and r.json()["campaign"]["running"] is True
    assert client.post("/demo/api/p0/campaign/start", json={"by": "pm"}).status_code == 400
    assert client.post("/demo/api/p0/campaign/pause", json={"by": "pm"}).json()["campaign"]["running"] is False
    assert client.get("/demo/api/p0/board").json()["campaign"]["running"] is False
