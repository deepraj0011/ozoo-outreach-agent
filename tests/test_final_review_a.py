"""Final whole-branch review, Batch A: tool-stop retry, lead row locking, stuck-lead recovery,
once-only sends, SQLite robustness."""
from __future__ import annotations

from datetime import timedelta
from types import MappingProxyType, SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from outreach import worker
from outreach.actions import (
    TOOL_BLOCK_PENDING, TOOL_STOP_PENDING, Alert, SendReply, SetState, ToolStop, WaSendText, execute,
)
from outreach.app import create_app
from outreach.botfilter import Scanners
from outreach.classifier import ReplyClass, actions_for_class
from outreach.db import init_db, make_engine
from outreach.emailtool.base import NormalizedEvent
from outreach.emailtool.fake import FakeEmailTool
from outreach.emailtool.instantly import BASE_URL, InstantlyTool
from outreach.ingest import ingest
from outreach.jobs import JobQueue, run_job
from outreach.models import Alert as AlertRow
from outreach.models import Event, Job, Lead, Suppression
from outreach.outreach import INTRO_ATTEMPT, Outreach
from outreach.rules import EventSnapshot, LeadSnapshot, decide
from outreach.sequencer import end_of_sequence_route
from outreach.states import LeadState
from outreach.whatsapp.client import WaSendError, WhatsAppNotAllowed
from outreach.whatsapp.fake import FakeWhatsApp

from .conftest import NOW, make_lead
from .fakes import WEBHOOK_SECRET, make_deps, make_settings
from .test_handoff import activity, teams_headers

S = LeadState
PHONE = "+919876543210"
SCANNERS = Scanners(ua_substrings=(), cidrs=(), apple_proxy_cidrs=())


