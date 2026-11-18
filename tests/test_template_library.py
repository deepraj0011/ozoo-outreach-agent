"""Task 22: templates managed in the web app (write, approve, switch off, preview, send by hand)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from outreach import template_library as lib
from outreach.app import create_app
from outreach.emailtool.fake import FakeEmailTool
from outreach.models import EmailTemplate, Event, Suppression
from outreach.sequencer import enroll, load_templates, render_step
from tests.conftest import NOW, make_lead
from tests.fakes import make_app_engine, make_deps, make_settings
from tests.fakes import TestSession

PASS = "pm-passcode-1234"
ST = make_settings(template_approver_passcode=PASS)

SEQ = {"name": "Collections: compliance example", "kind": "sequence", "step": 2, "variant": "standard",
       "angle": "collections", "subject": "A compliance example for {company}",
       "body": "Hi {first_name},\n\nCUSTOM-MARK {hook}\n\n> Use case: {usecase}\n\n[[image]]\n\n{ask}\n\n[[button]]"
               "\n\n{signature}\n\nUnsubscribe: {unsubscribe_link}",
       "rich": {"image": "compliance-checks", "image_link": "case_study", "button": "Book a demo"}}
ONE_OFF = {"name": "Event invite", "kind": "manual", "subject": "See you at the summit?",
           "body": "Hi {first_name},\n\nONE-OFF-MARK we'll be at the lending summit.\n\n[[button]]\n\n{signature}"
                   "\n\nUnsubscribe: {unsubscribe_link}", "rich": {"button": "Meet us there"}}


def save(session, data, by="Priya"):
    return lib.save_template(session, data, by, NOW, settings=ST)


def approve(session, key):
    return lib.set_status(session, key, "approved", "Priya", PASS, ST, NOW)


# ---------------------------------------------------------------- checking drafts
@pytest.mark.parametrize("change,message", [
    ({"name": ""}, "name"),
    ({"subject": "two\nlines"}, "one line"),
    ({"body": "Hi {first_name}, {secret_field} {unsubscribe_link} [[button]]"}, "unknown placeholder"),
    ({"body": "Hi {first_name} {unsubscribe_link} [[button]] {"}, "stray"),
    ({"body": "Hi {answer} {unsubscribe_link} [[button]]"}, "automatic replies"),
    ({"body": "Hi {first_name}, no links"}, "booking_link"),
    ({"body": "Hi {first_name} {booking_link}"}, "unsubscribe_link"),
    ({"step": 1}, "Email 1 stays plain"),
    ({"rich": {"image": "../../secret"}}, "listed pictures"),
    ({"rich": {}}, "choose a picture"),
    ({"rich": {"image": "qa-scorecard", "image_link": "evil"}}, "click on the picture"),
    ({"rich": {"image": "qa-scorecard", "button": "x" * 200}}, "too long"),
    ({"angle": "nobody"}, "role group"),
    ({"variant": "spying"}, "when this version"),
    ({"kind": "blast"}, "one-off"),
    ({"body": "x" * 7000}, "too long"),
])
def test_bad_drafts_are_refused_with_a_clear_message(change, message):
    with pytest.raises(lib.TemplateError, match=message):
        lib.clean_draft({**SEQ, **change}, settings=ST)


def test_attribute_access_in_placeholders_is_refused():
    with pytest.raises(lib.TemplateError):
        lib.clean_draft({**SEQ, "subject": "{company.__class__}"}, settings=ST)


def test_one_off_needs_no_booking_link_but_needs_unsubscribe():
    assert lib.clean_draft(ONE_OFF, settings=ST)["kind"] == "manual"
    with pytest.raises(lib.TemplateError, match="unsubscribe"):
        lib.clean_draft({**ONE_OFF, "body": "Hi {first_name}"}, settings=ST)


def test_a_name_is_required_for_every_change(session):
    with pytest.raises(lib.TemplateError, match="your name"):
        save(session, SEQ, by="  ")


# ---------------------------------------------------------------- lifecycle
def test_saved_templates_are_drafts_and_not_used_until_approved(session):
    row = save(session, SEQ)
    assert row.status == "draft" and row.created_by == "Priya"
    lead = make_lead(session, role="Head of Collections")
    tpl = lib.templates_for(session)
    assert row.key not in [c["key"] for c in tpl["_custom"]]
    approve(session, row.key)
    assert row.status == "approved" and row.approved_by == "Priya"
    tpl = lib.templates_for(session)
    seen = {render_step(make_lead(session, role="Head of Collections", name=f"P{i} X"), 2, templates=tpl,
                        settings=ST)["body_2"].count("CUSTOM-MARK") for i in range(30)}
    assert seen == {0, 1}  # one of the live versions: the custom one for some leads
    other = {render_step(make_lead(session, role="CTO", name=f"C{i} X"), 2, templates=tpl, settings=ST)["body_2"]
             for i in range(20)}
    assert not any("CUSTOM-MARK" in b for b in other)  # only for collections roles
    assert lead  # noqa


def test_editing_an_approved_template_puts_it_back_to_draft(session):
    row = save(session, SEQ)
    approve(session, row.key)
    save_again = lib.save_template(session, {**SEQ, "name": "Renamed"}, "Arun", NOW, settings=ST, key=row.key)
    assert save_again.status == "draft" and save_again.approved_by is None and save_again.updated_by == "Arun"


def test_builtin_templates_cannot_be_edited_only_switched(session):
    key = lib.list_templates(session)[0]["key"]
    with pytest.raises(lib.TemplateError, match="Duplicate"):
        lib.save_template(session, SEQ, "Priya", NOW, settings=ST, key=key)


def test_switching_off_a_builtin_version_removes_it_from_sending(session):
    t = load_templates()
    keys = [v["key"] for v in lib.list_templates(session) if v["step"] == 2 and v["variant"] == "standard"]
    for k in keys[:-1]:
        lib.set_status(session, k, "off", "Priya", PASS, ST, NOW)
    tpl = lib.templates_for(session, t)
    bodies = {render_step(make_lead(session, name=f"Z{i} Y"), 2, templates=tpl, settings=ST)["body_2"].split("\n")[2]
              for i in range(15)}
    assert len(bodies) == 1  # only the one live version is used
    with pytest.raises(lib.TemplateError, match="at least one live"):
        lib.set_status(session, keys[-1], "off", "Priya", PASS, ST, NOW)
    lib.set_status(session, keys[0], "approved", "Priya", PASS, ST, NOW)  # switch back on
    assert lib.find_version(session, keys[0])["status"] == "approved"


def test_passcode_is_required_and_checked(session):
    row = save(session, SEQ)
    with pytest.raises(lib.TemplateError, match="not right"):
        lib.set_status(session, row.key, "approved", "Priya", "guess", ST, NOW)
    with pytest.raises(lib.TemplateError, match="turned off"):
        lib.set_status(session, row.key, "approved", "Priya", PASS, make_settings(), NOW)


def test_enrolment_uses_approved_custom_versions(session):
    row = save(session, {**SEQ, "angle": "any"})
    approve(session, row.key)
    for k in [v["key"] for v in lib.list_templates(session) if v["step"] == 2 and v["variant"] == "standard"
              and v["source"] == "builtin"]:
        lib.set_status(session, k, "off", "Priya", PASS, ST, NOW)
    tool = FakeEmailTool()
    lead = make_lead(session, role="CTO")
    enroll(session, lead, tool, NOW, settings=ST)
    variables = [c for c in tool.calls if c[0] == "add_lead"][0][3]
    assert "CUSTOM-MARK" in variables["body_2"] and "compliance-checks.jpg" in variables["html_2"]
    assert variables["html_1"] == ""  # email 1 stays plain


# ---------------------------------------------------------------- preview
def test_preview_uses_real_pages_not_tracked_links(session):
    out = lib.preview(session, ST, draft=SEQ, role="Head of Collections", name="Ravi Kumar", company="Fino")
    assert "Hi Ravi," in out["text"] and "/r/" not in out["text"] + out["html"]
    assert "https://ozoo.ai/case" in out["html"] and out["angle"] == "collections"


def test_preview_of_a_builtin_version(session):
    key = lib.list_templates(session)[0]["key"]
    out = lib.preview(session, ST, key=key, role="CTO")
    assert out["subject"] and out["html"] == ""  # email 1: plain


# ---------------------------------------------------------------- sending by hand
def emailed_lead(session, **kw):
    kw.setdefault("state", "in_sequence")
    return make_lead(session, tool_lead_id=f"t{kw.pop('n', 1)}", step_sent=2, **kw)


def test_send_goes_in_thread_with_design_and_is_recorded(session):
    row = save(session, ONE_OFF)
    approve(session, row.key)
    tool = FakeEmailTool()
    lead = emailed_lead(session, name="Ravi Kumar")
    res = lib.send_manual(session, row.key, [lead.id], by="Priya", passcode=PASS, settings=ST, tool=tool, now=NOW)
    assert res == [{"lead_id": lead.id, "ok": True, "reason": None}]
    call = [c for c in tool.calls if c[0] == "reply"][0]
    assert call[1] == "t1" and "ONE-OFF-MARK" in call[2] and "Hi Ravi," in call[2] and "Meet us there" in call[3]
    ev = session.scalars(select(Event).where(Event.kind == "manual_email")).one()
    assert ev.processed and ev.data["by"] == "Priya" and ev.data["template"] == row.key


def test_send_skips_leads_that_cannot_receive_it(session):
    row = save(session, ONE_OFF)
    approve(session, row.key)
    ok = emailed_lead(session, n=1)
    closed = emailed_lead(session, n=2, state="closed")
    fresh = make_lead(session)
    unsub = emailed_lead(session, n=3)
    session.add(Suppression(value=unsub.email, kind="email", reason="unsubscribe", created_at=NOW))
    session.flush()
    res = {r["lead_id"]: r for r in lib.send_manual(session, row.key, [ok.id, closed.id, fresh.id, unsub.id, 999],
                                                    by="Priya", passcode=PASS, settings=ST, tool=FakeEmailTool(), now=NOW)}
    assert res[ok.id]["ok"] and not res[closed.id]["ok"] and "no email sent yet" in res[fresh.id]["reason"]
    assert res[unsub.id]["reason"] == "unsubscribed or blocked" and res[999]["reason"] == "no such lead"


def test_send_refuses_drafts_too_many_leads_and_bad_passcodes(session):
    row = save(session, ONE_OFF)
    lead = emailed_lead(session)
    with pytest.raises(lib.TemplateError, match="approved"):
        lib.send_manual(session, row.key, [lead.id], by="P", passcode=PASS, settings=ST, tool=FakeEmailTool(), now=NOW)
    approve(session, row.key)
    with pytest.raises(lib.TemplateError, match="At most"):
        lib.send_manual(session, row.key, list(range(1, 40)), by="P", passcode=PASS, settings=ST,
                        tool=FakeEmailTool(), now=NOW)
    with pytest.raises(lib.TemplateError, match="not right"):
        lib.send_manual(session, row.key, [lead.id], by="P", passcode="x", settings=ST, tool=FakeEmailTool(), now=NOW)


def test_a_failing_tool_is_reported_not_raised(session):
    row = save(session, ONE_OFF)
    approve(session, row.key)
    tool = FakeEmailTool(reply_supported=False)
    res = lib.send_manual(session, row.key, [emailed_lead(session).id], by="P", passcode=PASS, settings=ST,
                          tool=tool, now=NOW)
    assert res[0]["ok"] is False and session.scalars(select(Event).where(Event.kind == "manual_email")).first() is None


# ---------------------------------------------------------------- web routes
@pytest.fixture
def web():
    engine = make_app_engine()
    deps = make_deps()
    app = create_app(make_settings(app_env="local", template_approver_passcode=PASS), deps=deps, engine=engine,
                     clock=lambda: NOW)
    yield TestClient(app, raise_server_exceptions=False), deps, engine
    engine.dispose()


def test_routes_list_preview_save_approve_send(web):
    client, deps, engine = web
    j = client.get("/demo/api/templates").json()
    assert j["approver_set"] and len(j["templates"]) >= 20 and "compliance-checks" in j["options"]["images"]
    pv = client.post("/demo/api/templates/preview", json={"draft": ONE_OFF, "sample": {"role": "CTO"}}).json()
    assert pv["ok"] and 'src="/img/' in pv["html"]  # images from this app in the preview
    key = client.post("/demo/api/templates/save", json={"draft": ONE_OFF, "by": "Priya"}).json()["key"]
    bad = client.post("/demo/api/templates/status", json={"key": key, "status": "approved", "by": "Priya",
                                                          "passcode": "nope"}).json()
    assert bad["ok"] is False and "not right" in bad["error"]
    ok = client.post("/demo/api/templates/status", json={"key": key, "status": "approved", "by": "Priya",
                                                         "passcode": PASS}).json()
    assert ok["template"]["status"] == "approved"
    with TestSession(bind=engine) as s:
        lead = make_lead(s, tool_lead_id="tt", step_sent=1, state="in_sequence")
        s.commit()
        lead_id = lead.id
    sent = client.post("/demo/api/templates/send", json={"key": key, "lead_ids": [lead_id], "by": "Priya",
                                                         "passcode": PASS}).json()
    assert sent["results"][0]["ok"] and any(c[0] == "reply" for c in deps.tool.calls)
    assert client.get("/demo/templates.js").headers["content-type"].startswith("text/javascript")


def test_routes_need_json(web):
    client, _, _ = web
    r = client.post("/demo/api/templates/save", content="draft=x", headers={"content-type": "text/plain"})
    assert r.status_code == 400 and "JSON" in r.json()["error"]


def test_routes_are_local_only(web):
    client, _, _ = web
    assert client.get("/demo/api/templates", headers={"cf-connecting-ip": "1.2.3.4"}).status_code == 404


def test_template_table_exists():
    assert EmailTemplate.__tablename__ == "email_template"


@pytest.fixture(autouse=True)
def _fresh_passcode_counter():
    lib._wrong.clear()
    yield
    lib._wrong.clear()


def test_passcode_guessing_is_locked_out():
    for _ in range(lib.MAX_WRONG_PASSCODES):
        with pytest.raises(lib.TemplateError, match="not right"):
            lib.check_passcode(ST, "guess", now_s=100.0)
    with pytest.raises(lib.TemplateError, match="Too many"):
        lib.check_passcode(ST, PASS, now_s=200.0)  # even the right one, for 10 minutes
    lib.check_passcode(ST, PASS, now_s=100.0 + lib.LOCKOUT_S + 1)


@pytest.mark.parametrize("subject", ["{company:>99999999}", "{company!r}", "{company:{company.__class__}}"])
def test_format_specs_and_conversions_are_refused(subject):
    with pytest.raises(lib.TemplateError):
        lib.clean_draft({**SEQ, "subject": subject}, settings=ST)


def test_lead_supplied_urls_never_become_links(session):
    lead = make_lead(session, name="Ravi http://evil.example/x", company="Fino www.evil.example")
    out = render_step(lead, 2, settings=ST)
    assert "evil" not in out["body_2"] + out["html_2"]


@pytest.mark.parametrize("headers", [{"host": "attacker.example:8000"},
                                     {"origin": "http://attacker.example"}])
def test_console_refuses_other_host_names_dns_rebinding(web, headers):
    client, _, _ = web
    assert client.get("/demo/api/templates", headers=headers).status_code == 404
    assert client.get("/demo/api/templates", headers={"host": "127.0.0.1:8000"}).status_code == 200
