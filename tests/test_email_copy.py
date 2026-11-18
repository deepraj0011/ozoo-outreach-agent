"""Task 20: varied, role-specific email copy and automatic replies to every kind of answer."""
from __future__ import annotations

import copy
import re
from datetime import date

import pytest

from outreach.actions import SendReply
from outreach.chatbot import load_kb
from outreach.classifier import make_classifier
from outreach.email_answer import answer_question
from outreach.llm.fake import FakeLLM
from outreach.llm.local_fake import RuleBasedLLM
from outreach.models import Event
from outreach.sequencer import (ANGLE_FIELDS, STEP_VARIANTS, angle_for, has_reply, load_templates, render_reply,
                                render_step)
from tests.conftest import NOW, make_lead
from tests.fakes import make_settings

ST = make_settings()
KB = load_kb()


@pytest.fixture
def tpl():
    return load_templates()


ROLES = {
    "Head of Collections & Recovery": "collections",
    "VP Quality and Compliance": "quality",
    "Chief Risk Officer": "quality",
    "National Head - Tele-sales": "sales",
    "Head of Customer Service": "service",
    "Head of Contact Centre Operations": "service",
    "CTO": "technology",
    "Chief Technology Officer": "technology",
    "Managing Director": "general",
}


@pytest.mark.parametrize("role,angle", sorted(ROLES.items()))
def test_angle_comes_from_the_role(session, tpl, role, angle):
    assert angle_for(make_lead(session, role=role), tpl) == angle


def test_angle_falls_back_to_industry_then_general(session, tpl):
    assert angle_for(make_lead(session, role=None, industry="Collections agency"), tpl) == "collections"
    assert angle_for(make_lead(session, role="", industry=""), tpl) == "general"


def test_every_email_renders_completely_for_every_angle_and_variant(session, tpl):
    """No leftover {placeholder}, for every role, variant and step."""
    for role in ROLES:
        lead = make_lead(session, name="Asha Rao", company="Acme Finance", role=role)
        for variants in ([], ["case_study"], ["product"], ["booking_slots"]):
            lead.variant_flags = {"variants": variants}
            for step in (1, 2, 3, 4):
                out = render_step(lead, step, templates=tpl, settings=ST)
                text = out[f"subject_{step}"] + out[f"body_{step}"]
                assert not re.search(r"\{[a-z_]+\}", text), (role, variants, step, text)
                assert out[f"subject_{step}"].strip() and "Hi Asha," in out[f"body_{step}"]


def test_two_leads_at_the_same_bank_get_different_emails(session, tpl):
    leads = [make_lead(session, name=f"P{i} X", company="Acme Finance", role="Head of Collections")
             for i in range(12)]
    first = {render_step(ld, 1, templates=tpl, settings=ST)["body_1"].split("\n", 1)[1] for ld in leads}
    subjects = {render_step(ld, 1, templates=tpl, settings=ST)["subject_1"] for ld in leads}
    assert len(first) >= 3 and len(subjects) >= 2


def test_a_lead_always_gets_the_same_version(session, tpl):
    lead = make_lead(session, role="CTO")
    assert render_step(lead, 3, templates=tpl, settings=ST) == render_step(lead, 3, templates=tpl, settings=ST)


def test_steps_differ_for_one_lead(session, tpl):
    lead = make_lead(session, role="Head of Customer Service")
    bodies = [render_step(lead, s, templates=tpl, settings=ST)[f"body_{s}"] for s in (1, 2, 3, 4)]
    assert len(set(bodies)) == 4


def test_signature_comes_from_settings(session, tpl):
    lead = make_lead(session)
    st = make_settings(email_signature="Riya Sharma\nOzoo.ai")
    assert "Riya Sharma\nOzoo.ai" in render_step(lead, 1, templates=tpl, settings=st)["body_1"]


def test_copy_states_no_price_and_never_mentions_tracking(tpl):
    from tests.test_sequencer import _all_text
    for text in _all_text(tpl):
        assert not re.search(r"[₹$€£]|\b(?:INR|Rs\.?)\s*\d", text), text
        assert not re.search(r"\b(opened|clicked|you read|saw you)\b", text.lower()), text


