"""python -m outreach.dev (Task 13): setup, seed, show, tick, advance, sim."""
from __future__ import annotations

import os
import stat

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from outreach import dev
from outreach.app import create_app
from outreach.config import Settings
from outreach.db import make_engine
from tests.fakes import TestSession
from outreach.models import Lead

@pytest.fixture
def envfile(tmp_path, monkeypatch):
    # keep the developer's real environment out of the test
    for key in list(os.environ):
        if key.upper() in Settings.model_fields or key.lower() in Settings.model_fields:
            monkeypatch.delenv(key, raising=False)
    path = tmp_path / ".env.local"
    assert dev.main(["setup", "--env-file", str(path),
                     "--db", str(tmp_path / "local.db"), "--clock-file", str(tmp_path / "clock.json")]) == 0
    return path


def run(envfile, *argv, **kw) -> int:
    return dev.main(["--env-file", str(envfile), *argv], **kw)


def settings_of(envfile) -> Settings:
    return Settings(_env_file=str(envfile))


# ---------------------------------------------------------------- setup

def test_setup_writes_local_settings_with_fresh_secrets(envfile, tmp_path):
    s = settings_of(envfile)
    assert s.app_env == "local" and s.email_mode == "fake" and s.llm_mode == "fake"
    assert s.database_url == f"sqlite:///{tmp_path / 'local.db'}"
    assert len(s.link_secret) >= 40 and s.run_worker and s.worker_interval_s == 10
    text = envfile.read_text()
    assert "# SQLAlchemy database URL" in text  # comments from .env.example are kept
    assert "\n# TOOL_API_KEY=\n" in text  # provider credentials stay unset
    assert "\n# PRIMARY_WHEN_BOTH=\n" in text  # blank keys are commented out: defaults apply
    assert s.primary_when_both == "whatsapp" and s.business_tz == "Asia/Kolkata"
    assert stat.S_IMODE(envfile.stat().st_mode) == 0o600
    assert (tmp_path / "local.db").exists()


def test_setup_is_idempotent_and_force_regenerates(envfile, tmp_path, capsys):
    before = envfile.read_text()
    assert dev.main(["setup", "--env-file", str(envfile), "--db", str(tmp_path / "local.db")]) == 0
    assert envfile.read_text() == before
    assert "kept" in capsys.readouterr().out
    assert dev.main(["setup", "--force", "--env-file", str(envfile), "--db", str(tmp_path / "local.db"),
                     "--clock-file", str(tmp_path / "clock.json")]) == 0
    assert envfile.read_text() != before


def test_dev_commands_refuse_production(tmp_path, monkeypatch, capsys):
    from tests.test_config import REQUIRED

    for k, v in REQUIRED.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("APP_ENV", "production")
    assert dev.main(["--env-file", str(tmp_path / "none"), "seed"]) == 2
    assert "APP_ENV=local" in capsys.readouterr().err


# ---------------------------------------------------------------- seed / show / tick / advance

def test_seed_show_tick_advance(envfile, capsys):
    assert run(envfile, "seed") == 0
    out = capsys.readouterr().out
    assert out.count("accepted") == 4 and "held_eu" in out
    s = settings_of(envfile)
    engine = make_engine(s.database_url)
    with TestSession(bind=engine) as ses:
        leads = list(ses.scalars(select(Lead).order_by(Lead.id)))
        assert len(leads) == 5
        assert all(ld.email is None or ld.email.endswith("example.com") for ld in leads)
        assert all(ld.phone is None or ld.phone.startswith("+1201555") for ld in leads)
    assert run(envfile, "seed") == 0  # again: duplicates are skipped, nothing new
    assert capsys.readouterr().out.count("skipped_duplicate") == 5

    assert run(envfile, "tick") == 0
    assert "start_new" in capsys.readouterr().out

    assert run(envfile, "show") == 0
    listing = capsys.readouterr().out
    assert "ana@acme.example.com" in listing and "in_sequence" in listing
    assert run(envfile, "show", "--mask") == 0
    masked = capsys.readouterr().out
    assert "ana@acme.example.com" not in masked and "a***@acme.example.com" in masked

    assert run(envfile, "show", "2") == 0
    detail = capsys.readouterr().out
    assert "[template:ozoo_intro]" in detail and "Events" in detail and "Jobs" in detail and "Alerts" in detail

    assert run(envfile, "advance", "--hours", "49") == 0
    assert "+49.0 h" in capsys.readouterr().out
    assert run(envfile, "tick") == 0
    assert "wa_reminder" in capsys.readouterr().out
    assert run(envfile, "advance", "--reset") == 0
    engine.dispose()


def test_show_unknown_lead(envfile, capsys):
    assert run(envfile, "show", "999") == 1
    assert "not found" in capsys.readouterr().err


# ---------------------------------------------------------------- sim

def test_sim_sends_signed_request_and_prints_summary(envfile, capsys):
    run(envfile, "seed")
    run(envfile, "tick")
    capsys.readouterr()
    s = settings_of(envfile)
    client = TestClient(create_app(s), raise_server_exceptions=False, follow_redirects=False)
    assert run(envfile, "sim", "email-reply", "--lead", "1", "--text", "Sounds interesting, can we talk?",
               client=client) == 0
    out = capsys.readouterr().out
    assert "HTTP 200" in out and "qualifying" in out
    assert run(envfile, "sim", "wa-button", "--lead", "2", "--id", "book_demo", client=client) == 0
    assert "qualifying" in capsys.readouterr().out
    assert run(envfile, "sim", "click", "--lead", "1", "--tag", "pricing", client=client) == 0
    assert "browser confirmed the click" in capsys.readouterr().out
    assert run(envfile, "sim", "teams", "--text", "help", client=client) == 0
    assert "Commands:" in capsys.readouterr().out
    assert run(envfile, "sim", "wa-status", "--lead", "2", "--status", "delivered", client=client) == 0
    assert run(envfile, "sim", "cal-form", "--lead", "1", "--size", "51-200", "--timeline", "now",
               client=client) == 0


def test_sim_reports_missing_contact(envfile, capsys):
    run(envfile, "seed")
    assert run(envfile, "sim", "wa-text", "--lead", "1", "--text", "hi") == 1
    assert "no phone" in capsys.readouterr().err
