from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from outreach.app import create_app
from outreach.botfilter import Scanners
from outreach.db import SessionLocal
from outreach.models import Lead
from tests.conftest import NOW
from tests.fakes import INTAKE_KEY, make_app_engine, make_deps, make_settings


class FakeVerifier:
    def check(self, email):
        return "valid"


@pytest.fixture
def env():
    engine = make_app_engine()
    app = create_app(make_settings(), deps=make_deps(), engine=engine, clock=lambda: NOW,
                     scanners=Scanners((), (), ()), verifier=FakeVerifier())
    client = TestClient(app, raise_server_exceptions=False)
    s = SessionLocal(bind=engine)
    yield client, s
    s.close()
    engine.dispose()


PAYLOAD = dict(name="Asha", company="Acme", role="CTO", source="form", email="asha@acme.com",
               consent_email=True, country="IN")
OK = {"X-API-Key": INTAKE_KEY}


def test_intake_requires_api_key(env):
    client, s = env
    assert client.post("/intake", json=PAYLOAD).status_code == 401
    assert client.post("/intake", json=PAYLOAD, headers={"X-API-Key": "wrong"}).status_code == 401
    assert s.scalar(select(func.count()).select_from(Lead)) == 0


def test_intake_accepted_201_and_committed(env):
    client, s = env
    r = client.post("/intake", json=PAYLOAD, headers=OK)
    assert r.status_code == 201
    body = r.json()
    assert body["outcome"] == "accepted" and body["channel"] == "email" and body["lead_id"]
    assert s.get(Lead, body["lead_id"]).state == "in_sequence"


def test_intake_duplicate_and_rejected_200(env):
    client, _ = env
    client.post("/intake", json=PAYLOAD, headers=OK)
    r = client.post("/intake", json=PAYLOAD, headers=OK)
    assert r.status_code == 200 and r.json()["outcome"] == "skipped_duplicate"
    r = client.post("/intake", json={"name": "x"}, headers=OK)
    assert r.status_code == 200 and r.json()["outcome"] == "rejected"


def test_intake_oversized_413(env):
    client, s = env
    r = client.post("/intake", content=b"{" + b" " * (1024 * 1024) + b"}",
                    headers={**OK, "Content-Type": "application/json"})
    assert r.status_code == 413
    assert s.scalar(select(func.count()).select_from(Lead)) == 0


def test_intake_bad_json_400(env):
    client, _ = env
    r = client.post("/intake", content=b"{nope", headers={**OK, "Content-Type": "application/json"})
    assert r.status_code == 400 and r.json() == {"ok": False}
