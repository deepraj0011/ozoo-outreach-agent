"""Read-only JSON routes for the Connectors and Settings screens (local only)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from outreach.app import create_app
from outreach.botfilter import Scanners

from .conftest import NOW
from .fakes import make_app_engine, make_deps, make_settings

SCANNERS = Scanners(ua_substrings=(), cidrs=(), apple_proxy_cidrs=())


def build(app_env: str = "local") -> TestClient:
    settings = make_settings(app_env=app_env, email_flow="p0")
    app = create_app(settings, deps=make_deps(), engine=make_app_engine(), clock=lambda: NOW, scanners=SCANNERS)
    return TestClient(app, raise_server_exceptions=False)


def test_connectors_route_lists_ten_cards():
    r = build().get("/demo/api/connectors")
    assert r.status_code == 200 and len(r.json()["connectors"]) == 10
    assert r.headers["cache-control"] == "no-store"


def test_settings_route_returns_sections():
    r = build().get("/demo/api/settings")
    assert r.json()["sections"][0]["id"] == "sending"
    assert r.headers["cache-control"] == "no-store"


def test_routes_absent_outside_local():
    c = build(app_env="production")
    assert c.get("/demo/api/connectors").status_code == 404
    assert c.get("/demo/api/settings").status_code == 404
