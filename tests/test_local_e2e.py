"""Local end-to-end run of the plan's Task 13 e2e table, in-process.

Everything is the local setup: APP_ENV=local with every service fake (default_deps), the fake
clock (LocalClock, moved with advance()), leads created through POST /intake, and every provider
event sent as the signed HTTP request the simulator builds (outreach.devsim). Opens are logged
only (plan v2.1), so the open row checks only that the open was logged.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from outreach import devsim, worker
from outreach.alerts import ConsoleAlerter
from outreach.app import create_app, default_deps
from outreach.chatbot import make_bot
from outreach.llm.local_fake import RuleBasedLLM
from outreach.localclock import LocalClock
from outreach.models import Alert as AlertRow
from outreach.models import ChatMessage, Event, Lead, Suppression
from outreach.states import LeadState as S
from tests.fakes import make_app_engine
from tests.test_local_settings import local

START = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)  # Thu 15:30 in Asia/Kolkata (business hours)
KB = """# Ozoo.ai knowledge base
## Services
- Ozoo.ai builds AI agents that answer customer support tickets in your helpdesk.
## FAQs
- Q: Which languages do you support?
  A: English, Hindi and Spanish today.
- Q: How long does setup take?
  A: Most teams are live within a few weeks of the demo.
"""


class Local:
    def __init__(self, tmp_path) -> None:
        self.settings = local(fake_clock_file=str(tmp_path / "clock.json"))
        self.clock = LocalClock(self.settings.fake_clock_file, real_now=lambda: START)
        self.engine = make_app_engine()
        self.deps = default_deps(self.settings)
        self.console: List[str] = []
        self.deps.alerter = ConsoleAlerter(out=self.console.append)
        kb = tmp_path / "kb.md"
        kb.write_text(KB)
        self.deps.bot_reply = make_bot(self.settings, RuleBasedLLM(), kb_path=kb)
        self.app = create_app(self.settings, deps=self.deps, engine=self.engine, clock=self.clock)
        self.client = TestClient(self.app, raise_server_exceptions=False, follow_redirects=False)
        self.factory = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)

    # ------------------------------------------------------------ helpers
    def intake(self, **payload: Any) -> int:
        body = {"name": "Ana Test", "company": "Acme", "role": "CTO", "source": "e2e", **payload}
        r = self.client.post("/intake", json=body, headers={"X-API-Key": self.settings.intake_api_key})
        assert r.status_code in (200, 201), r.text
        return r.json()["lead_id"]

    def email_lead(self, **kw: Any) -> int:
        return self.intake(email=kw.pop("email", "ana@acme-e2e.example.com"), **kw)

    def wa_lead(self, **kw: Any) -> int:
        return self.intake(phone=kw.pop("phone", "+12015550123"), country="US", consent_whatsapp=True, **kw)

    def both_lead(self, **kw: Any) -> int:
        return self.intake(email="bo@both-e2e.example.com", phone="+12015550188", country="US",
                           consent_whatsapp=True, **kw)

    def lead(self, lead_id: int) -> Lead:
        with self.factory() as s:
            return s.get(Lead, lead_id)

    def sim_lead(self, lead_id: int) -> devsim.SimLead:
        return devsim.SimLead.of(self.lead(lead_id))

    def now(self) -> datetime:
        return self.clock()

    def send(self, req: devsim.SimRequest) -> Any:
        r = self.client.request(req.method, req.path, headers=req.headers, content=req.body)
        assert r.status_code < 500, r.text
        return r

    def tick(self) -> Dict[str, int]:
        out = worker.tick(self.factory, self.deps, self.clock, self.settings)
        assert out.get("failed_jobs") == 0, out
        return out

    def advance(self, **kw: float) -> None:
        self.clock.advance(timedelta(**kw))

    def alerts(self, kind: Optional[str] = None, lead_id: Optional[int] = None) -> List[AlertRow]:
        with self.factory() as s:
            q = select(AlertRow)
            if kind:
                q = q.where(AlertRow.kind == kind)
            if lead_id is not None:
                q = q.where(AlertRow.lead_id == lead_id)
            return list(s.scalars(q))

    def chat(self, lead_id: int, direction: str = "out") -> List[ChatMessage]:
        with self.factory() as s:
            return list(s.scalars(select(ChatMessage).where(ChatMessage.lead_id == lead_id,
                                                            ChatMessage.direction == direction)
                                  .order_by(ChatMessage.id)))

    def tool_calls(self, name: str) -> List[tuple]:
        return [c for c in self.deps.tool.calls if c[0] == name]

    def wa_calls(self, name: Optional[str] = None) -> List[tuple]:
        return [c for c in self.deps.wa.client.calls if name is None or c[0] == name]

    def started_email(self, **kw: Any) -> int:
        lid = self.email_lead(**kw)
        self.tick()  # start_new: enrolled in the (fake) email tool
        assert self.lead(lid).tool_lead_id
        self.send(devsim.email_sent(self.settings, self.sim_lead(lid), self.now(), step=1))
        return lid


@pytest.fixture
def env(tmp_path):
    e = Local(tmp_path)
    yield e
    e.engine.dispose()


def _state(env: Local, lid: int) -> str:
    return env.lead(lid).state


# ====================================================================== email only

def test_email_open_is_logged_only(env):
    lid = env.started_email()
    env.send(devsim.email_open(env.settings, env.sim_lead(lid), env.now()))
    env.send(devsim.email_open(env.settings, env.sim_lead(lid), env.now(), ip="17.58.0.1"))  # Apple MPP
    with env.factory() as s:
        verdicts = sorted(s.scalars(select(Event.verdict).where(Event.kind == "open")))
    assert verdicts == ["human", "machine"]
    lead = env.lead(lid)
    assert lead.variant_flags.get("human_opens") == 1 and lead.score == 0
    assert _state(env, lid) == S.IN_SEQUENCE.value


def test_email_pricing_click_hot_lead_alert(env):
    lid = env.started_email()
    r = env.send(devsim.click(env.settings, env.sim_lead(lid), tag="pricing"))
    assert r.status_code == 302 and r.headers["location"] == env.settings.link_targets["pricing"]
    env.advance(minutes=1)
    env.tick()  # the click settles: human, +10, hot_lead alert, booking slots reply
    assert len(env.alerts("hot_lead", lid)) == 1
    assert env.lead(lid).score == 10
    assert any("hot_lead" in line for line in env.console)


def test_email_scanner_click_is_bot(env):
    lid = env.started_email()
    env.send(devsim.click(env.settings, env.sim_lead(lid), tag="pricing", scanner=True))
    env.advance(minutes=1)
    env.tick()
    assert env.alerts("hot_lead", lid) == [] and env.lead(lid).score == 0


def test_email_reply_interested(env):
    lid = env.started_email()
    env.send(devsim.email_reply(env.settings, env.sim_lead(lid), env.now(), text="Sounds interesting, can we talk?"))
    assert _state(env, lid) == S.QUALIFYING.value
    assert [c[1] for c in env.tool_calls("reply")] == [env.lead(lid).tool_lead_id]  # booking link emailed
    assert env.tool_calls("stop")  # outreach stopped on the reply


def test_email_reply_not_now_march(env):
    lid = env.started_email()
    env.send(devsim.email_reply(env.settings, env.sim_lead(lid), env.now(), text="Not now, try in March"))
    lead = env.lead(lid)
    assert lead.state == S.NURTURE.value
    assert lead.nurture_until == datetime(2027, 3, 1, 9, 0, tzinfo=timezone.utc)


def test_email_ooo_pauses_then_resumes(env):
    lid = env.started_email()
    env.send(devsim.email_reply(env.settings, env.sim_lead(lid), env.now(),
                                text="I am out of office until 2026-11-16"))
    assert _state(env, lid) == S.PAUSED.value
    env.advance(days=4)  # past 2026-11-16 09:00
    env.tick()
    assert _state(env, lid) == S.IN_SEQUENCE.value


def test_email_unsubscribe_link_suppresses_email_and_phone(env):
    lid = env.email_lead(phone="+12015550177", country="US")  # phone without opt-in: never messaged
    env.tick()
    sim = env.sim_lead(lid)
    r = env.send(devsim.click(env.settings, sim, tag="unsubscribe"))
    assert r.status_code == 302 and r.headers["location"].startswith("/u/")
    assert _state(env, lid) == S.IN_SEQUENCE.value  # a GET (scanner) never unsubscribes
    assert env.send(devsim.unsubscribe_link(env.settings, sim)).status_code == 200
    assert _state(env, lid) == S.SUPPRESSED.value
    with env.factory() as s:
        kinds = sorted(s.scalars(select(Suppression.kind)))
    assert kinds == ["email", "phone"]


def test_email_hard_bounce_suppressed(env):
    lid = env.started_email(email="dana.nomailbox@acme-e2e.example.com")
    env.send(devsim.email_bounce(env.settings, env.sim_lead(lid), env.now(), hard=True))
    assert _state(env, lid) == S.SUPPRESSED.value


def test_fake_verifier_rejects_bounce_address_at_intake(env):
    r = env.client.post("/intake", json={"name": "X", "company": "Y", "role": "Z", "source": "e2e",
                                         "email": "will.bounce@example.com"},
                        headers={"X-API-Key": env.settings.intake_api_key})
    assert r.json()["outcome"] == "rejected"


# ====================================================================== WhatsApp only

def started_wa(env: Local, **kw: Any) -> int:
    lid = env.wa_lead(**kw)
    env.tick()
    assert [c[2] for c in env.wa_calls("send_template")] == ["ozoo_intro"]
    return lid


def test_wa_book_demo_then_booked(env):
    lid = started_wa(env)
    env.send(devsim.wa_button(env.settings, env.sim_lead(lid), env.now(), button="book_demo"))
    assert _state(env, lid) == S.QUALIFYING.value
    assert env.wa_calls("send_cta_link")  # booking link as a CTA button
    env.send(devsim.cal_event(env.settings, env.sim_lead(lid), env.now(), "cal-booked"))
    assert _state(env, lid) == S.BOOKED.value
    assert len(env.alerts("booked", lid)) == 1


def test_wa_ask_question_bot_answers_from_kb(env):
    lid = started_wa(env)
    env.send(devsim.wa_button(env.settings, env.sim_lead(lid), env.now(), button="ask_question"))
    env.send(devsim.wa_text(env.settings, env.sim_lead(lid), env.now(), text="Which languages do you support?"))
    env.advance(seconds=5)
    env.send(devsim.wa_text(env.settings, env.sim_lead(lid), env.now(), text="How long does setup take?"))
    bot = [m.text for m in env.chat(lid) if m.author == "bot" and not m.text.startswith("[template")]
    assert any("Hindi" in t for t in bot) and any("few weeks" in t for t in bot)
    assert env.alerts("wa_human_needed", lid) == []


def test_wa_custom_price_hands_off_and_teams_reply_reaches_lead(env):
    lid = started_wa(env)
    env.send(devsim.wa_text(env.settings, env.sim_lead(lid), env.now(), text="What would a custom price be for us?"))
    assert len(env.alerts("wa_human_needed", lid)) == 1
    r = env.send(devsim.teams_command(env.settings, env.now(), text=f"wa {lid} Happy to help, I'll call you"))
    assert r.json()["text"].startswith("Sending to lead")
    human = [m for m in env.chat(lid) if m.author == "human"]
    assert [m.text for m in human] == ["Happy to help, I'll call you"]
    r = env.send(devsim.teams_command(env.settings, env.now(), text=f"done {lid}"))
    assert "back with the bot" in r.json()["text"]


def test_wa_stop_suppresses_and_nothing_more_is_sent(env):
    lid = started_wa(env)
    env.send(devsim.wa_text(env.settings, env.sim_lead(lid), env.now(), text="STOP"))
    assert _state(env, lid) == S.SUPPRESSED.value
    before = len(env.wa_calls())
    for _ in range(3):
        env.advance(hours=48)
        env.tick()
    assert len(env.wa_calls()) == before


def test_wa_no_response_reminder_then_nurture(env):
    lid = started_wa(env)
    env.advance(hours=48)
    env.tick()
    assert [c[2] for c in env.wa_calls("send_template")] == ["ozoo_intro", "ozoo_reminder"]
    env.advance(hours=48)
    env.tick()
    assert _state(env, lid) == S.NURTURE.value


def test_wa_status_delivered_is_stored(env):
    lid = started_wa(env)
    wamid = env.chat(lid)[0].wa_message_id
    env.send(devsim.wa_status(env.settings, env.sim_lead(lid), env.now(), status="delivered", wamid=wamid))
    with env.factory() as s:
        assert s.scalar(select(func.count(Event.id)).where(Event.kind == "wa_delivered", Event.lead_id == lid)) == 1


# ====================================================================== both channels

def test_both_no_wa_response_switches_to_email(env):
    lid = env.both_lead()
    env.tick()
    assert env.lead(lid).active_channel == "whatsapp"
    env.advance(hours=48)
    env.tick()
    env.advance(hours=48)
    env.tick()
    lead = env.lead(lid)
    assert lead.active_channel == "email" and lead.channel_switched and lead.tool_lead_id
    sent = len(env.wa_calls())
    env.advance(hours=48)
    env.tick()
    assert len(env.wa_calls()) == sent  # no further WhatsApp messages


def test_both_number_not_on_whatsapp_starts_email(env):
    lid = env.both_lead()
    env.tick()
    wamid = env.chat(lid)[0].wa_message_id
    env.send(devsim.wa_status(env.settings, env.sim_lead(lid), env.now(), status="failed", code=131026,
                              wamid=wamid))
    lead = env.lead(lid)
    assert lead.active_channel == "email" and lead.channel_switched
    assert lead.tool_lead_id and env.tool_calls("add_lead")


def test_both_wa_tap_after_switch_stops_email_tool(env):
    lid = env.both_lead()
    env.tick()
    wamid = env.chat(lid)[0].wa_message_id
    env.send(devsim.wa_status(env.settings, env.sim_lead(lid), env.now(), status="failed", code=131026,
                              wamid=wamid))
    tool_lead_id = env.lead(lid).tool_lead_id
    env.send(devsim.wa_button(env.settings, env.sim_lead(lid), env.now(), button="book_demo"))
    assert ("stop", tool_lead_id) in env.deps.tool.calls
    assert _state(env, lid) == S.QUALIFYING.value


def test_both_meeting_held_hands_off_with_both_channels(env):
    lid = env.both_lead()
    env.tick()
    env.send(devsim.wa_button(env.settings, env.sim_lead(lid), env.now(), button="book_demo"))
    env.send(devsim.cal_event(env.settings, env.sim_lead(lid), env.now(), "cal-form", size="51-200",
                              timeline="now"))
    env.send(devsim.cal_event(env.settings, env.sim_lead(lid), env.now(), "cal-booked"))
    env.advance(days=1, minutes=40)
    env.send(devsim.cal_event(env.settings, env.sim_lead(lid), env.now(), "cal-meeting-ended"))
    assert _state(env, lid) == S.HANDED_OFF.value
    [handoff] = env.alerts("handoff", lid)
    assert "Channels: email, whatsapp" in handoff.text


# ====================================================================== any

def test_duplicate_webhook_has_no_duplicate_effect(env):
    lid = env.started_email()
    req = devsim.email_reply(env.settings, env.sim_lead(lid), env.now(), text="Sounds interesting, can we talk?")
    assert env.send(req).status_code == 200
    assert env.send(req).status_code == 200
    with env.factory() as s:
        assert s.scalar(select(func.count(Event.id)).where(Event.kind == "reply")) == 1
    assert len(env.tool_calls("reply")) == 1
    wa = started_wa(env)
    req = devsim.wa_text(env.settings, env.sim_lead(wa), env.now(), text="STOP")
    env.send(req)
    env.send(req)
    assert len(env.chat(wa, "in")) == 1


def test_teams_command_signed_wrong_is_rejected(env):
    req = devsim.teams_command(env.settings, env.now(), text="help")
    req.headers["Authorization"] = "HMAC " + "A" * 44
    assert env.send(req).status_code == 401


def test_time_scale_turns_days_into_minutes(tmp_path):
    e = Local(tmp_path)
    try:
        e.settings = local(fake_clock_file=e.settings.fake_clock_file, time_scale=1440)  # 48 h -> 2 min
        lid = started_wa(e)
        e.advance(minutes=2)
        e.tick()
        e.advance(minutes=2)
        e.tick()
        assert _state(e, lid) == S.NURTURE.value
    finally:
        e.engine.dispose()
