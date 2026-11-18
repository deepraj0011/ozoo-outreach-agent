"""WhatsApp chatbot (plan section 7): forced tool call, guardrail post-checks, turn limit, routing."""
from __future__ import annotations

import logging
from datetime import timedelta
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from sqlalchemy import select

from outreach.actions import (
    SetNurtureUntil, SetState, StartTakeover, WaSendRaw, WaSendText, execute,
)
from outreach.chatbot import (
    BOT_SCHEMA, BOT_TOOL_DESCRIPTION, BOT_TOOL_NAME, DEFAULT_KB, GUARDRAILS, KINDS, BotResult, bot_reply,
    build_messages, build_system, make_bot,
)
from outreach.handoff import start_takeover
from outreach.llm import LLMBadOutput, LLMTruncated, LLMUnavailable
from outreach.llm.fake import FakeLLM
from outreach.models import Alert as AlertRow
from outreach.models import ChatMessage
from outreach.states import LeadState as S
from outreach.whatsapp.fake import FakeWhatsApp
from outreach.whatsapp.sender import load_wa_templates
from tests.conftest import NOW, make_lead
from tests.fakes import make_deps, make_settings

TODAY = NOW.date()
KB = "# Ozoo.ai\n## Services\nWe build AI agents.\n## FAQs\nThe starter plan is $49 per month.\n"
SETTINGS = make_settings()


FakeClient = FakeLLM  # scripted LLM port: dicts are returned, exceptions raised; records kwargs


def out(kind: str, text: Optional[str] = None) -> Dict[str, Any]:
    return {"kind": kind, "text": text}


def msg(author: str, text: str, i: int = 0) -> ChatMessage:
    return ChatMessage(lead_id=1, direction="in" if author == "lead" else "out", author=author, text=text,
                       created_at=NOW + timedelta(seconds=i))


def wa_lead(session, **kw):
    defaults = dict(state=S.ENGAGED, active_channel="whatsapp", phone="+919800000002", email=None,
                    consent_whatsapp=True, wa_window_until=NOW + timedelta(hours=10))
    defaults.update(kw)
    return make_lead(session, **defaults)


def add_history(session, lead, *items):
    for i, (author, text) in enumerate(items):
        session.add(ChatMessage(lead_id=lead.id, direction="in" if author == "lead" else "out", author=author,
                                text=text, created_at=NOW - timedelta(minutes=100) + timedelta(minutes=i)))
    session.flush()


def ask(client: FakeClient, history=None, kb: str = KB) -> BotResult:
    lead = SimpleNamespace(id=1, name="Ann Lee", company="Acme")
    return bot_reply(lead, history if history is not None else [msg("lead", "What do you do?")], kb, client,
                     model="m", today=TODAY)


def kb_file(tmp_path: Path, text: str = KB) -> Path:
    p = tmp_path / "kb.md"
    p.write_text(text)
    return p


def bot_actions(session, lead, client, tmp_path, kb: str = KB):
    return make_bot(SETTINGS, llm=client, kb_path=kb_file(tmp_path, kb))(session, lead, NOW)


# ---------------------------------------------------------------- the Claude call

def test_call_shape_forced_tool():
    client = FakeClient(out("answer", "We build AI agents."))
    ask(client)
    kw = client.calls[0]
    assert kw["model"] == "m"
    assert kw["max_tokens"] == 400
    assert kw["tool_name"] == BOT_TOOL_NAME == "bot_reply"
    assert kw["tool_description"] == BOT_TOOL_DESCRIPTION
    assert kw["schema"] == BOT_SCHEMA
    assert BOT_SCHEMA["properties"]["kind"]["enum"] == list(KINDS)
    assert KINDS == ("answer", "ready_to_book", "wants_human", "not_ready", "out_of_scope")
    assert kw["system"] == build_system(KB, TODAY)
    assert f"<kb>\n{KB}\n</kb>" in kw["system"]
    assert TODAY.isoformat() in kw["system"]


