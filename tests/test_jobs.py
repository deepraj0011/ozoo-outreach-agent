"""Task 12a: LLM classification and chatbot replies run as background jobs, never inside a
webhook request (outreach.jobs)."""
from __future__ import annotations

import logging
import threading
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

import outreach.app as app_mod
from outreach.actions import Alert, BotReply, Classify, SetState, WaSendRaw, execute
from outreach.app import create_app, default_deps
from outreach.botfilter import Scanners
from outreach.db import SessionLocal, init_db, make_engine
from outreach.emailtool.base import NormalizedEvent
from outreach.emailtool.instantly import SECRET_HEADER
from outreach.handoff import start_takeover
from outreach.ingest import ingest
from outreach.jobs import MAX_ATTEMPTS, JobQueue, run_due, run_job
from outreach.models import Alert as AlertRow
from outreach.models import Event, Job, Lead
from outreach.states import LeadState as S
from tests.conftest import NOW, make_lead
from tests.fakes import WEBHOOK_SECRET, fixture_body, make_app_engine, make_deps, make_settings

SCANNERS = Scanners(ua_substrings=(), cidrs=(), apple_proxy_cidrs=())
PHONE = "+919800000000"
SECRET_TEXT = "my-secret-lead-message-text"


class Counter:
    """Counting LLM-backed port: records calls, returns scripted actions or raises."""

    def __init__(self, actions=None, exc=None):
        self.calls = []
        self.actions = actions or []
        self.exc = exc

    def classify(self, session, lead, event, now):
        self.calls.append((lead.id, event.id))
        if self.exc is not None:
            raise self.exc
        return list(self.actions)

    def bot(self, session, lead, now):
        self.calls.append((lead.id,))
        if self.exc is not None:
            raise self.exc
        return list(self.actions)


@pytest.fixture
def env():
    engine = make_app_engine()
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    s = SessionLocal(bind=engine)
    yield engine, factory, s
    s.close()
    engine.dispose()


def jdeps(classify=None, bot=None, **kw):
    return make_deps(classify=classify, bot_reply=bot, jobs=JobQueue(), **kw)


def count(s, model, *where):
    return s.scalar(select(func.count()).select_from(model).where(*where))


def clock(at):
    return lambda: at


def reply_ev(pid="r1", email="lead@example.com", text=SECRET_TEXT):
    return NormalizedEvent(provider_event_id=pid, channel="email", kind="reply", occurred_at=NOW,
                           email=email, data={"text": text})


def wa_ev(pid, text=SECRET_TEXT, at=NOW):
    return NormalizedEvent(provider_event_id=pid, channel="whatsapp", kind="wa_text", occurred_at=at,
                           phone=PHONE, data={"text": text, "wamid": pid})


def wa_lead(s, **kw):
    d = dict(phone=PHONE, email=None, consent_whatsapp=True, active_channel="whatsapp", state=S.QUALIFYING)
    d.update(kw)
    lead = make_lead(s, **d)
    s.commit()
    return lead


def only_job(s, **where):
    """The single matching job; ends the read so the shared test connection is free for run_job."""
    s.commit()
    job = s.scalars(select(Job).filter_by(**where)).one()
    s.commit()
    return job


# ------------------------------------------------------------------ wiring


def test_default_deps_wires_a_job_queue():
    assert isinstance(default_deps(make_settings()).jobs, JobQueue)


def test_create_app_always_wires_a_job_queue():
    engine = make_app_engine()
    deps = make_deps()
    assert deps.jobs is None
    create_app(make_settings(), deps=deps, engine=engine, clock=lambda: NOW, scanners=SCANNERS)
    assert isinstance(deps.jobs, JobQueue)
    engine.dispose()


# ------------------------------------------------------------------ enqueue


