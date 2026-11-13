"""Final whole-branch review, Batch B: verification errors, intake limits, local/real guard,
logging / proxy headers, alert retry cap and minors."""
from __future__ import annotations

import json
import logging
from datetime import timedelta
from types import MappingProxyType, SimpleNamespace

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from outreach import worker
from outreach.app import create_app
from outreach.botfilter import Scanners
from outreach.channels import EMAIL_OK_STATUSES
from outreach.db import init_db, make_engine
from outreach.emailtool.fake import FakeEmailTool
from outreach.intake import IntakePayload, intake_lead
from outreach.localclock import LocalClock, default_clock
from outreach.models import Alert as AlertRow
from outreach.models import AlertAttempt, Event, Lead
from outreach.outreach import Outreach
from outreach.rules import EventSnapshot, LeadSnapshot, decide
from outreach.states import TERMINAL, TERMINAL_VALUES, LeadState
from outreach.verify import VERIFY_ERROR, EmailVerifier
from outreach.whatsapp.fake import FakeWhatsApp

from .conftest import NOW, make_lead
from .fakes import INTAKE_KEY, LINK_TARGETS, WEBHOOK_SECRET, make_app_engine, make_deps, make_settings

S = LeadState
SCANNERS = Scanners(ua_substrings=(), cidrs=(), apple_proxy_cidrs=())
ZB = "https://api.zerobounce.net/v2/validate"


class Verifier:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0

    def check(self, email):
        self.calls += 1
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


