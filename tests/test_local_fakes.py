"""Local fake providers (Task 13): verifier, console alerter, logging fakes, rule-based LLM,
the fake clock, and default_deps choosing them per mode."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from outreach.alerts import ConsoleAlerter, TeamsAlerter
from outreach.app import default_deps
from outreach.chatbot import BOT_SCHEMA, BOT_TOOL_NAME, bot_reply, build_system
from outreach.classifier import CLASSIFY_SCHEMA, CLASSIFY_TOOL_NAME, SYSTEM_PROMPT, classify_reply
from outreach.emailtool.fake import FakeEmailTool
from outreach.emailtool.instantly import InstantlyTool
from outreach.llm.local_fake import RuleBasedLLM
from outreach.localclock import LocalClock, default_clock
from outreach.models import Alert as AlertRow
from outreach.models import ChatMessage
from outreach.verify import EmailVerifier, FakeVerifier, make_verifier
from outreach.whatsapp.client import WhatsAppClient
from outreach.whatsapp.fake import FakeWhatsAppClient
from tests.conftest import NOW, make_lead
from tests.fakes import make_settings
from tests.test_local_settings import local

TODAY = date(2026, 11, 12)
KB = """# KB
## Services
- Ozoo.ai builds AI agents that answer customer support tickets in your helpdesk.
- TODO: placeholder line.
## FAQs
- Q: Which languages do you support?
  A: English, Hindi and Spanish today.
- Q: How much does it cost?
  A: Plans start at $49 per month.
