"""P0 scheduled emails, task 1: the P0 template library and its starter templates."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from outreach.links import make_unsubscribe_url
from outreach.models import P0ScheduleRow, P0Template
from outreach.p0 import templates as p0
from outreach.sequencer import _values
from tests.conftest import NOW, make_lead
from tests.fakes import make_settings

S = make_settings()
GOOD = {"name": "Intro", "subject": "Hi {first_name}", "body": "Hello {first_name} at {company}"}


def test_save_creates_draft_and_records_by(session):
    tpl = p0.save_template(session, GOOD, "pm", NOW, settings=S)

    assert tpl.status == "draft"
    assert tpl.created_by == "pm" and tpl.updated_by == "pm"


def test_edit_keeps_status(session):
    tpl = p0.save_template(session, {**GOOD, "status": "live"}, "pm", NOW, settings=S)

    edited = p0.save_template(session, {**GOOD, "id": tpl.id, "name": "Renamed"}, "ann", NOW, settings=S)

    assert edited.id == tpl.id and edited.name == "Renamed"
    assert edited.status == "live" and edited.updated_by == "ann" and edited.created_by == "pm"


def test_unknown_placeholder_rejected(session):
    with pytest.raises(p0.P0Error, match=r"\{nickname\}"):
        p0.save_template(session, {**GOOD, "body": "Hi {nickname}"}, "pm", NOW, settings=S)


@pytest.mark.parametrize("change", [{"subject": "  "}, {"body": ""}, {"name": ""}])
def test_blank_fields_rejected(session, change):
    with pytest.raises(p0.P0Error):
        p0.save_template(session, {**GOOD, **change}, "pm", NOW, settings=S)


def test_render_fills_name_company_role_and_links(session):
    lead = make_lead(session, name="Asha Rao", company="Acme Finance", role="CTO")
    tpl = p0.save_template(session, {**GOOD, "body": "{first_name} {name} {company} {role} {booking_link}"},
                           "pm", NOW, settings=S)

    out = p0.render(lead, tpl, 1, settings=S)

    for part in ("Asha", "Asha Rao", "Acme Finance", "CTO", _values(lead, 1, S)["booking_link"]):
        assert part in out["text"]
    assert out["html"] != ""


def test_render_always_has_unsubscribe(session):
    lead = make_lead(session)
    tpl = p0.save_template(session, {**GOOD, "body": "Just a note"}, "pm", NOW, settings=S)

    out = p0.render(lead, tpl, 1, settings=S)

    token = make_unsubscribe_url(lead.id, 1, settings=S).rsplit("/u/", 1)[1]
    assert token in out["text"]


def test_cannot_switch_off_template_on_schedule(session):
    tpl = p0.save_template(session, {**GOOD, "status": "live"}, "pm", NOW, settings=S)
    session.add(P0ScheduleRow(email_no=1, template_id=tpl.id, day=0))
    session.flush()

    with pytest.raises(p0.P0Error, match="email 1"):
        p0.set_status(session, tpl.id, "off", "pm", NOW)


def test_ensure_starter_once(session):
    p0.ensure_starter(session, NOW, settings=S)
    p0.ensure_starter(session, NOW, settings=S)

    tpls = session.scalars(select(P0Template)).all()
    rows = session.scalars(select(P0ScheduleRow).order_by(P0ScheduleRow.email_no)).all()
    assert len(tpls) == 4 and all(t.status == "live" and t.created_by == "starter" for t in tpls)
    assert [r.day for r in rows] == [0, 4, 9, 15]
    assert [r.email_no for r in rows] == [1, 2, 3, 4]


def test_list_templates_shows_where_used_and_preview_works(session):
    p0.ensure_starter(session, NOW, settings=S)

    listed = p0.list_templates(session)
    out = p0.preview(session, S, template_id=listed[0]["id"])

    assert [t["used_by"] for t in listed] == [[1], [2], [3], [4]]
    assert out["subject"] and out["text"]


def test_every_starter_template_renders_without_leftover_markers(session):
    p0.ensure_starter(session, NOW, settings=S)
    lead = make_lead(session)

    for tpl in session.scalars(select(P0Template)).all():
        out = p0.render(lead, tpl, 1, settings=S)

        assert "[[" not in out["text"] and "{" not in out["text"], tpl.name
        assert out["subject"] and out["html"]