@pytest.fixture
def env(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    init_db(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    settings = make_settings()
    tool = FakeEmailTool(webhook_secret=WEBHOOK_SECRET)
    wa = FakeWhatsApp(settings)
    outreach = Outreach(settings)
    deps = make_deps(tool=tool, wa=wa, outreach=outreach)
    outreach.bind(deps)
    yield SimpleNamespace(engine=engine, factory=factory, settings=settings, tool=tool, wa=wa, deps=deps)
    engine.dispose()


def run(env, now, **kw):
    return worker.tick(env.factory, env.deps, lambda: now, env.settings, **kw)


def new_lead(env, **kw):
    with env.factory() as s:
        lead = make_lead(s, **kw)
        s.commit()
        return lead.id


def get(env, lead_id):
    with env.factory() as s:
        return s.get(Lead, lead_id)


def alerts(env, kind=None):
    with env.factory() as s:
        q = select(AlertRow)
        if kind:
            q = q.where(AlertRow.kind == kind)
        return list(s.scalars(q.order_by(AlertRow.id)))


def calls(env, name):
    return [c for c in env.tool.calls if c[0] == name]


def wa_kw(with_email=False, **kw):
    kw.setdefault("phone", PHONE)
    kw.setdefault("consent_whatsapp", True)
    kw.setdefault("active_channel", "whatsapp")
    if with_email:
        kw.setdefault("verify_status", "valid")
    else:
        kw["email"] = None
    return kw


def snap(state, **kw):
    base = dict(id=1, state=state, score=0, step_sent=1, active_channel="email", channel_switched=False,
                has_usable_email=True, has_wa_optin=False, email="a@b.com", phone=None, wa_human_owner=None,
                wa_window_open=False, flags=MappingProxyType({}))
    base.update(kw)
    return LeadSnapshot(**base)


def ev(kind, channel="email", tag=None, verdict="human", step=1, at=NOW, **data):
    return EventSnapshot(kind=kind, channel=channel, tag=tag, verdict=verdict, step=step, occurred_at=at,
                         data=MappingProxyType(data))


def do(env, lead_id, actions, now=NOW):
    with env.factory() as s:
        lead = s.get(Lead, lead_id)
        execute(s, lead, actions, env.deps, now)
        s.commit()


# ====================================================================== A1 tool stop retry

def test_failed_tool_stop_flags_alerts_and_worker_retries_until_it_works(env):
    lid = new_lead(env, verify_status="valid", active_channel="email", tool_lead_id="t-1", step_sent=1)
    env.tool.fail.add("stop")
    do(env, lid, [ToolStop()])
    pending = get(env, lid).variant_flags[TOOL_STOP_PENDING]
    assert pending["items"] == ["t-1"] and pending["attempts"] == 1
    a = alerts(env, "tool_stop_failed")
    assert len(a) == 1 and "may still receive emails from the email tool" in a[0].text
    assert f"lead {lid}" in a[0].text

    run(env, NOW + timedelta(minutes=1))  # not due yet (5 min)
    assert len(calls(env, "stop")) == 1
    run(env, NOW + timedelta(minutes=5))  # due, fails again -> next in 30 min
    assert len(calls(env, "stop")) == 2
    assert get(env, lid).variant_flags[TOOL_STOP_PENDING]["attempts"] == 2
    run(env, NOW + timedelta(minutes=20))
    assert len(calls(env, "stop")) == 2
    env.tool.fail.clear()
    run(env, NOW + timedelta(minutes=40))
    assert calls(env, "stop")[-1] == ("stop", "t-1")
    assert TOOL_STOP_PENDING not in get(env, lid).variant_flags
    assert len(alerts(env, "tool_stop_failed")) == 1


def test_tool_stop_realerts_once_after_three_failed_retries_then_retries_silently(env):
    lid = new_lead(env, verify_status="valid", active_channel="email", tool_lead_id="t-1", step_sent=1)
    env.tool.fail.add("stop")
    do(env, lid, [ToolStop()])
    t = NOW
    for _ in range(6):
        t += timedelta(hours=7)
        run(env, t)
    assert len(calls(env, "stop")) == 7  # first try + 6 retries, never given up
    assert len(alerts(env, "tool_stop_failed")) == 2
    assert get(env, lid).variant_flags[TOOL_STOP_PENDING]["attempts"] == 7


def test_successful_stop_clears_an_older_pending_flag(env):
    lid = new_lead(env, verify_status="valid", active_channel="email", tool_lead_id="t-1",
                   variant_flags={TOOL_STOP_PENDING: {"items": ["t-1"], "attempts": 1,
                                                       "next_at": (NOW + timedelta(hours=1)).isoformat()}})
    do(env, lid, [ToolStop()])
    assert TOOL_STOP_PENDING not in get(env, lid).variant_flags


@pytest.mark.parametrize("state", [S.SUPPRESSED, S.CLOSED, S.HANDED_OFF])
def test_sent_on_a_terminal_lead_restops_and_alerts(state):
    out = decide(snap(state), ev("sent"), NOW)
    assert out == [ToolStop(), Alert("tool_stop_failed")]


@pytest.mark.parametrize("state", [S.ENGAGED, S.QUALIFYING, S.NURTURE, S.PAUSED])
def test_sent_on_a_non_sequence_lead_records_the_step_and_restops(state):
    out = decide(snap(state), ev("sent", step=2), NOW)
    assert ToolStop() in out and Alert("tool_stop_failed") in out


def test_sent_in_sequence_does_not_restop():
    out = decide(snap(S.IN_SEQUENCE), ev("sent", step=2), NOW)
    assert ToolStop() not in out and not any(isinstance(a, Alert) for a in out)


def test_sent_webhook_on_suppressed_lead_calls_tool_stop(env):
    lid = new_lead(env, verify_status="valid", active_channel="email", tool_lead_id="t-9",
                   state=S.SUPPRESSED, step_sent=1)
    with env.factory() as s:
        ingest(s, [NormalizedEvent(provider_event_id="sent-x", channel="email", kind="sent", occurred_at=NOW,
                                   tool_lead_id="t-9", step=2)], env.deps, NOW, SCANNERS)
    assert calls(env, "stop") == [("stop", "t-9")]
    assert len(alerts(env, "tool_stop_failed")) == 1


@pytest.mark.parametrize("kind,data", [("unsubscribe", {}), ("bounce_hard", {})])
def test_opt_out_and_hard_bounce_block_the_email_in_the_tool(env, kind, data):
    lid = new_lead(env, email="who@acme.com", verify_status="valid", active_channel="email", tool_lead_id="t-1")
    with env.factory() as s:
        ingest(s, [NormalizedEvent(provider_event_id=f"{kind}-1", channel="email", kind=kind, occurred_at=NOW,
                                   lead_id=lid, data=data)], env.deps, NOW, SCANNERS)
    assert calls(env, "block") == [("block", "who@acme.com")]


def test_wa_stop_keyword_blocks_the_email_too(env):
    lid = new_lead(env, **wa_kw(with_email=True, email="both@acme.com"))
    with env.factory() as s:
        ingest(s, [NormalizedEvent(provider_event_id="wa-stop", channel="whatsapp", kind="wa_text",
                                   occurred_at=NOW, phone=PHONE, data={"text": "STOP"})], env.deps, NOW, SCANNERS)
    assert get(env, lid).state == S.SUPPRESSED.value
    assert calls(env, "block") == [("block", "both@acme.com")]


def test_failed_block_is_retried_with_its_own_flag(env):
    lid = new_lead(env, email="who@acme.com", verify_status="valid", active_channel="email")
    env.tool.fail.add("block")
    with env.factory() as s:
        ingest(s, [NormalizedEvent(provider_event_id="u-1", channel="email", kind="unsubscribe", occurred_at=NOW,
                                   lead_id=lid)], env.deps, NOW, SCANNERS)
    flags = get(env, lid).variant_flags
    assert flags[TOOL_BLOCK_PENDING]["items"] == ["who@acme.com"] and TOOL_STOP_PENDING not in flags
    assert len(alerts(env, "tool_stop_failed")) == 1
    env.tool.fail.clear()
    run(env, NOW + timedelta(minutes=6))
    assert len(calls(env, "block")) == 2
    assert TOOL_BLOCK_PENDING not in get(env, lid).variant_flags


def test_end_of_sequence_route_stops_the_tool():
    lead = SimpleNamespace(score=0)
    for clicks in (0, 2):
        _route, actions = end_of_sequence_route(lead, clicks)
        assert actions[0] == ToolStop()


def test_end_of_sequence_job_calls_tool_stop(env):
    lid = new_lead(env, verify_status="valid", active_channel="email", tool_lead_id="t-4", step_sent=4,
                   last_sent_at=NOW - timedelta(days=6))
    run(env, NOW)
    assert get(env, lid).state == S.NURTURE.value
    assert calls(env, "stop") == [("stop", "t-4")]


def _instantly(handler):
    return InstantlyTool("k", WEBHOOK_SECRET, client=httpx.Client(transport=httpx.MockTransport(handler)),
                         sleep=lambda s: None)


def test_instantly_stop_treats_404_as_already_stopped():
    t = _instantly(lambda req: httpx.Response(404, json={}))
    t.stop("gone")  # no raise


def test_instantly_block_and_find_lead():
    seen = []

    def handler(req):
        seen.append((req.method, req.url.path))
        if req.url.path.endswith("/leads/list"):
            return httpx.Response(200, json={"items": [{"id": "L-7", "email": "x@acme.com"}]})
        return httpx.Response(200, json={"id": "b-1"})

    t = _instantly(handler)
    t.block("x@acme.com")
    assert t.find_lead("x@acme.com") == "L-7"
    assert seen[0] == ("POST", "/api/v2/block-lists-entries")
    assert seen[1] == ("POST", "/api/v2/leads/list")


def test_instantly_find_lead_absent():
    t = _instantly(lambda req: httpx.Response(200, json={"items": []}))
    assert t.find_lead("x@acme.com") is None


def test_fake_tool_block_and_find_lead():
    f = FakeEmailTool()
    lead = SimpleNamespace(id=3, email="z@acme.com")
    tid = f.add_lead("c1", lead, {})
    assert f.find_lead("z@acme.com") == tid
    f.stop(tid)
    assert f.find_lead("z@acme.com") is None
    f.block("z@acme.com")
    assert ("block", "z@acme.com") in f.calls


# ====================================================================== A2 locking / stale rows

def test_ingest_decides_on_the_fresh_lead_row(env):
    lid = new_lead(env, verify_status="valid", active_channel="email", tool_lead_id="t-1", email="r@acme.com")
    s = env.factory()
    s.get(Lead, lid)  # stale copy in the identity map
    s.commit()  # end the read (expire_on_commit=False keeps the stale copy)
    with env.factory() as other:
        other.get(Lead, lid).state = S.BOOKED.value
        other.commit()
    ingest(s, [NormalizedEvent(provider_event_id="rep-1", channel="email", kind="reply", occurred_at=NOW,
                               email="r@acme.com", data={"text": "hi"})], env.deps, NOW, SCANNERS)
    s.close()
    assert get(env, lid).state == S.BOOKED.value
    assert alerts(env, "integration_error") == []  # no illegal BOOKED -> ENGAGED attempt


def test_booking_committed_during_the_classify_call_discards_the_result(env):
    lid = new_lead(env, verify_status="valid", active_channel="email", tool_lead_id="t-1",
                   email="c@acme.com", state=S.ENGAGED)

    def classify(session, lead, event, now):
        with env.factory() as other:
            other.get(Lead, lid).state = S.BOOKED.value
            other.commit()
        return [SetState(S.QUALIFYING, "reply_interested"), SendReply("booking_link")]

    deps = make_deps(tool=env.tool, classify=classify, jobs=JobQueue())
    with env.factory() as s:
        ingest(s, [NormalizedEvent(provider_event_id="rep-2", channel="email", kind="reply", occurred_at=NOW,
                                   email="c@acme.com", data={"text": "yes"})], deps, NOW, SCANNERS)
        job_id = s.scalars(select(Job.id)).one()
    assert run_job(env.factory, job_id, deps, lambda: NOW) == "obsolete"
    with env.factory() as s:
        job = s.get(Job, job_id)
        assert (job.status, job.last_error) == ("done", "superseded:state_changed")
    assert get(env, lid).state == S.BOOKED.value
    assert deps.outreach.calls == []


def test_newer_inbound_during_the_classify_call_discards_the_result(env):
    lid = new_lead(env, verify_status="valid", active_channel="email", tool_lead_id="t-1",
                   email="d@acme.com", state=S.ENGAGED)

    def classify(session, lead, event, now):
        if event.data.get("text") == "first":
            with env.factory() as other:
                other.add(Event(lead_id=lid, channel="email", kind="reply", occurred_at=NOW, received_at=NOW,
                                verdict="human", processed=True, data={"text": "second"}))
                other.commit()
        return [SendReply("booking_link")]

    deps = make_deps(tool=env.tool, classify=classify, jobs=JobQueue())
    with env.factory() as s:
        ingest(s, [NormalizedEvent(provider_event_id="rep-3", channel="email", kind="reply", occurred_at=NOW,
                                   email="d@acme.com", data={"text": "first"})], deps, NOW, SCANNERS)
        job_id = s.scalars(select(Job.id)).one()
    assert run_job(env.factory, job_id, deps, lambda: NOW) == "obsolete"
    assert deps.outreach.calls == []


def test_wa_no_response_skips_a_lead_that_engaged_after_pick(env, monkeypatch):
    lid = new_lead(env, **wa_kw(with_email=True, wa_template_sent_at=NOW - timedelta(hours=96),
                                wa_reminder_sent_at=NOW - timedelta(hours=49)))
    real_pick = worker._pick

    def pick(session, stmt, keep, limit):
        rows = real_pick(session, stmt, keep, limit)
        if any(getattr(r, "id", None) == lid for r in rows):
            with env.factory() as other:
                other.get(Lead, lid).state = S.ENGAGED.value
                other.commit()
        return rows

    monkeypatch.setattr(worker, "_pick", pick)
    run(env, NOW)
    lead = get(env, lid)
    assert lead.state == S.ENGAGED.value and lead.active_channel == "whatsapp" and not lead.channel_switched
    assert calls(env, "add_lead") == []


def test_flag_committed_between_pick_and_one_is_not_lost(env, monkeypatch):
    lid = new_lead(env, **wa_kw(wa_template_sent_at=NOW - timedelta(hours=49)))
    real_pick = worker._pick

    def pick(session, stmt, keep, limit):
        rows = real_pick(session, stmt, keep, limit)
        if any(getattr(r, "id", None) == lid for r in rows):
            with env.factory() as other:
                lead = other.get(Lead, lid)
                lead.variant_flags = {**(lead.variant_flags or {}), "concurrent": "kept"}
                other.commit()
        return rows

    monkeypatch.setattr(worker, "_pick", pick)
    run(env, NOW)
    flags = get(env, lid).variant_flags
    assert flags.get("concurrent") == "kept"
    assert flags.get("wa_reminder_attempted_at")  # the reminder job ran on the fresh row


# ====================================================================== A3 no stuck leads

@pytest.mark.parametrize("code", [130429, 131056, 131048, 131000, 1, 2, 4])
def test_transient_intro_failure_backs_off_and_retries(env, code):
    lid = new_lead(env, **wa_kw())
    env.wa.client.fail_code = code
    run(env, NOW)
    lead = get(env, lid)
    assert lead.state == S.IN_SEQUENCE.value and lead.active_channel == "whatsapp"
    assert INTRO_ATTEMPT not in lead.variant_flags
    assert lead.variant_flags["wa_retry_after"] == (NOW + timedelta(minutes=10)).isoformat()
    run(env, NOW + timedelta(minutes=5))
    assert len(env.wa.calls) == 1
    env.wa.client.fail_code = None
    run(env, NOW + timedelta(minutes=10))
    assert len(env.wa.calls) == 2
    lead = get(env, lid)
    assert lead.wa_template_sent_at == NOW + timedelta(minutes=10)
    assert "wa_retry_after" not in lead.variant_flags


def test_transient_intro_failure_becomes_permanent_after_three(env):
    lid = new_lead(env, **wa_kw(with_email=True))
    env.wa.client.fail_code = 130429
    t = NOW
    for _ in range(4):
        run(env, t)
        t += timedelta(hours=7)
    lead = get(env, lid)
    assert len(env.wa.calls) == 4
    assert lead.active_channel == "email" and lead.channel_switched  # fell back to email
    assert calls(env, "add_lead")


def test_permanent_intro_failure_without_email_goes_to_nurture(env):
    lid = new_lead(env, **wa_kw())
    env.wa.client.fail_code = 999
    run(env, NOW)
    assert get(env, lid).state == S.NURTURE.value
    with env.factory() as s:
        reasons = [e.data.get("reason") for e in s.scalars(select(Event).where(Event.kind == "state_change"))]
    assert reasons[-1] == "wa_failed"


def test_permanent_intro_failure_with_email_falls_back(env):
    lid = new_lead(env, **wa_kw(with_email=True))
    env.wa.client.fail_code = 999
    run(env, NOW)
    lead = get(env, lid)
    assert lead.active_channel == "email" and calls(env, "add_lead")


def test_2xx_without_message_id_is_an_unknown_outcome_never_resent(env):
    lid = new_lead(env, **wa_kw())

    def no_id(*a, **k):
        env.wa.client.calls.append(("send_template",))
        raise WaSendError(None, 200)

    env.wa.client.send_template = no_id
    run(env, NOW)
    run(env, NOW + timedelta(minutes=30))
    lead = get(env, lid)
    assert len(env.wa.calls) == 1
    assert lead.variant_flags.get(INTRO_ATTEMPT) and lead.state == S.IN_SEQUENCE.value


def test_unconfirmed_template_gives_up_after_24h(env):
    lid = new_lead(env, **wa_kw(with_email=True, variant_flags={INTRO_ATTEMPT: NOW.isoformat()}))
    run(env, NOW + timedelta(hours=2))
    assert len(alerts(env, "wa_send_failed")) == 1
    run(env, NOW + timedelta(hours=20))
    assert get(env, lid).active_channel == "whatsapp"
    run(env, NOW + timedelta(hours=27))
    lead = get(env, lid)
    assert lead.active_channel == "email" and lead.channel_switched
    run(env, NOW + timedelta(hours=28))
    assert len(calls(env, "add_lead")) == 1


def test_unconfirmed_template_without_email_goes_to_nurture(env):
    lid = new_lead(env, **wa_kw(variant_flags={INTRO_ATTEMPT: NOW.isoformat()}))
    run(env, NOW + timedelta(hours=2))
    run(env, NOW + timedelta(hours=27))
    assert get(env, lid).state == S.NURTURE.value


def test_email_start_failed_moves_the_lead_to_nurture(env):
    env.tool.fail.add("add_lead")
    lid = new_lead(env, verify_status="valid", active_channel="email")
    t = NOW
    for _ in range(4):
        run(env, t)
        t += timedelta(minutes=30)
    lead = get(env, lid)
    assert lead.state == S.NURTURE.value and lead.variant_flags["start_failed"]
    assert len(alerts(env, "unsure")) == 1


def test_stuck_lead_alerts_once(env):
    lid = new_lead(env, verify_status="valid", active_channel="email", tool_lead_id="t-1",
                   created_at=NOW - timedelta(days=5))
    young = new_lead(env, verify_status="valid", active_channel="email", tool_lead_id="t-2",
                     created_at=NOW - timedelta(days=1))
    sent = new_lead(env, verify_status="valid", active_channel="email", tool_lead_id="t-3", step_sent=1,
                    last_sent_at=NOW - timedelta(days=4), created_at=NOW - timedelta(days=5))
    run(env, NOW)
    run(env, NOW + timedelta(hours=2))
    stuck = alerts(env, "stuck_lead")
    assert [a.lead_id for a in stuck] == [lid]
    assert young and sent


def test_stuck_whatsapp_lead_alerts(env):
    lid = new_lead(env, **wa_kw(created_at=NOW - timedelta(days=4),
                                variant_flags={INTRO_ATTEMPT: (NOW - timedelta(days=4)).isoformat(),
                                               INTRO_ATTEMPT + "_unconfirmed": NOW.isoformat()}))
    run(env, NOW)
    assert [a.lead_id for a in alerts(env, "stuck_lead")] == [lid]


# ---------------------------------------------------------------- Teams commands

@pytest.fixture
def app_env(env):
    app = create_app(env.settings, deps=env.deps, engine=env.engine, clock=lambda: NOW, scanners=SCANNERS)
    return SimpleNamespace(client=TestClient(app, raise_server_exceptions=False), **vars(env))


def teams(app_env, command):
    import json

    body = json.dumps(activity(command)).encode()
    resp = app_env.client.post("/teams/commands", content=body, headers=teams_headers(body))
    assert resp.status_code == 200
    return resp.json()["text"]


def test_teams_retry_clears_attempt_and_failure_flags(app_env):
    lid = new_lead(app_env, **wa_kw(variant_flags={INTRO_ATTEMPT: NOW.isoformat(), "start_attempts": 2,
                                                   "wa_retry_after": NOW.isoformat(),
                                                   INTRO_ATTEMPT + "_failed": 999}))
    assert "will be retried" in teams(app_env, f"retry {lid}")
    flags = get(app_env, lid).variant_flags
    assert not ({INTRO_ATTEMPT, "start_attempts", "wa_retry_after", INTRO_ATTEMPT + "_failed"} & set(flags))
    run(app_env, NOW + timedelta(minutes=1))
    assert get(app_env, lid).wa_template_sent_at is not None


def test_teams_retry_refused_outside_the_sequence(app_env):
    lid = new_lead(app_env, verify_status="valid", active_channel="email", state=S.NURTURE)
    assert "in_sequence" in teams(app_env, f"retry {lid}")


def test_teams_nurture_stops_outreach(app_env):
    lid = new_lead(app_env, verify_status="valid", active_channel="email", tool_lead_id="t-1")
    assert "nurture" in teams(app_env, f"nurture {lid}")
    assert get(app_env, lid).state == S.NURTURE.value
    assert calls(app_env, "stop") == [("stop", "t-1")]
    with app_env.factory() as s:
        last = s.scalars(select(Event).where(Event.kind == "state_change").order_by(Event.id.desc())).first()
        assert last.data["reason"] == "manual"


def test_teams_close_stops_outreach(app_env):
    lid = new_lead(app_env, verify_status="valid", active_channel="email", tool_lead_id="t-1")
    assert "closed" in teams(app_env, f"close {lid}")
    assert get(app_env, lid).state == S.CLOSED.value
    assert calls(app_env, "stop") == [("stop", "t-1")]


def test_teams_close_illegal_transition_changes_nothing(app_env):
    lid = new_lead(app_env, verify_status="valid", active_channel="email", state=S.SUPPRESSED)
    answer = teams(app_env, f"close {lid}")
    assert "cannot" in answer.lower()
    assert get(app_env, lid).state == S.SUPPRESSED.value


def test_teams_status_has_no_pii(app_env):
    lid = new_lead(app_env, verify_status="valid", active_channel="email", email="secret@acme.com",
                   name="Secret Name", variant_flags={"start_failed": NOW.isoformat()})
    answer = teams(app_env, f"status {lid}")
    assert f"lead {lid}" in answer and "in_sequence" in answer and "email" in answer and "start_failed" in answer
    assert "secret" not in answer.lower()


def test_teams_help_lists_the_new_commands(app_env):
    answer = teams(app_env, "help")
    for word in ("retry", "nurture", "close", "status"):
        assert word in answer


def test_teams_unknown_lead(app_env):
    assert "not found" in teams(app_env, "status 99999")


# ====================================================================== A4 once-only sends

def test_pricing_click_booking_slots_once_per_7_days():
    first = decide(snap(S.IN_SEQUENCE), ev("click", tag="pricing"), NOW)
    assert SendReply("booking_slots") in first
    flag = [a for a in first if getattr(a, "key", None) == "booking_slots_at"]
    assert flag
    flags = MappingProxyType({"booking_slots_at": NOW.isoformat(), "hot_alerted": True})
    again = decide(snap(S.IN_SEQUENCE, flags=flags), ev("click", tag="pricing", at=NOW + timedelta(days=3)), NOW)
    assert SendReply("booking_slots") not in again
    later = decide(snap(S.IN_SEQUENCE, flags=flags), ev("click", tag="pricing", at=NOW + timedelta(days=8)), NOW)
    assert SendReply("booking_slots") in later


@pytest.mark.parametrize("state", [S.PAUSED, S.NURTURE])
def test_pricing_click_never_sends_slots_to_paused_or_nurture(state):
    out = decide(snap(state), ev("click", tag="pricing"), NOW)
    assert SendReply("booking_slots") not in out and WaSendText("booking_slots") not in out


def _rc(label):
    return ReplyClass(label=label, confidence=0.9, reason="r", return_date=None, later_date=None,
                      referral_email=None, referral_phone=None, referral_name=None, draft_answer=None)


def test_interested_while_qualifying_with_a_recent_link_alerts_instead():
    lead = SimpleNamespace(id=5, state=S.QUALIFYING.value, active_channel="email", email="x@acme.com",
                           domain="acme.com",
                           variant_flags={"sent_texts": {"booking_link": (NOW - timedelta(hours=2)).isoformat()}})
    event = SimpleNamespace(data={}, channel="email")
    out = actions_for_class(_rc("interested"), lead, event, NOW)
    assert SendReply("booking_link") not in out
    assert Alert("engaged") in out


def test_interested_while_qualifying_with_an_old_link_resends():
    lead = SimpleNamespace(id=5, state=S.QUALIFYING.value, active_channel="email", email="x@acme.com",
                           domain="acme.com",
                           variant_flags={"sent_texts": {"booking_link": (NOW - timedelta(hours=30)).isoformat()}})
    out = actions_for_class(_rc("interested"), lead, SimpleNamespace(data={}, channel="email"), NOW)
    assert SendReply("booking_link") in out


def test_successful_send_records_when_the_text_was_sent(env):
    lid = new_lead(env, verify_status="valid", active_channel="email", tool_lead_id="t-1", state=S.QUALIFYING)
    do(env, lid, [SendReply("booking_link")])
    assert get(env, lid).variant_flags["sent_texts"]["booking_link"] == NOW.isoformat()


@pytest.mark.parametrize("state", [S.SUPPRESSED, S.CLOSED])
def test_send_email_text_refuses_terminal_leads(env, state):
    lid = new_lead(env, verify_status="valid", active_channel="email", tool_lead_id="t-1", state=state)
    with env.factory() as s:
        assert env.deps.outreach.send_email_text(s, s.get(Lead, lid), "booking_link", NOW) is False
    assert calls(env, "reply") == []


def test_send_email_text_refuses_a_suppressed_address(env):
    lid = new_lead(env, email="sup@acme.com", verify_status="valid", active_channel="email", tool_lead_id="t-1",
                   state=S.QUALIFYING)
    with env.factory() as s:
        s.add(Suppression(value="sup@acme.com", kind="email", reason="x", created_at=NOW))
        s.commit()
        assert env.deps.outreach.send_email_text(s, s.get(Lead, lid), "booking_link", NOW) is False
    assert calls(env, "reply") == []


def test_whatsapp_automated_sends_refused_for_terminal_leads(env):
    lid = new_lead(env, **wa_kw(state=S.CLOSED, wa_window_until=NOW + timedelta(hours=5)))
    with env.factory() as s:
        lead = s.get(Lead, lid)
        with pytest.raises(WhatsAppNotAllowed):
            env.wa.send_text(s, lead, "booking_link", NOW)
        with pytest.raises(WhatsAppNotAllowed):
            env.wa.send_template(s, lead, "ozoo_intro", NOW)
        with pytest.raises(WhatsAppNotAllowed):
            env.wa.send_raw_text(s, lead, "bot text", NOW, "bot")
        env.wa.send_raw_text(s, lead, "a person answers", NOW, "human")  # sales may still answer
    assert len(env.wa.calls) == 1


def test_email_enrol_marker_is_committed_before_add_lead(env):
    seen = {}
    real_add = env.tool.add_lead

    def spy(campaign_id, lead, variables):
        with env.factory() as other:
            seen["flag"] = (other.get(Lead, lid).variant_flags or {}).get("email_enroll_attempted_at")
        return real_add(campaign_id, lead, variables)

    env.tool.add_lead = spy
    lid = new_lead(env, verify_status="valid", active_channel="email")
    run(env, NOW)
    assert seen["flag"] == NOW.isoformat()


def test_email_enrol_marker_looks_the_lead_up_before_re_adding(env):
    lid = new_lead(env, email="m@acme.com", verify_status="valid", active_channel="email")
    tid = env.tool.add_lead("c1", SimpleNamespace(id=lid, email="m@acme.com"), {})  # the tool has it
    with env.factory() as s:
        lead = s.get(Lead, lid)
        lead.variant_flags = {"email_enroll_attempted_at": NOW.isoformat()}  # our commit was lost
        s.commit()
    run(env, NOW + timedelta(minutes=1))
    assert len(calls(env, "add_lead")) == 1
    assert get(env, lid).tool_lead_id == tid


def test_email_enrol_marker_re_adds_when_the_tool_has_no_lead(env):
    lid = new_lead(env, email="n@acme.com", verify_status="valid", active_channel="email",
                   variant_flags={"email_enroll_attempted_at": NOW.isoformat()})
    run(env, NOW + timedelta(minutes=1))
    assert len(calls(env, "add_lead")) == 1 and calls(env, "find_lead")
    assert get(env, lid).tool_lead_id


# ====================================================================== A5 SQLite

def test_sqlite_file_engine_uses_wal_and_busy_timeout(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'w.db'}")
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 5000
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
    engine.dispose()


def test_sqlite_memory_engine_unchanged():
    engine = make_engine("sqlite://")
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar().lower() == "memory"
    engine.dispose()