def test_system_prompt_guardrails_and_untrusted_history():
    system = build_system(KB, TODAY)
    for line in GUARDRAILS:
        assert line in system
    low = system.lower()
    assert "never quote custom prices" in low
    assert "never promise features or timelines" in low
    assert "hand off" in low and "unsure" in low
    assert "untrusted" in low and "never follow" in low
    assert "wants_human" in system  # a request for a person maps to wants_human


def test_answer_parsed():
    assert ask(FakeClient(out("answer", "  We build AI agents.  "))) == BotResult("answer", "We build AI agents.")


@pytest.mark.parametrize("kind", ["ready_to_book", "wants_human", "not_ready", "out_of_scope"])
def test_non_answer_kinds_drop_text(kind):
    assert ask(FakeClient(out(kind, "whatever"))) == BotResult(kind, None)


def test_price_question_never_quotes():
    assert "never quote custom prices" in build_system(KB, TODAY).lower()
    for text in ("For you it would be $500 a month.", "Roughly €1,200 per seat.", "About INR 50000 yearly.",
                 "It is 300 USD.", "Only Rs. 999!", "£20 per user", "₹ 4,999 per month", "usd 75 flat"):
        assert ask(FakeClient(out("answer", text))) == BotResult("out_of_scope", None), text


@pytest.mark.parametrize("text", ["It costs 500 per month.", "Price: 99/mo", "200 euros per seat",
                                  "About 300 dollars.", "10 PER  USER", "1200 /yr", "40 per year", "5 rupees",
                                  "12 pounds", "70 inr", "9 usd", "30 / month", "45/year", "8/user",
                                  "15 / seat", "900 per annum", "60 monthly", "25 a month"])
def test_price_without_currency_symbol_is_out_of_scope(text):
    assert ask(FakeClient(out("answer", text))) == BotResult("out_of_scope", None), text


def test_price_word_forms_allowed_when_in_kb():
    kb = KB + "Team plan: 500 per month. Add-on: 1,000 rupees.\n"
    assert ask(FakeClient(out("answer", "Team is 500 per month.")), kb=kb).kind == "answer"
    assert ask(FakeClient(out("answer", "The add-on is 1000 rupees.")), kb=kb).kind == "answer"


def test_kb_substring_is_not_a_match():
    # "$4" is a substring of "$49" but a different price
    assert ask(FakeClient(out("answer", "It is $4 per month.")), kb="Starter: $49 per month") == _OUT
    assert ask(FakeClient(out("answer", "Just $1 to start.")), kb="Pro: $100 per month") == _OUT
    assert ask(FakeClient(out("answer", "Pro is $100.00.")), kb="Pro: $100 per month").kind == "answer"
    assert ask(FakeClient(out("answer", "Pro is USD 1,000.")), kb="Pro: $1000").kind == "answer"


def test_kb_prices_normalised():
    from outreach.chatbot import kb_prices

    assert kb_prices("A $1,000 plan, B 49.50 USD, C 99/mo, D 3 steps, E €10.00, F Rs. 500, G 7 rupees, "
                     "H £2 per seat") == frozenset(
        {("USD", "1000"), ("USD", "49.5"), (None, "99"), ("EUR", "10"), ("INR", "500"), ("INR", "7"),
         ("GBP", "2")})


@pytest.mark.parametrize("text", ["We answer 24/7.", "Up to 2 per day.", "Works for 3 users.",
                                  "Launched on 2026-11-13.", "Since 13/11/2026.", "Since Nov 13, 2026.",
                                  "12 months of support"])
def test_non_prices_still_answer(text):
    assert ask(FakeClient(out("answer", text))).kind == "answer", text


def test_currency_aware_comparison():
    kb = "Starter: €49 per month. Team: 99/mo."
    assert ask(FakeClient(out("answer", "Starter is $49.")), kb=kb) == _OUT  # other currency
    assert ask(FakeClient(out("answer", "Starter is €49.")), kb=kb).kind == "answer"
    assert ask(FakeClient(out("answer", "Starter is EUR 49.")), kb=kb).kind == "answer"
    assert ask(FakeClient(out("answer", "Starter is 49 per month.")), kb=kb).kind == "answer"  # no currency
    assert ask(FakeClient(out("answer", "Team is $99.")), kb=kb) == _OUT  # KB has no currency for 99
    assert ask(FakeClient(out("answer", "Team is 99 a month.")), kb=kb).kind == "answer"


