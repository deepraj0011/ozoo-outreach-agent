"""Web app campaign board: GET /demo/api/p0/board and one email's exact copy."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import sessionmaker

from outreach.models import Event, Lead, PlannedEmail
from outreach.p0 import planner

from .conftest import NOW, make_lead
from .fakes import make_settings
from .test_p0_console import build


def _seed(engine, **lead_kw):
    with sessionmaker(engine)() as s:
        lead = make_lead(s, active_channel="email", tool_lead_id=f"t-{lead_kw.pop('tag', 'a')}", **lead_kw)
        planner.plan_lead(s, lead, NOW - timedelta(days=5), settings=make_settings(email_flow="p0"))
        s.commit()
        return lead.id


def _mark_first_sent(engine, lead_id):
    with sessionmaker(engine)() as s:
        row = s.query(PlannedEmail).filter_by(lead_id=lead_id, email_no=1).one()
        row.status, row.sent_at = "sent", NOW - timedelta(days=5)
        row.sent_subject, row.sent_text = "Stored subject", "Stored text"
        s.add(Event(lead_id=lead_id, channel="email", kind="open", step=1, verdict="human",
                    occurred_at=NOW - timedelta(days=4), received_at=NOW - timedelta(days=4)))
        s.commit()


def test_board_lists_p0_leads_with_cells_and_totals(tmp_path):
    client, engine = build(tmp_path)
    client.get("/demo/api/p0/schedule")  # starter templates + schedule
    lead_id = _seed(engine, tag="a")
    _mark_first_sent(engine, lead_id)
    with sessionmaker(engine)() as s:
        make_lead(s, name="Classic only")  # no p0 flag: not on the board
        s.commit()

    got = client.get("/demo/api/p0/board").json()

    assert [r["day"] for r in got["schedule"]] == [0, 4, 9, 15]
    assert all(r["template_name"] for r in got["schedule"])
    assert len(got["leads"]) == 1
    lead = got["leads"][0]
    assert lead["id"] == lead_id and lead["stop_reason"] is None and lead["paused"] is False
    cells = {c["email_no"]: c for c in lead["emails"]}
    assert cells[1]["status"] == "sent" and cells[1]["opens"] == 1
    assert cells[2]["status"] in ("planned", "today")
    assert got["totals"]["leads"] == 1 and got["totals"]["sent"] == 1


def test_board_shows_reply_stop(tmp_path):
    client, engine = build(tmp_path)
    client.get("/demo/api/p0/schedule")
    lead_id = _seed(engine, tag="b")
    with sessionmaker(engine)() as s:
        lead = s.get(Lead, lead_id)
        lead.state = "engaged"
        planner.cancel_remaining(s, lead, "reply", NOW)
        s.add(Event(lead_id=lead_id, channel="email", kind="reply", verdict="human",
                    occurred_at=NOW, received_at=NOW))
        s.commit()

    got = client.get("/demo/api/p0/board").json()

    lead = got["leads"][0]
    assert lead["stop_reason"] == "replied" and lead["stopped_at"]
    assert {c["status"] for c in lead["emails"]} == {"cancelled"}
    assert got["totals"]["replied"] == 1 and got["totals"]["stopped"] == 1


def test_email_detail_sent_and_planned(tmp_path):
    client, engine = build(tmp_path)
    client.get("/demo/api/p0/schedule")
    lead_id = _seed(engine, tag="c")
    _mark_first_sent(engine, lead_id)

    sent = client.get(f"/demo/api/p0/leads/{lead_id}/emails/1").json()
    planned = client.get(f"/demo/api/p0/leads/{lead_id}/emails/2").json()

    assert sent["subject"] == "Stored subject" and sent["text"] == "Stored text"
    assert sent["status"] == "sent" and sent["opens"] == 1
    assert sent["html_is_current_template"] is True and "<" in sent["html"]
    assert planned["status"] == "planned" and planned["subject"] and planned["text"]
    assert planned["html_is_current_template"] is False
    assert "/r/" not in planned["html"]  # preview links, never the lead's tracked ones
    assert client.get(f"/demo/api/p0/leads/{lead_id}/emails/99").status_code == 404
    assert client.get("/demo/api/p0/leads/9999/emails/1").status_code == 404


def test_board_absent_outside_local(tmp_path):
    client, _ = build(tmp_path, app_env="production")
    assert client.get("/demo/api/p0/board").status_code == 404