"""


# ---------------------------------------------------------------- verifier

def test_fake_verifier():
    v = FakeVerifier()
    assert v.check("a@example.com") == "valid"
    assert v.check("will.bounce@example.com") == "invalid"
    assert v.check("INVALID-x@example.com") == "invalid"


def test_make_verifier_per_mode():
    assert isinstance(make_verifier(local()), FakeVerifier)
    assert isinstance(make_verifier(make_settings()), EmailVerifier)


# ---------------------------------------------------------------- console alerter

def test_console_alerter_prints_and_stores_posted_row(session):
    lines = []
    lead = make_lead(session)
    a = ConsoleAlerter(out=lines.append)
    a.send("hot_lead", "lead clicked pricing\nsecond line", lead, NOW, session)
    row = session.scalars(select(AlertRow)).one()
    assert row.kind == "hot_lead" and row.posted_at == NOW and row.lead_id == lead.id
    assert len(lines) == 1 and "hot_lead" in lines[0] and f"lead {lead.id}" in lines[0]
    assert "\n" not in lines[0]
    assert a.repost(session, row, NOW) is True
    assert a.post_text("Digest", ["x"], NOW) is True and len(lines) == 3


def test_console_alerter_without_session_does_not_raise():
    ConsoleAlerter(out=lambda _l: None).send("unsure", "t", None, NOW)


# ---------------------------------------------------------------- logging fakes

def test_fake_wa_client_logs_metadata_only(caplog):
    c = FakeWhatsAppClient(log_sends=True)
    with caplog.at_level(logging.INFO, logger="outreach.whatsapp.fake"):
        wamid = c.send_text("+12015550123", "secret body text")
    assert wamid.startswith("wamid.")
    assert "send_text" in caplog.text and "2015550123" not in caplog.text and "secret" not in caplog.text


def test_fake_email_tool_logs_metadata_only(caplog):
    t = FakeEmailTool(log_calls=True)
    with caplog.at_level(logging.INFO, logger="outreach.emailtool.fake"):
        t.reply("fake-1", "hello private body")
    assert "reply" in caplog.text and "private" not in caplog.text


# ---------------------------------------------------------------- RuleBasedLLM: classifier

def classify(text: str):
    return classify_reply(text, "email", RuleBasedLLM(), model="local", today=TODAY)


@pytest.mark.parametrize("text,label", [
    ("Sounds interesting, can we talk?", "interested"),
    ("Yes, please book a demo", "interested"),
    ("What integrations do you have?", "question"),
    ("Not now, try in March", "not_now"),
    ("I am out of office until 2026-11-20", "ooo"),
    ("Automatic reply: I'm on vacation", "ooo"),
    ("Please unsubscribe me", "unsubscribe"),
    ("Remove me from your list", "unsubscribe"),
    ("I'm not the right person, talk to jo@acme.com", "wrong_person"),
    ("Not interested, thanks", "not_interested"),
    ("ok", "unsure"),
])
def test_rule_classifier_labels(text, label):
    assert classify(text).label == label


def test_rule_classifier_dates():
    assert classify("Not now, try in March").later_date == date(2027, 3, 1)
    assert classify("Not now, ping me on 2027-01-15").later_date == date(2027, 1, 15)
    assert classify("Out of office, back on 2026-11-20").return_date == date(2026, 11, 20)
    assert classify("Not now").later_date is None


def test_rule_classifier_referral_is_grounded():
    rc = classify("Wrong person - please contact jo@acme.com")
    assert rc.referral_email == "jo@acme.com"


def test_rule_classifier_returns_the_port_shape():
    out = RuleBasedLLM().structured(system=SYSTEM_PROMPT, messages=[
        {"role": "user", "content": "Today is 2026-11-12. Channel: email.\n<reply>\nwhat is it?\n</reply>"}],
        tool_name=CLASSIFY_TOOL_NAME, tool_description="", schema=CLASSIFY_SCHEMA, max_tokens=500, model="m")
    assert set(out) <= set(CLASSIFY_SCHEMA["properties"]) and {"label", "confidence", "reason"} <= set(out)
    assert out["label"] == "question" and out["draft_answer"]


# ---------------------------------------------------------------- RuleBasedLLM: chatbot

def bot(text: str, kb: str = KB):
    hist = [ChatMessage(author="lead", direction="in", text=text)]
    return bot_reply(None, hist, kb, RuleBasedLLM(), model="local", today=TODAY)


def test_rule_bot_answers_from_kb():
    r = bot("which languages do you support")
    assert r.kind == "answer" and "Hindi" in r.text
    r = bot("what does ozoo do for customer support tickets?")
    assert r.kind == "answer" and "helpdesk" in r.text


@pytest.mark.parametrize("text,kind", [
    ("HUMAN", "wants_human"),
    ("can I talk to a human please", "wants_human"),
    ("what is your pricing?", "wants_human"),
    ("how much does a custom price cost", "wants_human"),
    ("I'd like to book a demo", "ready_to_book"),
    ("not interested right now, maybe later", "not_ready"),
    ("do you integrate with zorblax?", "out_of_scope"),
])
def test_rule_bot_kinds(text, kind):
    assert bot(text).kind == kind


def test_rule_bot_skips_todo_lines():
    assert bot("placeholder line").kind == "out_of_scope"


def test_rule_bot_output_still_post_checked():
    kb = KB + "- Visit https://evil.example for support languages\n"
    # the matched KB line has a link: the guardrail post-check turns it into out_of_scope
    r = bot("visit evil.example link", kb)
    assert r.kind == "out_of_scope"


def test_rule_bot_returns_the_port_shape():
    out = RuleBasedLLM().structured(system=build_system(KB, TODAY), messages=[
        {"role": "user", "content": "which languages?"}], tool_name=BOT_TOOL_NAME, tool_description="",
        schema=BOT_SCHEMA, max_tokens=400, model="m")
    assert set(out) <= {"kind", "text"} and out["kind"] == "answer"


def test_rule_llm_unknown_tool_raises_llm_error():
    from outreach.llm import LLMError

    with pytest.raises(LLMError):
        RuleBasedLLM().structured(system="", messages=[], tool_name="x", tool_description="", schema={},
                                  max_tokens=1, model="m")


# ---------------------------------------------------------------- clock

def test_local_clock_offset_persists(tmp_path):
    path = tmp_path / "clock.json"
    real = datetime(2026, 11, 13, 8, 0, tzinfo=timezone.utc)
    c = LocalClock(path, real_now=lambda: real)
    assert c() == real and c.offset() == timedelta(0)
    c.advance(timedelta(hours=49))
    assert LocalClock(path, real_now=lambda: real)() == real + timedelta(hours=49)
    assert json.loads(path.read_text())["offset_s"] == 49 * 3600
    c.reset()
    assert c() == real


def test_local_clock_bad_file_means_no_offset(tmp_path):
    path = tmp_path / "clock.json"
    path.write_text("not json")
    assert LocalClock(path).offset() == timedelta(0)


def test_default_clock_is_real_in_production(tmp_path):
    clock = default_clock(make_settings(fake_clock_file=str(tmp_path / "c.json")))
    assert not isinstance(clock, LocalClock)
    assert isinstance(default_clock(local(fake_clock_file=str(tmp_path / "c.json"))), LocalClock)


# ---------------------------------------------------------------- default_deps

def test_default_deps_all_fake_locally():
    deps = default_deps(local())
    assert isinstance(deps.tool, FakeEmailTool)
    assert isinstance(deps.wa.client, FakeWhatsAppClient)
    assert isinstance(deps.alerter, ConsoleAlerter)
    assert deps.classify is not None and deps.bot_reply is not None


def test_default_deps_real_in_production():
    deps = default_deps(make_settings())
    assert isinstance(deps.tool, InstantlyTool)
    assert isinstance(deps.wa.client, WhatsAppClient)
    assert isinstance(deps.alerter, TeamsAlerter)


def test_default_deps_mixed():
    deps = default_deps(local(alerts_mode="real", teams_webhook_url="https://x.example.com/h?sig=1"))
    assert isinstance(deps.alerter, TeamsAlerter) and isinstance(deps.tool, FakeEmailTool)