def test_make_bot_caches_kb_prices(session, tmp_path, monkeypatch):
    import outreach.chatbot as cb

    calls = []
    real = cb.kb_prices
    monkeypatch.setattr(cb, "kb_prices", lambda kb: calls.append(kb) or real(kb))
    lead = wa_lead(session)
    add_history(session, lead, ("lead", "price?"))
    bot = make_bot(SETTINGS, llm=FakeClient(out("answer", "Starter is $49 per month.")), kb_path=kb_file(tmp_path))
    assert bot(session, lead, NOW) == [WaSendRaw("Starter is $49 per month.")]
    assert bot(session, lead, NOW) == [WaSendRaw("Starter is $49 per month.")]
    assert len(calls) == 1  # computed once, with the KB


_OUT = BotResult("out_of_scope", None)


def test_currency_in_kb_allowed():
    assert ask(FakeClient(out("answer", "The starter plan is $49 per month."))).kind == "answer"
    # a different amount next to a KB amount still hands off
    assert ask(FakeClient(out("answer", "Starter is $49, enterprise $990."))).kind == "out_of_scope"


def test_plain_numbers_are_not_prices():
    assert ask(FakeClient(out("answer", "We have 3 steps and 40 customers."))).kind == "answer"


@pytest.mark.parametrize("text", ["See https://ozoo.ai/pricing", "Visit www.ozoo.ai", "go to http://x.y",
                                  "Docs: WWW.example.com"])
def test_url_in_answer_is_out_of_scope(text):
    assert ask(FakeClient(out("answer", text))) == BotResult("out_of_scope", None)


@pytest.mark.parametrize("text", [None, "", "   ", "x" * 1001])
def test_empty_or_long_answer_is_out_of_scope(text):
    assert ask(FakeClient(out("answer", text))).kind == "out_of_scope"


def test_answer_at_limit_ok():
    assert ask(FakeClient(out("answer", "x" * 1000))).kind == "answer"


def test_api_error_hands_to_human():
    for exc in (LLMUnavailable("llm unavailable: timeout"), RuntimeError("boom")):
        assert ask(FakeClient(exc)) == BotResult("wants_human", None)


@pytest.mark.parametrize("client", [
    FakeClient(out("maybe", "x")),  # kind outside the enum
    FakeClient({"text": "no kind"}),
    FakeClient(LLMBadOutput("no tool call")),  # no tool call / refusal / bad JSON
    FakeClient(LLMTruncated("llm output truncated")),
    FakeClient("not a dict"),
])
def test_malformed_output_hands_to_human(client):
    assert ask(client) == BotResult("wants_human", None)


def test_no_user_turn_hands_to_human_without_call():
    client = FakeClient(out("answer", "x"))
    assert ask(client, history=[msg("bot", "[template:ozoo_intro]")]) == BotResult("wants_human", None)
    assert ask(client, history=[]) == BotResult("wants_human", None)
    assert client.calls == []


# ---------------------------------------------------------------- history

def test_history_roles_merge_and_start_with_user():
    hist = [msg("bot", "[template:ozoo_intro]", 0), msg("lead", "hi", 1), msg("lead", "what is it?", 2),
            msg("bot", "We build agents.", 3), msg("human", "Hi, Raj here.", 4), msg("lead", "cool", 5)]
    assert build_messages(hist) == [
        {"role": "user", "content": "hi\nwhat is it?"},
        {"role": "assistant", "content": "We build agents.\nHi, Raj here."},
        {"role": "user", "content": "cool"},
    ]
    client = FakeClient(out("answer", "ok"))
    ask(client, history=hist)
    assert client.calls[0]["messages"] == build_messages(hist)


def test_history_trailing_assistant_dropped():
    hist = [msg("lead", "hi", 0), msg("bot", "hello", 1)]
    assert build_messages(hist) == [{"role": "user", "content": "hi"}]


