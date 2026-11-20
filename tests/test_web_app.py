"""The plain HTML/JS web app at /app/: served locally only, `/` redirects to it."""
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


def test_app_serves_index_locally():
    client = build()
    r = client.get("/app/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "Ozoo.ai Outreach" in r.text and 'id="view"' in r.text


def test_app_without_slash_redirects_to_slash():
    r = build().get("/app", follow_redirects=False)
    assert r.status_code in (301, 302, 307, 308)
    assert r.headers["location"].endswith("/app/")


def test_root_redirects_to_app():
    r = build().get("/", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/app/"


def test_js_module_and_css_have_right_content_types():
    client = build()
    js = client.get("/app/js/main.js")
    assert js.status_code == 200
    assert "javascript" in js.headers["content-type"]
    css = client.get("/app/css/tokens.css")
    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")


def test_app_absent_outside_local():
    client = build(app_env="production")
    assert client.get("/app/").status_code == 404
    assert client.get("/app/js/main.js").status_code == 404


def test_app_refused_through_a_tunnel():
    client = build()
    assert client.get("/app/", headers={"x-forwarded-for": "203.0.113.9"}).status_code == 404
    assert client.get("/app/js/main.js", headers={"cf-ray": "abc"}).status_code == 404


def test_app_refused_for_a_foreign_host_name():
    client = build()
    assert client.get("/app/", headers={"host": "evil.example.com"}).status_code == 404
