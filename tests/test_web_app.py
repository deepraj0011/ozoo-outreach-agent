"""The React web app build served at /app/: local only, `/` redirects to it."""
from __future__ import annotations

from fastapi.testclient import TestClient

from outreach.app import create_app
from outreach.botfilter import Scanners

from .conftest import NOW
from .fakes import make_app_engine, make_deps, make_settings

SCANNERS = Scanners(ua_substrings=(), cidrs=(), apple_proxy_cidrs=())


def build(app_env: str = "local", web_dir=None) -> TestClient:
    settings = make_settings(app_env=app_env, email_flow="p0")
    app = create_app(settings, deps=make_deps(), engine=make_app_engine(), clock=lambda: NOW, scanners=SCANNERS, web_dir=web_dir)
    return TestClient(app, raise_server_exceptions=False)


def test_serves_built_index(tmp_path):
    (tmp_path / "index.html").write_text('<div id="root"></div>')
    r = build(web_dir=tmp_path).get("/app/")
    assert '<div id="root">' in r.text and r.headers["cache-control"] == "no-cache"


def test_deep_link_falls_back_to_index(tmp_path):
    (tmp_path / "index.html").write_text('<div id="root"></div>')
    r = build(web_dir=tmp_path).get("/app/connectors")
    assert r.status_code == 200 and '<div id="root">' in r.text


def test_missing_build_explains_itself(tmp_path):
    r = build(web_dir=tmp_path).get("/app/")
    assert r.status_code == 503 and "scripts/build_web" in r.text


def test_app_not_served_outside_local(tmp_path):
    (tmp_path / "index.html").write_text("x")
    assert build(app_env="production", web_dir=tmp_path).get("/app/").status_code == 404


def test_app_without_slash_redirects_to_slash():
    r = build().get("/app", follow_redirects=False)
    assert r.status_code in (301, 302, 307, 308)
    assert r.headers["location"].endswith("/app/")


def test_root_redirects_to_app():
    r = build().get("/", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/app/"


def test_asset_file_is_served(tmp_path):
    (tmp_path / "index.html").write_text("x")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "a.js").write_text("console.log(1)")
    r = build(web_dir=tmp_path).get("/app/assets/a.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]


def test_app_refused_through_a_tunnel(tmp_path):
    (tmp_path / "index.html").write_text("x")
    client = build(web_dir=tmp_path)
    assert client.get("/app/", headers={"x-forwarded-for": "203.0.113.9"}).status_code == 404
    assert client.get("/app/connectors", headers={"cf-ray": "abc"}).status_code == 404


def test_app_refused_for_a_foreign_host_name(tmp_path):
    (tmp_path / "index.html").write_text("x")
    client = build(web_dir=tmp_path)
    assert client.get("/app/", headers={"host": "evil.example.com"}).status_code == 404