def test_make_bot_uses_last_20_messages_oldest_first(session, tmp_path):
    lead = wa_lead(session)
    add_history(session, lead, *[("lead" if i % 2 == 0 else "bot", f"m{i}") for i in range(25)])
    client = FakeClient(out("answer", "ok"))
    bot_actions(session, lead, client, tmp_path)
    msgs = client.calls[0]["messages"]
    joined = "\n".join(m["content"] for m in msgs)
    assert "m4" not in joined.split("\n") and "m5" not in joined.split("\n")
    assert msgs[0] == {"role": "user", "content": "m6"}  # m5 (bot) is dropped: must start with user
    assert msgs[-1] == {"role": "user", "content": "m24"}


# ---------------------------------------------------------------- make_bot routing

def test_answer_sent(session, tmp_path):
    lead = wa_lead(session)
    add_history(session, lead, ("lead", "What do you do?"))
    acts = bot_actions(session, lead, FakeClient(out("answer", "We build AI agents.")), tmp_path)
    assert acts == [WaSendRaw("We build AI agents.")]
    wa = FakeWhatsApp(SETTINGS)
    execute(session, lead, acts, make_deps(wa=wa), NOW)
    assert wa.calls == [("send_text", lead.phone, "We build AI agents.")]
    row = session.scalars(select(ChatMessage).where(ChatMessage.direction == "out")).one()
    assert (row.author, row.text) == ("bot", "We build AI agents.")


def test_bot_turns_increment_on_send(session):
    lead = wa_lead(session, wa_bot_turns=2)
    wa = FakeWhatsApp(SETTINGS)
    deps = make_deps(wa=wa)
    execute(session, lead, [WaSendRaw("hello")], deps, NOW)
    assert lead.wa_bot_turns == 3
    execute(session, lead, [WaSendRaw("from a person", author="human")], deps, NOW)
    assert lead.wa_bot_turns == 3  # only bot sends count
    wa.client.fail_code = 131026
    execute(session, lead, [WaSendRaw("fails")], deps, NOW)
    assert lead.wa_bot_turns == 3  # a failed send is not a turn
    assert session.scalars(select(AlertRow.kind)).all() == ["integration_error"]
    lead.wa_window_until = NOW - timedelta(minutes=1)
    execute(session, lead, [WaSendRaw("window closed")], deps, NOW)
    assert lead.wa_bot_turns == 3


def test_price_answer_becomes_takeover(session, tmp_path):
    lead = wa_lead(session)
    add_history(session, lead, ("lead", "How much for 50 seats?"))
    acts = bot_actions(session, lead, FakeClient(out("answer", "That is $2,000 a month.")), tmp_path)
    assert acts == [StartTakeover("out_of_scope")]


def test_sixth_turn_offers_booking_or_human(session, tmp_path):
    lead = wa_lead(session, wa_bot_turns=6)
    add_history(session, lead, ("lead", "another question"))
    client = FakeClient(out("answer", "x"))
    assert bot_actions(session, lead, client, tmp_path) == [WaSendText("turn_limit")]
    assert client.calls == []
    t = load_wa_templates()
    text = t["texts"]["turn_limit"].lower()
    assert "demo" in text and "human" in text
    assert t["buttons"]["turn_limit"]  # sent as a tracked booking CTA button
    wa = FakeWhatsApp(SETTINGS)
    execute(session, lead, [WaSendText("turn_limit")], make_deps(wa=wa), NOW)
    assert wa.calls[0][0] == "send_cta_link" and "/r/" in wa.calls[0][4]


def test_turn_limit_no_llm_call(session, tmp_path):
    lead = wa_lead(session, wa_bot_turns=9)
    add_history(session, lead, ("lead", "q"))
    client = FakeClient(RuntimeError("must not be called"))
    assert bot_actions(session, lead, client, tmp_path) == [WaSendText("turn_limit")]
    assert client.calls == []
    lead.wa_bot_turns = 5
    assert bot_actions(session, lead, FakeClient(out("answer", "ok")), tmp_path) == [WaSendRaw("ok")]