def test_enqueue_makes_no_llm_call_and_dedupes(env):
    _, _, s = env
    c = Counter()
    deps = jdeps(classify=c.classify, bot=c.bot)
    lead = make_lead(s, email="lead@example.com")
    s.commit()
    ids = ingest(s, [reply_ev()], deps, NOW, SCANNERS)
    assert c.calls == []
    job = only_job(s)
    assert ids == [job.id]
    ev = s.scalars(select(Event).where(Event.kind == "reply")).one()
    assert (job.kind, job.status, job.attempts, job.lead_id, job.event_id) == ("classify", "pending", 0, lead.id, ev.id)
    assert job.dedupe_key == f"classify:{ev.id}" and job.run_after == NOW and job.created_at == NOW
    # same event executed again (e.g. a replay): no second job, no error
    execute(s, s.get(Lead, lead.id), [Classify()], deps, NOW, event=ev)
    s.commit()
    assert count(s, Job) == 1


def test_bot_reply_dedupe_key(env):
    _, _, s = env
    c = Counter()
    deps = jdeps(bot=c.bot)
    lead = wa_lead(s)
    ingest(s, [wa_ev("wa:1")], deps, NOW, SCANNERS)
    ev = s.scalars(select(Event).where(Event.kind == "wa_text")).one()
    job = only_job(s)
    assert job.kind == "bot_reply" and job.dedupe_key == f"bot:{lead.id}:{ev.id}" and c.calls == []


def test_ingest_reports_no_ids_for_events_without_jobs(env):
    _, _, s = env
    make_lead(s, email="lead@example.com")
    s.commit()
    ev = NormalizedEvent(provider_event_id="u1", channel="email", kind="unsubscribe", occurred_at=NOW,
                         email="lead@example.com")
    assert ingest(s, [ev], jdeps(), NOW, SCANNERS) == []


def test_without_job_queue_the_inline_path_is_kept(env):
    _, _, s = env
    c = Counter([SetState(S.QUALIFYING, "reply_interested")])
    deps = make_deps(classify=c.classify)  # jobs=None: unit-test path
    lead = make_lead(s, email="lead@example.com")
    s.commit()
    assert ingest(s, [reply_ev()], deps, NOW, SCANNERS) == []
    assert len(c.calls) == 1 and count(s, Job) == 0
    assert s.get(Lead, lead.id).state == S.QUALIFYING.value


# ------------------------------------------------------------------ webhook -> background task


def _email_app(deps):
    engine = make_app_engine()
    app = create_app(make_settings(), deps=deps, engine=engine, clock=lambda: NOW, scanners=SCANNERS)
    return engine, TestClient(app, raise_server_exceptions=False)


def _post_reply(client, **changes):
    return client.post("/webhooks/email", content=fixture_body("tool_reply.json", **changes),
                       headers={SECRET_HEADER: WEBHOOK_SECRET})


def test_webhook_makes_no_llm_call_in_the_request(monkeypatch):
    c = Counter([SetState(S.QUALIFYING, "reply_interested"), Alert("hot_lead", "interested")])
    deps = make_deps(classify=c.classify)
    engine, client = _email_app(deps)
    s = SessionLocal(bind=engine)
    lead = make_lead(s, email="lead@example.com")
    s.commit()
    scheduled = []
    monkeypatch.setattr(app_mod, "run_job", lambda factory, job_id, d, now_fn: scheduled.append(job_id))
    assert _post_reply(client).status_code == 200
    assert c.calls == []  # no LLM call while the request was open
    job = only_job(s)
    job_id = job.id
    assert scheduled == [job_id] and job.status == "pending"
    s.expire_all()
    assert s.get(Lead, lead.id).state == S.ENGAGED.value
    s.commit()
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    assert run_job(factory, job_id, deps, clock(NOW)) == "done"
    assert len(c.calls) == 1
    s.expire_all()
    assert s.get(Lead, lead.id).state == S.QUALIFYING.value
    assert s.get(Job, job_id).status == "done" and s.get(Job, job_id).finished_at == NOW
    assert ("send", "hot_lead", lead.id) in deps.alerter.calls
    s.close()
    engine.dispose()