def test_replies_render_with_values(session, tpl):
    lead = make_lead(session, name="Asha Rao", company="Acme")
    body = render_reply(lead, "question_answer", templates=tpl, settings=ST, values={"answer": "Yes, in Hindi."})
    assert "Yes, in Hindi." in body and "{" not in body
    nn = render_reply(lead, "not_now_ack", templates=tpl, settings=ST, values={"follow_up_when": "in March"})
    assert "check back in March" in nn
    assert "{" not in render_reply(lead, "who_is_right", templates=tpl, settings=ST)
    # only the two reply values are accepted from outside
    assert "{" not in render_reply(lead, "booking_link", templates=tpl, settings=ST, values={"company": "X"})


def test_courtesy_replies_are_optional(tmp_path, tpl):
    import yaml
    t = copy.deepcopy(tpl)
    del t["replies"]["not_interested_ack"]
    p = tmp_path / "t.yaml"
    p.write_text(yaml.safe_dump(t))
    assert has_reply("not_interested_ack", load_templates(str(p))) is False
    assert has_reply("not_interested_ack", tpl) is True


def test_angle_validation(tmp_path, tpl):
    import yaml
    breakers = (lambda t: t["angles"].pop("general"),
                lambda t: t["angles"]["quality"].pop("proof"),
                lambda t: t["angles"]["quality"].__setitem__("hook", "uses {hook} itself"))
    for i, breaker in enumerate(breakers):
        t = copy.deepcopy(tpl)
        breaker(t)
        p = tmp_path / f"bad{i}.yaml"
        p.write_text(yaml.safe_dump(t))
        with pytest.raises(ValueError):
            load_templates(str(p))
    assert set(ANGLE_FIELDS) == {"topic", "hook", "value", "proof", "ask"} and "standard" in STEP_VARIANTS


# ---------------------------------------------------------------- answering a question

def ask(llm, text="Which languages do you support?"):
    return answer_question(text, KB, llm, model="m", today=date(2026, 11, 12), lead_id=1)


def test_answer_from_the_kb_is_returned():
    text = "Post-Call Analytics handles Hindi, Hinglish and English."
    assert ask(FakeLLM({"kind": "answer", "text": text})) == text


@pytest.mark.parametrize("out", [
    {"kind": "answer", "text": "It costs ₹50,000 per month."},           # a price not in the KB
    {"kind": "answer", "text": "See https://ozoo.ai/pricing for more."},  # a link
    {"kind": "answer", "text": "Write to sales@ozoo.ai for details."},    # an address
    {"kind": "out_of_scope"},
    {"kind": "wants_human"},
    {"oops": 1},
    "not a dict",
])
def test_anything_unsafe_or_unsure_goes_to_a_person(out):
    assert ask(FakeLLM(out)) is None


def test_llm_failure_goes_to_a_person():
    assert ask(FakeLLM(RuntimeError("down"))) is None
    assert ask(FakeLLM({"kind": "answer", "text": "x"}), text="   ") is None


def test_local_rule_based_model_answers_a_kb_question():
    assert ask(RuleBasedLLM(), "Is there a free trial?") is not None


def _reply(session, lead, text):
    ev = Event(lead_id=lead.id, channel="email", kind="reply", occurred_at=NOW, received_at=NOW,
               verdict="human", processed=True, data={"text": text})
    session.add(ev)
    session.flush()
    return ev


def test_classifier_answers_an_emailed_question_end_to_end(session):
    lead = make_lead(session, active_channel="email", state="engaged")
    llm = FakeLLM({"label": "question", "confidence": 0.95, "reason": "asks", "draft_answer": "d"},
                  {"kind": "answer", "text": "Yes. Post-Call Analytics has a 30-day trial."})
    acts = make_classifier(make_settings(llm_classifier_model="c", llm_chat_model="chat"), llm=llm)(
        session, lead, _reply(session, lead, "Is there a trial?"), NOW)
    assert acts[0] == SendReply("question_answer", (("answer", "Yes. Post-Call Analytics has a 30-day trial."),))
    assert [c["tool_name"] for c in llm.calls] == ["classify_reply", "bot_reply"]
    assert llm.calls[1]["model"] == "chat"


def test_classifier_acknowledges_a_question_it_cannot_answer(session):
    lead = make_lead(session, active_channel="email", state="engaged")
    llm = FakeLLM({"label": "question", "confidence": 0.95, "reason": "asks", "draft_answer": "d"},
                  {"kind": "out_of_scope"})
    acts = make_classifier(make_settings(llm_classifier_model="c"), llm=llm)(
        session, lead, _reply(session, lead, "Do you integrate with our custom LOS?"), NOW)
    assert acts[0] == SendReply("question_ack")