@pytest.mark.parametrize("text", ["HUMAN", " human ", "Human"])
def test_human_keyword_after_turn_limit_takes_over(session, tmp_path, text):
    lead = wa_lead(session, wa_bot_turns=6)
    add_history(session, lead, ("bot", "turn limit text"), ("lead", text))
    client = FakeClient(out("answer", "x"))
    assert bot_actions(session, lead, client, tmp_path) == [StartTakeover("wants_human")]
    assert client.calls == []


def test_ready_to_book(session, tmp_path):
    lead = wa_lead(session)
    add_history(session, lead, ("lead", "let's talk"))
    client = FakeClient(out("ready_to_book"))
    assert bot_actions(session, lead, client, tmp_path) == [SetState(S.QUALIFYING, "bot_ready"),
                                                             WaSendText("booking_link")]
    for state in (S.QUALIFYING, S.BOOKED, S.HANDED_OFF):
        lead.state = state.value
        assert bot_actions(session, lead, client, tmp_path) == [WaSendText("booking_link")], state


def test_not_ready(session, tmp_path):
    lead = wa_lead(session)
    add_history(session, lead, ("lead", "maybe next year"))
    client = FakeClient(out("not_ready"))
    tail = [SetNurtureUntil(NOW + timedelta(days=90)), WaSendText("not_ready_close")]
    assert bot_actions(session, lead, client, tmp_path) == [SetState(S.NURTURE, "not_now"), *tail]
    lead.state = S.NURTURE.value
    assert bot_actions(session, lead, client, tmp_path) == tail


@pytest.mark.parametrize("kind", ["wants_human", "out_of_scope"])
def test_handoff_kinds_start_takeover(session, tmp_path, kind):
    lead = wa_lead(session)
    add_history(session, lead, ("lead", "q"))
    assert bot_actions(session, lead, FakeClient(out(kind)), tmp_path) == [StartTakeover(kind)]


def test_api_error_bot_starts_takeover(session, tmp_path):
    lead = wa_lead(session)
    add_history(session, lead, ("lead", "q"))
    assert bot_actions(session, lead, FakeClient(RuntimeError("x")), tmp_path) == [StartTakeover("wants_human")]


def test_wants_human_starts_takeover(session, tmp_path):
    lead = wa_lead(session)
    add_history(session, lead, ("lead", "can I talk to a person?"))
    wa = FakeWhatsApp(SETTINGS)
    deps = make_deps(wa=wa)
    deps.takeover = partial(_takeover, deps=deps)
    deps.bot_reply = make_bot(SETTINGS, llm=FakeClient(out("wants_human")), kb_path=kb_file(tmp_path))
    from outreach.actions import BotReply

    execute(session, lead, [BotReply()], deps, NOW)
    assert lead.variant_flags["takeover_at"] == NOW.isoformat()
    assert lead.variant_flags["takeover_reason"] == "wants_human"
    assert session.scalars(select(AlertRow.kind)).all() == ["wa_human_needed"]
    assert [c[0] for c in wa.calls] == ["send_text"]  # handoff_notice
    assert "team" in wa.calls[0][2]


def _takeover(session, lead, reason, now, *, deps):
    return start_takeover(session, lead, reason, deps, now)


def test_start_takeover_without_port_alerts(session):
    lead = wa_lead(session)
    deps = make_deps()
    assert deps.takeover is None
    execute(session, lead, [StartTakeover("unsure")], deps, NOW)
    assert deps.alerter.calls == [("send", "wa_human_needed", lead.id)]


def test_bot_silent_during_takeover(session, tmp_path):
    lead = wa_lead(session, variant_flags={"takeover_at": NOW.isoformat()})
    add_history(session, lead, ("lead", "hello?"))
    client = FakeClient(out("answer", "hi"))
    assert bot_actions(session, lead, client, tmp_path) == []
    assert client.calls == []
    lead.variant_flags["takeover_released"] = NOW.isoformat()
    assert bot_actions(session, lead, client, tmp_path) == [WaSendRaw("hi")]
    assert len(client.calls) == 1