def test_webhook_background_task_runs_the_job():
    c = Counter([SetState(S.QUALIFYING, "reply_interested"), Alert("hot_lead", "interested")])
    deps = make_deps(classify=c.classify)
    engine, client = _email_app(deps)
    s = SessionLocal(bind=engine)
    lead = make_lead(s, email="lead@example.com")
    s.commit()
    assert _post_reply(client).status_code == 200  # TestClient runs background tasks before returning
    assert len(c.calls) == 1
    s.expire_all()
    assert s.get(Lead, lead.id).state == S.QUALIFYING.value
    assert only_job(s).status == "done"
    assert count(s, AlertRow, AlertRow.kind == "hot_lead") == 1
    s.commit()
    # duplicate delivery of the same webhook: no second job, no second LLM call
    assert _post_reply(client).status_code == 200
    assert count(s, Job) == 1 and len(c.calls) == 1
    s.close()
    engine.dispose()


def test_whatsapp_webhook_schedules_bot_job(monkeypatch):
    import hashlib
    import hmac
    import json
    from pathlib import Path

    c = Counter([WaSendRaw("hello")])
    deps = make_deps(bot_reply=c.bot)
    engine = make_app_engine()
    app = create_app(make_settings(), deps=deps, engine=engine, clock=lambda: NOW, scanners=SCANNERS)
    client = TestClient(app, raise_server_exceptions=False)
    s = SessionLocal(bind=engine)
    data = json.loads((Path(__file__).parent / "fixtures" / "wa_text.json").read_text())
    msg = data["entry"][0]["changes"][0]["value"]["messages"][0]
    wa_lead(s, phone="+" + msg["from"].lstrip("+"))
    body = json.dumps(data).encode()
    sig = "sha256=" + hmac.new(b"wa-app-secret", body, hashlib.sha256).hexdigest()
    scheduled = []
    monkeypatch.setattr(app_mod, "run_job", lambda f, job_id, d, n: scheduled.append(job_id))
    r = client.post("/webhooks/whatsapp", content=body,
                    headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})
    assert r.status_code == 200 and c.calls == []
    assert scheduled == [only_job(s, kind="bot_reply").id]
    s.close()
    engine.dispose()


# ------------------------------------------------------------------ claiming


def _classify_job(s, deps):
    lead = make_lead(s, email="lead@example.com")
    s.commit()
    ingest(s, [reply_ev()], deps, NOW, SCANNERS)
    return lead, only_job(s)


def test_run_job_twice_second_is_skipped(env):
    _, factory, s = env
    c = Counter()
    deps = jdeps(classify=c.classify)
    _, job = _classify_job(s, deps)
    assert run_job(factory, job.id, deps, clock(NOW)) == "done"
    assert run_job(factory, job.id, deps, clock(NOW)) == "skipped"
    assert len(c.calls) == 1
    s.expire_all()
    assert s.get(Job, job.id).attempts == 1


def test_run_job_not_due_yet_is_skipped(env):
    _, factory, s = env
    c = Counter()
    deps = jdeps(classify=c.classify)
    _, job = _classify_job(s, deps)
    assert run_job(factory, job.id, deps, clock(NOW - timedelta(seconds=1))) == "skipped"
    assert run_job(factory, 999_999, deps, clock(NOW)) == "skipped"
    assert c.calls == []


