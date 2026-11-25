"""P0 scheduled emails, task 8: the JSON routes behind the Templates, Email schedule and lead Emails screens."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from outreach.app import create_app
from outreach.botfilter import Scanners
from outreach.db import init_db
from outreach.emailtool.mailpit import MailpitEmailTool
from outreach.models import PlannedEmail

from .conftest import NOW, make_lead
from .fakes import WEBHOOK_SECRET, make_app_engine, make_deps, make_settings

SCANNERS = Scanners(ua_substrings=(), cidrs=(), apple_proxy_cidrs=())
GOOD = {"name": "Mine", "subject": "Hi {first_name}", "body": "Hello {first_name}", "by": "pm"}


class SMTPStub:
    def __init__(self, host, port):
        pass

    def send_message(self, msg):
        pass

    def quit(self):
        pass


def build(tmp_path, app_env="local"):
    tool = MailpitEmailTool(WEBHOOK_SECRET, state_file=tmp_path / "mp.db", now=lambda: NOW, smtp_factory=SMTPStub)
    engine = make_app_engine()
    settings = make_settings(app_env=app_env, email_flow="p0")
    app = create_app(settings, deps=make_deps(tool=tool), engine=engine, clock=lambda: NOW, scanners=SCANNERS)
    return TestClient(app, raise_server_exceptions=False), engine


def test_routes_absent_outside_local(tmp_path):
    client, _ = build(tmp_path, app_env="production")
    assert client.get("/demo/api/p0/status").status_code == 404
    assert client.get("/demo/p0.js").status_code == 404


def test_post_requires_json(tmp_path):
    client, _ = build(tmp_path)
    assert client.get("/demo/api/p0/status").json() == {"on": True}
    r = client.post("/demo/api/p0/templates", content="name=x", headers={"content-type": "text/plain"})
    assert r.status_code == 415


def test_template_round_trip(tmp_path):
    client, _ = build(tmp_path)
    first = client.get("/demo/api/p0/templates").json()
    assert len(first["templates"]) == 4 and "first_name" in first["placeholders"]
    saved = client.post("/demo/api/p0/templates", json=GOOD)
    assert saved.status_code == 200
    tid = saved.json()["id"]
    assert client.post(f"/demo/api/p0/templates/{tid}/status", json={"status": "live", "by": "pm"}).status_code == 200
    names = {t["name"]: t["status"] for t in client.get("/demo/api/p0/templates").json()["templates"]}
    assert names["Mine"] == "live"
    pv = client.post("/demo/api/p0/preview", json={"template_id": tid}).json()
    assert pv["subject"].startswith("Hi ") and "<" in pv["html"]
    bad = client.post("/demo/api/p0/templates", json={**GOOD, "body": "Hi {nope}"})
    assert bad.status_code == 400 and "error" in bad.json()


def test_schedule_save_error_is_400(tmp_path):
    client, _ = build(tmp_path)
    got = client.get("/demo/api/p0/schedule").json()
    assert [r["day"] for r in got["rows"]] == [0, 4, 9, 15] and len(got["example"]) == 4
    bad = client.post("/demo/api/p0/schedule", json={"rows": [], "by": "pm"})
    assert bad.status_code == 400 and "error" in bad.json()
    rows = [{"template_id": r["template_id"], "day": r["day"] + 1} for r in got["rows"]]
    ok = client.post("/demo/api/p0/schedule", json={"rows": rows, "by": "pm"})
    assert ok.status_code == 200 and ok.json()["rows"][0]["day"] == 1


def test_lead_rows_and_pause(tmp_path):
    client, engine = build(tmp_path)
    client.get("/demo/api/p0/schedule")
    from sqlalchemy.orm import sessionmaker
    from outreach.p0 import planner
    with sessionmaker(engine)() as s:
        lead = make_lead(s, active_channel="email")
        planner.plan_lead(s, lead, NOW, settings=make_settings(email_flow="p0"))
        s.commit()
        lead_id = lead.id
    view = client.get(f"/demo/api/p0/leads/{lead_id}").json()
    assert len(view["rows"]) == 4 and view["paused"] is False
    assert client.post(f"/demo/api/p0/leads/{lead_id}/pause", json={"by": "pm"}).status_code == 200
    assert client.get(f"/demo/api/p0/leads/{lead_id}").json()["paused"] is True
    assert client.post(f"/demo/api/p0/leads/{lead_id}/pause", json={"by": ""}).status_code == 400
    assert client.post(f"/demo/api/p0/leads/{lead_id}/resume", json={"by": "pm"}).status_code == 200
    assert client.get("/demo/api/p0/leads/9999").status_code == 404


def test_lead_rows_with_a_sent_email_is_json(tmp_path):
    client, engine = build(tmp_path)
    client.get("/demo/api/p0/schedule")
    from sqlalchemy.orm import sessionmaker
    from outreach.p0 import planner
    with sessionmaker(engine)() as s:
        lead = make_lead(s, active_channel="email")
        planner.plan_lead(s, lead, NOW, settings=make_settings(email_flow="p0"))
        row = s.query(PlannedEmail).filter_by(lead_id=lead.id, email_no=1).one()
        row.status, row.sent_at = "sent", NOW
        s.commit()
        lead_id = lead.id
    r = client.get(f"/demo/api/p0/leads/{lead_id}")
    assert r.status_code == 200
    assert r.json()["rows"][0]["sent_at"].startswith("2026-11-12T10:00")