@pytest.fixture
def env(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'b.db'}", connect_args={"check_same_thread": False})
    init_db(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    settings = make_settings()
    tool = FakeEmailTool(webhook_secret=WEBHOOK_SECRET)
    outreach = Outreach(settings)
    deps = make_deps(tool=tool, wa=FakeWhatsApp(settings), outreach=outreach)
    outreach.bind(deps)
    yield SimpleNamespace(engine=engine, factory=factory, settings=settings, tool=tool, deps=deps)
    engine.dispose()


def run(env, now):
    return worker.tick(env.factory, env.deps, lambda: now, env.settings)


def get(env, lid):
    with env.factory() as s:
        return s.get(Lead, lid)


def alerts(env, kind):
    with env.factory() as s:
        return list(s.scalars(select(AlertRow).where(AlertRow.kind == kind)))


def payload(**kw):
    p = dict(name="Ann Lee", company="Acme", role="CTO", source="test", email="ann@acme.com")
    p.update(kw)
    return p


# ====================================================================== B1 verification errors

@pytest.mark.parametrize("resp", [
    httpx.Response(500),
    httpx.Response(200, json={"error": "Invalid API key or your account ran out of credits"}),
    httpx.Response(200, json={}),
])
def test_zerobounce_failures_are_verify_error(resp):
    with respx.mock:
        respx.get(ZB).mock(return_value=resp)
        assert EmailVerifier("k").check("a@acme.com") == VERIFY_ERROR


def test_zerobounce_unknown_verdict_stays_unknown():
    with respx.mock:
        respx.get(ZB).mock(return_value=httpx.Response(200, json={"status": "unknown"}))
        assert EmailVerifier("k").check("a@acme.com") == "unknown"


def test_verify_error_is_never_sendable():
    assert VERIFY_ERROR not in EMAIL_OK_STATUSES


def test_intake_holds_an_email_only_lead_on_verify_error(env):
    with env.factory() as s:
        r = intake_lead(s, payload(), Verifier(VERIFY_ERROR), NOW)
        s.commit()
    assert r.outcome == "held_verify"
    lead = get(env, r.lead_id)
    assert (lead.state, lead.email_hold, lead.pause_reason) == (S.PAUSED.value, "verify_error", "verify_error")


def test_intake_verify_error_with_whatsapp_starts_on_whatsapp(env):
    with env.factory() as s:
        r = intake_lead(s, payload(phone="+919876543210", consent_whatsapp=True), Verifier(VERIFY_ERROR), NOW,
                        primary_when_both="email")
        s.commit()
    assert r.outcome == "accepted" and r.channel == "whatsapp"
    assert get(env, r.lead_id).email_hold == "verify_error"


def _held(env, verifier_answers):
    with env.factory() as s:
        r = intake_lead(s, payload(), Verifier(VERIFY_ERROR), NOW)
        s.commit()
    v = Verifier(*verifier_answers)
    env.deps.verify = v.check
    return r.lead_id, v


def test_retry_verification_backoff_then_success_starts_the_lead(env):
    lid, v = _held(env, [VERIFY_ERROR, "valid"])
    run(env, NOW + timedelta(minutes=10))
    assert v.calls == 0
    run(env, NOW + timedelta(minutes=15))
    assert v.calls == 1 and get(env, lid).email_hold == "verify_error"
    run(env, NOW + timedelta(minutes=30))
    assert v.calls == 1  # next at +1 h
    run(env, NOW + timedelta(hours=1))
    lead = get(env, lid)
    assert v.calls == 2 and lead.email_hold is None and lead.verify_status == "valid"
    assert lead.state == S.IN_SEQUENCE.value
    run(env, NOW + timedelta(hours=1, minutes=1))
    assert get(env, lid).tool_lead_id  # start_new enrolled it


def test_retry_verification_gives_up_with_one_alert(env):
    lid, v = _held(env, [VERIFY_ERROR])
    for t in (timedelta(minutes=15), timedelta(hours=1), timedelta(hours=6), timedelta(hours=24),
              timedelta(hours=48), timedelta(hours=96)):
        run(env, NOW + t)
    assert v.calls == 4
    assert len(alerts(env, "verify_failed")) == 1
    lead = get(env, lid)
    assert lead.email_hold == "verify_error" and lead.state == S.PAUSED.value


def test_retry_verification_invalid_email_closes_the_lead(env):
    lid, _v = _held(env, ["invalid"])
    run(env, NOW + timedelta(minutes=15))
    assert get(env, lid).state == S.CLOSED.value


# ====================================================================== B2 intake limits

def test_every_intake_field_max_matches_its_column():
    cols = Lead.__table__.columns
    checked = 0
    for name, field in IntakePayload.model_fields.items():
        if name not in cols or not hasattr(cols[name].type, "length"):
            continue
        maxes = [m.max_length for m in field.metadata if hasattr(m, "max_length")]
        assert maxes == [cols[name].type.length], name
        checked += 1
    assert checked >= 9


def test_too_long_field_rejected_not_truncated(env):
    with env.factory() as s:
        r = intake_lead(s, payload(source="x" * 101), Verifier("valid"), NOW)
    assert r.outcome == "rejected" and "source" in r.reason


def test_unknown_timezone_group_uses_the_default_and_records_it(env):
    with env.factory() as s:
        r = intake_lead(s, payload(timezone_group="mars"), Verifier("valid"), NOW, tz_groups={"in_a": "c1"},
                        default_tz_group="in_a")
        s.commit()
    lead = get(env, r.lead_id)
    assert lead.timezone_group == "in_a" and lead.variant_flags["timezone_group_unknown"] == "mars"


def test_unknown_timezone_group_without_default_rejected(env):
    with env.factory() as s:
        r = intake_lead(s, payload(timezone_group="mars"), Verifier("valid"), NOW, tz_groups={"in_a": "c1"},
                        default_tz_group=None)
    assert r.outcome == "rejected" and r.reason == "unknown timezone_group"


def test_intake_api_uses_the_configured_groups():
    engine = make_app_engine()
    app = create_app(make_settings(), deps=make_deps(), engine=engine, clock=lambda: NOW,
                     scanners=SCANNERS, verifier=Verifier("valid"))
    r = TestClient(app).post("/intake", headers={"X-API-Key": INTAKE_KEY}, json=payload(timezone_group="nope"))
    assert r.status_code == 201
    engine.dispose()


# ====================================================================== B3 local mode with real providers

LOCAL = dict(app_env="local")


@pytest.mark.parametrize("extra", [{"time_scale": 60}, {"fake_clock_file": "/tmp/x.json"},
                                   {"wa_reminder_after_s": 60}])
def test_local_real_sending_refuses_time_tricks(extra):
    with pytest.raises(ValidationError) as e:
        make_settings(**LOCAL, email_mode="real", **extra)
    assert "ALLOW_REAL_LOCAL" in str(e.value)


def test_local_real_sending_allowed_with_override():
    s = make_settings(**LOCAL, whatsapp_mode="real", time_scale=60, allow_real_local=True)
    assert s.time_scale == 60


def test_local_fake_sending_keeps_time_tricks():
    assert make_settings(**LOCAL, time_scale=60).time_scale == 60


def test_clock_offset_ignored_when_real_sending(tmp_path, caplog):
    path = tmp_path / "clock.json"
    LocalClock(path).advance(timedelta(hours=48))
    clock = default_clock(make_settings(**LOCAL, email_mode="real"))
    assert isinstance(clock, LocalClock) and clock.ignore_offset
    clock.path = path
    with caplog.at_level(logging.WARNING):
        a, b = clock(), clock()
    assert abs((a - NOW.now(a.tzinfo)).total_seconds()) < 60
    assert caplog.text.count("offset ignored") == 1
    fake = default_clock(make_settings(**LOCAL))
    assert not fake.ignore_offset


# ====================================================================== B4 ops

def test_logging_setup_configures_the_outreach_logger(monkeypatch):
    from outreach import logging_setup

    monkeypatch.setenv("LOG_LEVEL", "warning")
    assert logging_setup.configure().level == logging.WARNING
    monkeypatch.delenv("LOG_LEVEL")
    assert logging_setup.configure().level == logging.INFO


def _click(settings, headers):
    from outreach.links import make_link

    engine = make_app_engine()
    with sessionmaker(bind=engine)() as s:
        lead = make_lead(s)
        s.commit()
        lid = lead.id
    app = create_app(settings, deps=make_deps(), engine=engine, clock=lambda: NOW, scanners=SCANNERS)
    url = make_link(lid, 1, "pricing", "email", settings=settings)
    TestClient(app, follow_redirects=False).get("/r/" + url.rsplit("/", 1)[1], headers=headers)
    with sessionmaker(bind=engine)() as s:
        ip = s.scalars(select(Event.ip)).one()
    engine.dispose()
    return ip


def test_forwarded_for_ignored_by_default():
    assert _click(make_settings(), {"X-Forwarded-For": "203.0.113.5"}) == "testclient"


def test_forwarded_for_trusted_when_configured():
    assert _click(make_settings(trust_proxy_headers=True), {"X-Forwarded-For": "203.0.113.5, 10.0.0.1"}) == \
        "203.0.113.5"


def test_run_local_disables_the_access_log():
    from pathlib import Path

    assert "--no-access-log" in (Path(__file__).parents[1] / "scripts" / "run_local").read_text()


def test_requirements_lock_pins_and_excludes_the_project():
    from pathlib import Path

    lines = [ln for ln in (Path(__file__).parents[1] / "requirements.lock").read_text().splitlines()
             if ln and not ln.startswith("#")]
    assert lines and all("==" in ln for ln in lines)
    assert not any(ln.lower().startswith("outreach") for ln in lines)


# ====================================================================== B5 minors

def test_booking_target_is_required():
    with pytest.raises(ValidationError):
        make_settings(link_targets={k: v for k, v in LINK_TARGETS.items() if k != "booking"})


def test_production_links_must_be_https():
    with pytest.raises(ValidationError) as e:
        make_settings(link_base_url="http://go.example.com")
    assert "link_base_url" in str(e.value)
    with pytest.raises(ValidationError):
        make_settings(link_targets={**LINK_TARGETS, "pricing": "http://ozoo.ai/pricing"})
    assert make_settings(app_env="local", link_base_url="http://127.0.0.1:8000")


def test_cal_booking_url_is_gone():
    from outreach.config import Settings

    assert "cal_booking_url" not in Settings.model_fields


@pytest.mark.parametrize("path", ["/intake", "/webhooks/whatsapp", "/webhooks/cal"])
def test_deeply_nested_json_is_a_400(path, monkeypatch):
    import outreach.app as app_mod

    engine = make_app_engine()
    settings = make_settings()
    app = create_app(settings, deps=make_deps(), engine=engine, clock=lambda: NOW, scanners=SCANNERS)

    def boom(*a, **k):
        raise RecursionError("too deep")

    monkeypatch.setattr(app_mod.json, "loads", boom)
    monkeypatch.setattr(app_mod, "parse_wa_webhook", boom)
    monkeypatch.setattr(app_mod, "parse_cal_webhook", boom)
    r = TestClient(app).post(path, content=b"[" * 10, headers={"X-API-Key": INTAKE_KEY})
    assert r.status_code == 400
    engine.dispose()


def test_alert_repost_capped_at_five_then_in_the_digest(env):
    env.deps.alerter.repost_ok = False
    with env.factory() as s:
        s.add(AlertRow(lead_id=None, kind="hot_lead", text="lead 1: hot", created_at=NOW))
        s.commit()
    for i in range(8):
        run(env, NOW + timedelta(minutes=2 + i))
    reposts = [c for c in env.deps.alerter.calls if c[0] == "repost"]
    assert len(reposts) == 5
    with env.factory() as s:
        att = s.scalars(select(AlertAttempt)).one()
        assert att.attempts == 5 and att.gave_up_at is not None
    env.deps.alerter.repost_ok = True
    run(env, NOW + timedelta(hours=2))
    digests = [c for c in env.deps.alerter.calls if c[0] == "post_text"]
    assert digests and "could not be posted after 5 tries" in digests[-1][2]
    run(env, NOW + timedelta(hours=4))
    assert len([c for c in env.deps.alerter.calls if c[0] == "post_text"]) == len(digests)  # reported once


def test_dead_code_removed():
    import outreach.alerts as alerts
    import outreach.db as db
    import outreach.score as score

    assert not hasattr(db, "SessionLocal") and not hasattr(alerts, "unposted_alert_ids")
    assert not hasattr(score, "apply_points")


def test_dev_sim_bounce_flags_are_exclusive(capsys):
    from outreach import dev

    with pytest.raises(SystemExit):
        dev.main(["sim", "email-bounce", "1", "--hard", "--soft"])


def test_single_constants():
    import outreach.ingest as ingest
    import outreach.jobs as jobs
    import outreach.rules as rules
    import outreach.sequencer as sequencer

    assert rules.LAST_EMAIL_STEP == sequencer.LAST_STEP == worker.LAST_STEP == 4
    assert TERMINAL_VALUES == frozenset(s.value for s in TERMINAL)
    assert ingest._TERMINAL_VALUES is TERMINAL_VALUES and jobs._TERMINAL_VALUES is TERMINAL_VALUES


def _snap(flags=None):
    return LeadSnapshot(id=1, state=S.IN_SEQUENCE, score=0, step_sent=1, active_channel="email",
                        channel_switched=False, has_usable_email=True, has_wa_optin=False, email="a@b.com",
                        phone=None, wa_human_owner=None, wa_window_open=False,
                        flags=MappingProxyType(flags or {}))


def _click_ev(tag):
    return EventSnapshot(kind="click", channel="email", tag=tag, verdict="human", step=1, occurred_at=NOW,
                         data=MappingProxyType({}))


def test_click_points_capped_per_tag():
    from outreach.actions import AddScore, SetFlag

    first = decide(_snap(), _click_ev("pricing"), NOW)
    assert AddScore(10) in first and SetFlag("click_points", {"pricing": 1}) in first
    third = decide(_snap({"click_points": {"pricing": 2}, "hot_alerted": True}), _click_ev("pricing"), NOW)
    assert not any(isinstance(a, AddScore) for a in third)
    other = decide(_snap({"click_points": {"pricing": 2}}), _click_ev("case_study"), NOW)
    assert AddScore(5) in other