def test_concurrent_claim_exactly_one_wins(tmp_path):
    from outreach.jobs import _claim

    engine = make_engine(f"sqlite:///{tmp_path / 'jobs.db'}", connect_args={"timeout": 30})
    init_db(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    s = factory()
    lead = make_lead(s)
    job = Job(lead_id=lead.id, kind="classify", dedupe_key="classify:x", status="pending",
              attempts=0, run_after=NOW, created_at=NOW)
    s.add(job)
    s.commit()
    job_id = job.id
    s.close()
    barrier = threading.Barrier(4)
    results = []

    def claim():
        barrier.wait()
        results.append(_claim(factory, job_id, NOW))

    threads = [threading.Thread(target=claim) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results, key=str) == [1, None, None, None]  # one winner, with attempt 1
    s = factory()
    assert s.get(Job, job_id).attempts == 1 and s.get(Job, job_id).status == "running"
    s.close()
    engine.dispose()


# ------------------------------------------------------------------ failures and retries


def test_classify_failure_retries_with_backoff_then_fails(env, caplog):
    _, factory, s = env
    c = Counter(exc=RuntimeError(SECRET_TEXT))
    deps = jdeps(classify=c.classify)
    lead, job = _classify_job(s, deps)
    caplog.set_level(logging.DEBUG)
    assert run_job(factory, job.id, deps, clock(NOW)) == "retry"
    s.expire_all()
    j = s.get(Job, job.id)
    assert (j.status, j.attempts, j.run_after, j.last_error) == ("pending", 1, NOW + timedelta(minutes=1),
                                                                 "RuntimeError")
    s.commit()
    assert run_job(factory, job.id, deps, clock(NOW + timedelta(seconds=30))) == "skipped"  # backoff
    t2 = NOW + timedelta(minutes=1)
    assert run_job(factory, job.id, deps, clock(t2)) == "retry"
    s.expire_all()
    assert s.get(Job, job.id).run_after == t2 + timedelta(minutes=5)
    s.commit()
    t3 = t2 + timedelta(minutes=5)
    assert run_job(factory, job.id, deps, clock(t3)) == "failed"
    assert len(c.calls) == MAX_ATTEMPTS == 3
    s.expire_all()
    j = s.get(Job, job.id)
    assert (j.status, j.attempts, j.finished_at, j.last_error) == ("failed", 3, t3, "RuntimeError")
    assert count(s, AlertRow, AlertRow.kind == "integration_error", AlertRow.lead_id == lead.id) == 1
    unsure = s.scalars(select(AlertRow).where(AlertRow.kind == "unsure")).one()
    assert unsure.text == f"reply from lead {lead.id} could not be classified — please read it"
    # nothing rolled back by the failures: the event stays, the lead is still engaged
    assert s.get(Lead, lead.id).state == S.ENGAGED.value
    # no message text anywhere in logs or in last_error
    assert SECRET_TEXT not in caplog.text
    for row in s.scalars(select(AlertRow).where(AlertRow.kind == "integration_error")):
        assert SECRET_TEXT not in row.text
    s.commit()
    assert run_job(factory, job.id, deps, clock(t3 + timedelta(hours=1))) == "skipped"


def test_failed_attempt_rolls_back_partial_work(env):
    _, factory, s = env

    def classify(session, lead, event, now):
        lead.score = 99  # written, then the call fails
        raise ValueError("boom")

    deps = jdeps(classify=classify)
    lead, job = _classify_job(s, deps)
    assert run_job(factory, job.id, deps, clock(NOW)) == "retry"
    s.expire_all()
    assert s.get(Lead, lead.id).score == 0


def test_bot_failure_after_three_attempts_starts_takeover(env):
    _, factory, s = env
    c = Counter(exc=RuntimeError(SECRET_TEXT))
    deps = jdeps(bot=c.bot)
    deps.takeover = lambda session, lead, reason, now: start_takeover(session, lead, reason, deps, now)
    lead = wa_lead(s)
    ingest(s, [wa_ev("wa:1")], deps, NOW, SCANNERS)
    job = only_job(s)
    t = NOW
    for expected in ("retry", "retry", "failed"):
        assert run_job(factory, job.id, deps, clock(t)) == expected
        t += timedelta(minutes=5)
    s.expire_all()
    assert s.get(Job, job.id).status == "failed" and len(c.calls) == 3
    lead = s.get(Lead, lead.id)
    assert lead.variant_flags["takeover_reason"] == "bot_error"
    assert count(s, AlertRow, AlertRow.kind == "wa_human_needed") == 1
    assert count(s, AlertRow, AlertRow.kind == "integration_error") == 1


# ------------------------------------------------------------------ run_due


def _make_jobs(s, n, created_at):
    lead = make_lead(s)
    ids = []
    for i in range(n):
        j = Job(lead_id=lead.id, kind="classify", dedupe_key=f"classify:t{created_at.isoformat()}:{i}",
                status="pending", attempts=0, run_after=created_at, created_at=created_at)
        s.add(j)
        s.flush()
        ids.append(j.id)
    s.commit()
    return lead, ids


def test_run_due_respects_grace_and_limit(env):
    _, factory, s = env
    deps = jdeps(classify=Counter().classify)
    _, fresh = _make_jobs(s, 2, NOW)  # just enqueued: the request's background task goes first
    _, old = _make_jobs(s, 3, NOW - timedelta(minutes=5))
    assert run_due(factory, deps, clock(NOW), limit=2) == 2
    s.expire_all()
    assert [s.get(Job, i).status for i in old] == ["done", "done", "pending"]
    assert [s.get(Job, i).status for i in fresh] == ["pending", "pending"]
    s.commit()
    assert run_due(factory, deps, clock(NOW)) == 1
    s.commit()
    assert run_due(factory, deps, clock(NOW + timedelta(minutes=2))) == 2
    s.expire_all()
    assert all(s.get(Job, i).status == "done" for i in fresh + old)


def test_run_due_resets_stale_running_jobs(env):
    _, factory, s = env
    deps = jdeps(classify=Counter().classify)
    _, (stale, recent) = _make_jobs(s, 2, NOW - timedelta(hours=1))
    s.get(Job, stale).status = "running"
    s.get(Job, stale).attempts = 1
    s.get(Job, stale).started_at = NOW - timedelta(minutes=11)
    s.get(Job, recent).status = "running"
    s.get(Job, recent).attempts = 1
    s.get(Job, recent).started_at = NOW - timedelta(minutes=9)
    s.commit()
    assert run_due(factory, deps, clock(NOW)) == 1
    s.expire_all()
    assert s.get(Job, stale).status == "done" and s.get(Job, stale).attempts == 2
    assert s.get(Job, recent).status == "running"


def test_run_due_gives_up_on_stale_job_out_of_attempts(env):
    _, factory, s = env
    c = Counter()
    deps = jdeps(classify=c.classify)
    lead, (job_id,) = _make_jobs(s, 1, NOW - timedelta(hours=1))
    j = s.get(Job, job_id)
    j.status, j.attempts, j.started_at = "running", MAX_ATTEMPTS, NOW - timedelta(minutes=30)
    s.commit()
    assert run_due(factory, deps, clock(NOW)) == 0
    s.expire_all()
    assert s.get(Job, job_id).status == "failed" and s.get(Job, job_id).last_error == "Stale"
    assert count(s, AlertRow, AlertRow.kind == "unsure") == 1 and c.calls == []


# ------------------------------------------------------------------ coalescing and skips


def test_two_quick_messages_one_bot_call_for_the_newer_job(env):
    _, factory, s = env
    c = Counter([WaSendRaw("answer")])
    deps = jdeps(bot=c.bot)
    lead = wa_lead(s)
    ingest(s, [wa_ev("wa:1")], deps, NOW, SCANNERS)
    ingest(s, [wa_ev("wa:2", at=NOW + timedelta(seconds=5))], deps, NOW + timedelta(seconds=5), SCANNERS)
    s.commit()
    first, second = s.scalars(select(Job).order_by(Job.id)).all()
    s.commit()
    later = clock(NOW + timedelta(minutes=10))
    assert run_job(factory, first.id, deps, later) == "obsolete"
    assert c.calls == []
    assert run_job(factory, second.id, deps, later) == "done"
    assert c.calls == [(lead.id,)]
    assert deps.wa.calls == [("send_raw_text", lead.id, "answer", "bot")]
    s.expire_all()
    assert s.get(Job, first.id).status == "done" and s.get(Job, second.id).status == "done"


def test_bot_job_skipped_when_takeover_started_after_enqueue(env):
    _, factory, s = env
    c = Counter([WaSendRaw("answer")])
    deps = jdeps(bot=c.bot)
    lead = wa_lead(s)
    ingest(s, [wa_ev("wa:1")], deps, NOW, SCANNERS)
    job = only_job(s)
    s.get(Lead, lead.id).variant_flags = {"takeover_at": NOW.isoformat()}
    s.commit()
    assert run_job(factory, job.id, deps, clock(NOW)) == "obsolete"
    assert c.calls == [] and deps.wa.calls == []
    s.expire_all()
    assert s.get(Job, job.id).status == "done"


def test_bot_job_skipped_when_a_person_owns_the_chat(env):
    _, factory, s = env
    c = Counter([WaSendRaw("answer")])
    deps = jdeps(bot=c.bot)
    lead = wa_lead(s)
    ingest(s, [wa_ev("wa:1")], deps, NOW, SCANNERS)
    job = only_job(s)
    s.get(Lead, lead.id).wa_human_owner = "aad-raj"
    s.commit()
    assert run_job(factory, job.id, deps, clock(NOW)) == "obsolete" and c.calls == []


def test_bot_job_runs_after_takeover_was_released(env):
    _, factory, s = env
    c = Counter([WaSendRaw("answer")])
    deps = jdeps(bot=c.bot)
    lead = wa_lead(s, variant_flags={"takeover_at": NOW.isoformat(), "takeover_released": NOW.isoformat()})
    ingest(s, [wa_ev("wa:1")], deps, NOW, SCANNERS)
    assert run_job(factory, only_job(s).id, deps, clock(NOW)) == "done" and len(c.calls) == 1


@pytest.mark.parametrize("state", [S.SUPPRESSED, S.CLOSED, S.HANDED_OFF])
def test_job_skipped_when_lead_terminal_before_it_runs(env, state):
    _, factory, s = env
    c = Counter([SetState(S.QUALIFYING, "x")])
    deps = jdeps(classify=c.classify)
    lead, job = _classify_job(s, deps)
    s.get(Lead, lead.id).state = state.value
    s.commit()
    assert run_job(factory, job.id, deps, clock(NOW)) == "obsolete"
    assert c.calls == []
    s.expire_all()
    assert s.get(Job, job.id).status == "done"


def test_classify_then_bot_reply_chain_runs_in_the_same_background_task(env):
    _, factory, s = env
    bot = Counter([WaSendRaw("answer")])
    cls = Counter([BotReply()])
    deps = jdeps(classify=cls.classify, bot=bot.bot)
    lead = wa_lead(s, state=S.IN_SEQUENCE)  # wa_text in sequence -> engage -> Classify
    ids = ingest(s, [wa_ev("wa:1")], deps, NOW, SCANNERS)
    assert len(ids) == 1 and cls.calls == [] and bot.calls == []
    assert run_job(factory, ids[0], deps, clock(NOW)) == "done"
    assert len(cls.calls) == 1 and bot.calls == [(lead.id,)]
    s.commit()
    kinds = sorted((j.kind, j.status) for j in s.scalars(select(Job)))
    assert kinds == [("bot_reply", "done"), ("classify", "done")]


def test_run_job_logs_no_message_text(env, caplog):
    _, factory, s = env
    caplog.set_level(logging.DEBUG)
    c = Counter([Alert("question", f"lead asked: {SECRET_TEXT}")])
    deps = jdeps(classify=c.classify)
    _, job = _classify_job(s, deps)
    assert run_job(factory, job.id, deps, clock(NOW)) == "done"
    assert SECRET_TEXT not in caplog.text


# ------------------------------------------------------------------ review round: claim ownership


def _set_job(factory, job_id, **values):
    """A direct DB update from another session, as a concurrent worker would make it."""
    from sqlalchemy import update as sa_update

    s2 = factory()
    s2.execute(sa_update(Job).where(Job.id == job_id).values(**values))
    s2.commit()
    s2.close()


def test_claim_returns_the_claimed_attempt(env):
    from outreach.jobs import _claim

    _, factory, s = env
    _, job = _classify_job(s, jdeps(classify=Counter().classify))
    assert _claim(factory, job.id, NOW) == 1
    assert _claim(factory, job.id, NOW) is None


def test_stale_reset_while_original_runs_discards_the_original_apply(env):
    """Run #1 is slow; meanwhile it is reset as stale and run #2 claims and finishes it. Run #1's
    result must be thrown away: exactly one apply."""
    _, factory, s = env
    applied = []
    state = {"n": 0}

    def classify(session, lead, event, now):
        state["n"] += 1
        if state["n"] == 1:
            session.commit()  # test scaffolding: free the shared connection for the "other worker"
            _set_job(factory, job_id, status="pending", run_after=NOW)  # stale reset
            assert run_job(factory, job_id, deps, clock(NOW)) == "done"  # run #2
        return [SetState(S.QUALIFYING, f"run{state['n']}"), Alert("hot_lead", f"run {state['n']}")]

    deps = jdeps(classify=classify)
    deps.alerter.send = (lambda orig: lambda kind, text, lead, now, session=None: (
        applied.append(text), orig(kind, text, lead, now, session)))(deps.alerter.send)
    lead, job = _classify_job(s, deps)
    job_id = job.id
    assert run_job(factory, job_id, deps, clock(NOW)) == "lost_claim"
    assert applied == ["run 2"]
    s.expire_all()
    j = s.get(Job, job_id)
    assert (j.status, j.attempts) == ("done", 2)
    assert s.get(Lead, lead.id).state == S.QUALIFYING.value
    assert count(s, Event, Event.kind == "state_change", Event.lead_id == lead.id) == 2  # engaged + 1 apply


def test_stale_reset_racing_a_done_commit_leaves_it_done(env, monkeypatch):
    import outreach.jobs as jobs_mod

    _, factory, s = env
    deps = jdeps(classify=Counter().classify)
    _, job = _classify_job(s, deps)
    job_id = job.id
    assert run_job(factory, job_id, deps, clock(NOW)) == "done"
    # the reset saw the job "running" with attempt 1 just before run #1 committed "done"
    monkeypatch.setattr(jobs_mod, "_stale_jobs", lambda session, cutoff: [(job_id, 1)])
    run_due(factory, deps, clock(NOW + timedelta(hours=1)))
    s.expire_all()
    assert s.get(Job, job_id).status == "done" and s.get(Job, job_id).attempts == 1


def test_late_failure_of_original_after_run2_succeeded_leaves_done_no_takeover(env):
    _, factory, s = env
    state = {"n": 0}

    def bot(session, lead, now):
        state["n"] += 1
        if state["n"] == 1:
            session.commit()
            _set_job(factory, job_id, status="pending", run_after=NOW)
            assert run_job(factory, job_id, deps, clock(NOW)) == "done"
            raise RuntimeError("late failure")
        return [WaSendRaw("answer")]

    deps = jdeps(bot=bot)
    deps.takeover = lambda session, lead, reason, now: start_takeover(session, lead, reason, deps, now)
    wa_lead(s)
    ingest(s, [wa_ev("wa:1")], deps, NOW, SCANNERS)
    job_id = only_job(s).id
    _set_job(factory, job_id, attempts=MAX_ATTEMPTS - 1)  # run #1 is the last allowed attempt
    assert run_job(factory, job_id, deps, clock(NOW)) == "lost_claim"
    s.expire_all()
    j = s.get(Job, job_id)
    assert j.status == "done" and j.last_error is None
    assert count(s, AlertRow, AlertRow.kind == "wa_human_needed") == 0
    assert count(s, AlertRow, AlertRow.kind == "integration_error") == 0
    assert deps.wa.calls == [("send_raw_text", j.lead_id, "answer", "bot")]


def test_newer_message_during_the_llm_call_supersedes_the_answer(env):
    _, factory, s = env
    calls = []

    def bot(session, lead, now):
        calls.append(lead.id)
        if len(calls) == 1:
            session.commit()
            s2 = factory()
            ingest(s2, [wa_ev("wa:2", at=NOW + timedelta(seconds=5))], deps, NOW + timedelta(seconds=5),
                   SCANNERS)
            s2.close()
        return [WaSendRaw(f"answer {len(calls)}")]

    deps = jdeps(bot=bot)
    wa_lead(s)
    ingest(s, [wa_ev("wa:1")], deps, NOW, SCANNERS)
    first_id = only_job(s).id
    assert run_job(factory, first_id, deps, clock(NOW)) == "obsolete"
    assert deps.wa.calls == []  # the stale answer was never sent
    s.expire_all()
    first = s.get(Job, first_id)
    assert (first.status, first.last_error) == ("done", "superseded")
    second = s.scalars(select(Job).where(Job.id != first_id)).one()
    s.commit()
    assert run_job(factory, second.id, deps, clock(NOW + timedelta(seconds=10))) == "done"
    assert deps.wa.calls == [("send_raw_text", first.lead_id, "answer 2", "bot")]


def test_failure_never_overwrites_a_reclaimed_job(env):
    _, factory, s = env
    state = {"n": 0}

    def classify(session, lead, event, now):
        state["n"] += 1
        session.commit()
        # another worker reset and re-claimed it (attempt 2) while run #1 was still going
        _set_job(factory, job_id, status="running", attempts=2, started_at=NOW)
        raise RuntimeError("boom")

    deps = jdeps(classify=classify)
    _, job = _classify_job(s, deps)
    job_id = job.id
    assert run_job(factory, job_id, deps, clock(NOW)) == "lost_claim"
    s.expire_all()
    j = s.get(Job, job_id)
    assert (j.status, j.attempts, j.last_error) == ("running", 2, None)


# ------------------------------------------------------------------ review round: never raises


def test_run_job_never_raises_when_rollback_fails(env, caplog):
    _, factory, s = env
    caplog.set_level(logging.DEBUG)

    class BadRollback(type(factory())):
        def rollback(self):
            raise RuntimeError(SECRET_TEXT)

    bad_factory = sessionmaker(bind=factory.kw["bind"], class_=BadRollback, autoflush=False,
                               expire_on_commit=False)
    deps = jdeps(classify=Counter(exc=ValueError("x")).classify)
    _, job = _classify_job(s, deps)
    assert run_job(bad_factory, job.id, deps, clock(NOW)) == "error"
    assert SECRET_TEXT not in caplog.text
    s.rollback()


def test_run_due_continues_after_a_job_errors(env, monkeypatch):
    import outreach.jobs as jobs_mod

    _, factory, s = env
    _, ids = _make_jobs(s, 3, NOW - timedelta(minutes=5))
    seen = []

    def fake_run_job(f, job_id, d, n):
        seen.append(job_id)
        if job_id == ids[0]:
            raise RuntimeError("boom")
        return "error" if job_id == ids[1] else "done"

    monkeypatch.setattr(jobs_mod, "run_job", fake_run_job)
    assert run_due(factory, jdeps(), clock(NOW)) == 1
    assert seen == ids


# ------------------------------------------------------------------ review round: pre-LLM bot skips


@pytest.mark.parametrize("case,reason", [
    ("no_consent", "skipped:no_consent"),
    ("suppressed", "skipped:phone_suppressed"),
    ("window_closed", "skipped:window_closed"),
    ("window_none", "skipped:window_closed"),
])
def test_bot_job_skipped_before_llm_when_whatsapp_not_allowed(env, case, reason):
    from outreach.models import Suppression

    _, factory, s = env
    c = Counter([WaSendRaw("answer")])
    deps = jdeps(bot=c.bot)
    lead = wa_lead(s)
    ingest(s, [wa_ev("wa:1")], deps, NOW, SCANNERS)
    job_id = only_job(s).id
    ld = s.get(Lead, lead.id)
    run_at = NOW
    if case == "no_consent":
        ld.consent_whatsapp = False
    elif case == "suppressed":
        s.add(Suppression(value=PHONE, kind="phone", reason="test", created_at=NOW))
    elif case == "window_closed":
        run_at = NOW + timedelta(hours=24)
    else:
        ld.wa_window_until = None
    s.commit()
    assert run_job(factory, job_id, deps, clock(run_at)) == "obsolete"
    assert c.calls == [] and deps.wa.calls == []
    s.expire_all()
    j = s.get(Job, job_id)
    assert (j.status, j.last_error) == ("done", reason)


# ------------------------------------------------------------------ review round: savepoint pruning


def test_enqueued_ids_pruned_on_savepoint_rollback(env):
    from outreach.jobs import take_enqueued

    _, _, s = env
    lead = make_lead(s)
    ev = Event(lead_id=lead.id, channel="email", kind="reply", occurred_at=NOW, received_at=NOW, data={})
    s.add(ev)
    s.flush()
    q = JobQueue()
    kept = q.enqueue(s, lead, "bot_reply", None, NOW)
    with pytest.raises(RuntimeError):
        with s.begin_nested():
            q.enqueue(s, lead, "classify", ev, NOW)
            raise RuntimeError("roll this savepoint back")
    s.commit()
    assert take_enqueued(s) == [kept]
    assert count(s, Job) == 1