def test_missing_kb_hands_off(session, tmp_path):
    lead = wa_lead(session)
    add_history(session, lead, ("lead", "q"))
    client = FakeClient(out("answer", "x"))
    bot = make_bot(SETTINGS, llm=client, kb_path=tmp_path / "nope.md")
    assert bot(session, lead, NOW) == [StartTakeover("no_kb")]
    assert bot_actions(session, lead, client, tmp_path, kb="  \n") == [StartTakeover("no_kb")]
    assert client.calls == []


def test_default_kb_resolved_from_repo_root(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert DEFAULT_KB == Path(__file__).resolve().parents[1] / "kb" / "ozoo_kb.md"
    raw = DEFAULT_KB.read_text()
    assert "TODO" not in raw
    for section in ("What Ozoo.ai is", "Products", "Who it's for", "How engagement works", "Pricing", "FAQs"):
        assert section in raw
    client = FakeClient(out("answer", "ok"))
    lead = SimpleNamespace(id=1, variant_flags={}, wa_bot_turns=0, state="engaged")
    bot = make_bot(SETTINGS, llm=client)
    session = _FakeSession([msg("lead", "q")])
    assert bot(session, lead, NOW) == [WaSendRaw("ok")]
    assert "<kb>" in client.calls[0]["system"] and "Services" in client.calls[0]["system"]


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    def flush(self):
        pass

    def scalars(self, _stmt):
        return self._rows


def test_message_text_never_logged(session, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    lead = wa_lead(session)
    add_history(session, lead, ("lead", "SECRET-QUESTION"))
    bot_actions(session, lead, FakeClient(out("answer", "SECRET-ANSWER")), tmp_path)
    execute(session, lead, [WaSendRaw("SECRET-ANSWER")], make_deps(wa=FakeWhatsApp(SETTINGS)), NOW)
    ask(FakeClient(RuntimeError("SECRET-ERR")))
    assert "SECRET" not in caplog.text
    assert lead.phone not in caplog.text


def test_schema_portable_across_providers():
    """Plain JSON Schema both tool-calling APIs accept: object at the top, no $ref, closed."""
    import json

    assert BOT_SCHEMA["type"] == "object"
    assert BOT_SCHEMA["additionalProperties"] is False
    assert "$ref" not in json.dumps(BOT_SCHEMA) and "strict" not in BOT_SCHEMA
    assert set(BOT_SCHEMA["required"]) <= set(BOT_SCHEMA["properties"])


def test_make_bot_defaults_to_configured_llm(monkeypatch, tmp_path):
    import outreach.chatbot as cb

    built = []
    monkeypatch.setattr(cb, "make_llm", lambda settings: built.append(settings) or FakeLLM(out("answer", "x")))
    make_bot(SETTINGS, kb_path=kb_file(tmp_path))
    assert built == [SETTINGS]


def test_make_bot_uses_chat_model(session, tmp_path):
    lead = wa_lead(session)
    add_history(session, lead, ("lead", "What do you do?"))
    client = FakeClient(out("answer", "ok"))
    make_bot(make_settings(llm_chat_model="chat-test"), client, kb_path=kb_file(tmp_path))(session, lead, NOW)
    assert client.calls[0]["model"] == "chat-test"


def test_schema_has_no_type_arrays():
    from tests.test_classifier import _types

    assert list(_types(BOT_SCHEMA)) and not [t for t in _types(BOT_SCHEMA) if isinstance(t, list)]
    assert BOT_SCHEMA["required"] == ["kind"]  # text is optional, pydantic defaults it to None


def test_explicit_null_and_omitted_text_validate():
    assert ask(FakeClient({"kind": "ready_to_book", "text": None})) == BotResult("ready_to_book", None)
    assert ask(FakeClient({"kind": "not_ready"})) == BotResult("not_ready", None)


def test_llm_error_text_logged_for_status(caplog):
    caplog.set_level("WARNING")
    ask(FakeClient(LLMUnavailable("llm unavailable: http 503")))
    assert "LLMUnavailable" in caplog.text and "http 503" in caplog.text
