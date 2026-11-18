from __future__ import annotations

import copy
from datetime import timedelta

import pytest

from outreach import sequencer
from outreach.actions import Alert, SetState, SwitchChannel, ToolStop
from outreach.emailtool.fake import FakeEmailTool
from outreach.links import make_link, parse_token
from outreach.sequencer import (
    END_WAIT_DAYS, SCHEDULE_DAYS, SequencerConfigError, end_of_sequence_route, enroll,
    load_templates, refresh_next_step, render_reply, render_step,
)
from outreach.states import LeadState
from tests.conftest import NOW, make_lead
from tests.fakes import make_settings

VARIANTS = ["standard", "case_study", "product", "booking_slots"]
SPY = ("opened", "clicked", "read your", "saw you")


@pytest.fixture
def st():
    return make_settings()


@pytest.fixture
def tpl():
    return load_templates()


def _alts(t):
    return t if isinstance(t, list) else [t]


def _all_text(tpl):
    for step, blocks in tpl["steps"].items():
        for v, d in blocks.items():
            for k, t in d.items():
                yield from _alts(t)
    for d in tpl["replies"].values():
        yield from _alts(d["body"])
    for angle in (tpl.get("angles") or {}).values():
        for k, t in angle.items():
            if k != "match":
                yield from _alts(t)


def test_constants():
    assert SCHEDULE_DAYS == {1: 0, 2: 4, 3: 9, 4: 15}
    assert END_WAIT_DAYS == 5


def test_templates_have_all_keys(tpl):
    for step in (1, 2, 3, 4):
        blocks = tpl["steps"][step]
        assert set(blocks) == set(VARIANTS), step  # no open-driven variants
        for v in VARIANTS:
            assert all(_alts(blocks[v]["subject"]))
            for body in _alts(blocks[v]["body"]):
                assert "{booking_link}" in body and "{unsubscribe_link}" in body
    for k in ("booking_link", "booking_slots", "reschedule", "question_answer", "question_ack"):
        for body in _alts(tpl["replies"][k]["body"]):
            assert "{booking_link}" in body and "{unsubscribe_link}" in body


def test_no_spying_phrases(tpl):
    for text in _all_text(tpl):
        low = text.lower()
        for p in SPY:
            assert p not in low, (p, text)


def test_render_uses_tracked_links(session, st, tpl):
    lead = make_lead(session, name="Asha Rao", company="Acme")
    out = render_step(lead, 2, templates=tpl, settings=st)
    assert set(out) == {"subject_2", "body_2"}
    body = out["body_2"]
    assert "{" not in body
    tokens = [w.rsplit("/r/", 1)[1] for w in body.split() if "/r/" in w]
    tags = set()
    for t in tokens:
        parsed = parse_token(t.rstrip(".,"), settings=st)
        assert parsed is not None and parsed[0] == lead.id and parsed[1] == 2
        tags.add(parsed[2])
    assert {"booking", "unsubscribe"} <= tags
    assert "Asha" in body and "Rao" not in body.split("\n")[0]


def test_variant_priority(session, st, tpl):
    def body(variants):
        lead = make_lead(session, variant_flags={"variants": variants})
        return render_step(lead, 1, templates=tpl, settings=st)

    def subjects(v):  # every way the variant's subject can come out for a lead at Acme
        topics = [t.format(company="Acme") for a in tpl["angles"].values() for t in _alts(a["topic"])]
        out = {s.format(first_name="Test", company="Acme", topic=t) for s in _alts(tpl["steps"][1][v]["subject"])
               for t in topics}
        return out | {x[:1].upper() + x[1:] for x in out}  # subjects start with a capital
    assert body([])["subject_1"] in subjects("standard")
    assert body(["product"])["subject_1"] in subjects("product")
    assert body(["case_study", "product"])["subject_1"] in subjects("case_study")
    assert body(["booking_slots", "case_study", "product"])["subject_1"] in subjects("booking_slots")


def test_subject_does_not_depend_on_opens(session, st, tpl):
    lead = make_lead(session, variant_flags={})
    before = render_step(lead, 2, templates=tpl, settings=st)
    lead.variant_flags = {"opened_steps": [1], "human_opens": 3}
    assert render_step(lead, 2, templates=tpl, settings=st) == before


def test_subject_injection_sanitised(session, st, tpl):
    t = copy.deepcopy(tpl)
    t["steps"][1]["standard"]["subject"] = "Hi {first_name} at {company}"
    lead = make_lead(session, name="Bob\r\nBcc: x@y.z", company="Ac\tme\n{booking_link}" + "x" * 200)
    out = render_step(lead, 1, templates=t, settings=st)
    subj = out["subject_1"]
    assert "\r" not in subj and "\n" not in subj and "\t" not in subj
    assert subj.startswith("Hi Bob at Ac me")
    assert len(subj) <= len("Hi  at ") + 160
    # inserted values never re-formatted
    assert "{booking_link}" in subj


def test_render_reply(session, st, tpl):
    lead = make_lead(session, step_sent=2)
    for key in ("booking_link", "booking_slots", "reschedule"):
        body = render_reply(lead, key, templates=tpl, settings=st)
        assert "{" not in body and "/r/" in body
    body = render_reply(lead, "booking_link", templates=tpl, settings=st)
    tok = [w for w in body.split() if "/r/" in w][0].rsplit("/r/", 1)[1]
    assert parse_token(tok, settings=st)[1] == 2


def test_reply_templates_render(session, st, tpl):
    lead = make_lead(session, name="Zed Q", company="Co")
    assert "Zed" in render_reply(lead, "booking_link", templates=tpl, settings=st)
    with pytest.raises(KeyError):
        render_reply(lead, "nope", templates=tpl, settings=st)


def test_enroll_calls_tool_once_and_stores_id(session, st, tpl):
    tool = FakeEmailTool()
    lead = make_lead(session, name="Asha Rao", company="Acme", timezone_group="in_a")
    enroll(session, lead, tool, NOW, settings=st, templates=tpl)
    adds = [c for c in tool.calls if c[0] == "add_lead"]
    assert len(adds) == 1
    _, campaign, lid, variables = adds[0]
    assert campaign == "c1" and lid == lead.id
    for n in (1, 2, 3, 4):
        assert f"subject_{n}" in variables and f"body_{n}" in variables
    assert variables["first_name"] == "Asha" and variables["company"] == "Acme"
    assert lead.tool_lead_id == "fake-1"
    assert lead.variant_flags["enrolled_at"] == NOW.isoformat()


def test_enroll_idempotent(session, st, tpl):
    tool = FakeEmailTool()
    lead = make_lead(session, tool_lead_id="existing")
    enroll(session, lead, tool, NOW, settings=st, templates=tpl)
    assert tool.calls == [] and lead.tool_lead_id == "existing"
    lead2 = make_lead(session)
    enroll(session, lead2, tool, NOW, settings=st, templates=tpl)
    enroll(session, lead2, tool, NOW + timedelta(days=1), settings=st, templates=tpl)
    assert len([c for c in tool.calls if c[0] == "add_lead"]) == 1


def test_enroll_default_group_used(session, st, tpl):
    tool = FakeEmailTool()
    lead = make_lead(session, timezone_group=None)
    enroll(session, lead, tool, NOW, settings=st, templates=tpl)
    assert tool.calls[0][1] == "c1"


def test_missing_campaign_raises_without_tool_call(session, st, tpl):
    tool = FakeEmailTool()
    lead = make_lead(session, timezone_group="eu_z")
    with pytest.raises(SequencerConfigError, match="eu_z"):
        enroll(session, lead, tool, NOW, settings=st, templates=tpl)
    assert tool.calls == [] and lead.tool_lead_id is None


def test_refresh_updates_next_step(session, st, tpl):
    tool = FakeEmailTool()
    lead = make_lead(session, tool_lead_id="t1", step_sent=1)
    refresh_next_step(session, lead, tool, settings=st, templates=tpl)
    assert tool.calls[0][0] == "update_variables" and tool.calls[0][1] == "t1"
    assert set(tool.calls[0][2]) == {"subject_2", "body_2", "unsubscribe_url_2", "trap_url_2"}  # pixel off by default


def test_refresh_noop_without_tool_id(session, st, tpl):
    tool = FakeEmailTool()
    refresh_next_step(session, make_lead(session, step_sent=1), tool, settings=st, templates=tpl)
    assert tool.calls == []


def test_refresh_noop_after_step4(session, st, tpl):
    tool = FakeEmailTool()
    lead = make_lead(session, tool_lead_id="t1", step_sent=4)
    refresh_next_step(session, lead, tool, settings=st, templates=tpl)
    assert tool.calls == []


def test_template_validation_names_missing_key(tmp_path, tpl):
    import yaml
    bad = copy.deepcopy(tpl)
    del bad["steps"][3]["case_study"]
    p = tmp_path / "t.yaml"
    p.write_text(yaml.safe_dump(bad))
    with pytest.raises(ValueError, match="case_study"):
        load_templates(str(p))
    bad2 = copy.deepcopy(tpl)
    bad2["steps"][1]["standard"]["body"][0] += " {bogus}"
    p2 = tmp_path / "t2.yaml"
    p2.write_text(yaml.safe_dump(bad2))
    with pytest.raises(ValueError, match="bogus"):
        load_templates(str(p2))
    bad3 = copy.deepcopy(tpl)
    bad3["replies"]["reschedule"]["body"] = "no links"
    p3 = tmp_path / "t3.yaml"
    p3.write_text(yaml.safe_dump(bad3))
    with pytest.raises(ValueError, match="booking_link"):
        load_templates(str(p3))


def test_load_templates_cached_and_cwd_independent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert load_templates() is load_templates()


# --- end of sequence
def test_end_route_hot(session):
    lead = make_lead(session, score=20)
    track, acts = end_of_sequence_route(lead, 2)
    assert track == "sales_personal"
    assert acts[:2] == [ToolStop(), SetState(LeadState.NURTURE, "end_hot")]
    assert any(isinstance(a, Alert) and a.kind == "hot_no_reply" for a in acts)


def test_end_route_never_switches_to_whatsapp(session):
    # WhatsApp opt-in leads already got WhatsApp on day 1: no "try WhatsApp later" step
    lead = make_lead(session, phone="+911234567890", consent_whatsapp=True, active_channel="email")
    track, acts = end_of_sequence_route(lead, 0)
    assert track == "monthly"
    assert not any(isinstance(a, SwitchChannel) for a in acts)


def test_end_route_clicked(session):
    track, acts = end_of_sequence_route(make_lead(session, score=6), 1)
    assert track == "warm_2_3w" and acts == [ToolStop(), SetState(LeadState.NURTURE, "end_clicked")]


def test_end_route_no_clicks_monthly_whatever_the_opens(session):
    lead = make_lead(session, score=2, variant_flags={"human_opens": 5})
    track, acts = end_of_sequence_route(lead, 0)
    assert track == "monthly" and acts == [ToolStop(), SetState(LeadState.NURTURE, "end_silent")]


# ---------------------------------------------------------------- Task 12 review: resume mid-sequence

def test_enroll_from_step_shifts_the_campaign_steps(session, st, tpl):
    tool = FakeEmailTool()
    lead = make_lead(session, step_sent=2)
    enroll(session, lead, tool, NOW, settings=st, templates=tpl, from_step=3)
    _, _, _, variables = tool.calls[0]
    real3, real4 = render_step(lead, 3, templates=tpl, settings=st), render_step(lead, 4, templates=tpl, settings=st)
    assert (variables["subject_1"], variables["body_1"]) == (real3["subject_3"], real3["body_3"])
    assert (variables["subject_2"], variables["body_2"]) == (real4["subject_4"], real4["body_4"])
    # campaign steps past our last step repeat it (never blank); the tool lead is stopped first
    assert variables["subject_3"] == real4["subject_4"] and variables["subject_4"] == real4["subject_4"]
    assert lead.variant_flags["step_offset"] == 2


def test_enroll_from_step_one_has_no_offset(session, st, tpl):
    tool = FakeEmailTool()
    lead = make_lead(session, variant_flags={"step_offset": 2})
    enroll(session, lead, tool, NOW, settings=st, templates=tpl)
    assert "step_offset" not in lead.variant_flags


def test_enroll_from_step_out_of_range(session, st, tpl):
    with pytest.raises(ValueError):
        enroll(session, make_lead(session), FakeEmailTool(), NOW, settings=st, templates=tpl, from_step=5)


def test_refresh_after_resume_targets_the_campaign_step(session, st, tpl):
    tool = FakeEmailTool()
    lead = make_lead(session, tool_lead_id="t1", step_sent=3, variant_flags={"step_offset": 2})
    st = st.model_copy(update={"email_open_pixel": True})
    refresh_next_step(session, lead, tool, settings=st, templates=tpl)
    real4 = render_step(lead, 4, templates=tpl, settings=st)
    sent = dict(tool.calls[0][2])
    pixel = sent.pop("open_pixel_2")
    unsub = sent.pop("unsubscribe_url_2")
    sent.pop("trap_url_2")
    assert unsub.rsplit("/u/", 1)[1] == make_link(lead.id, 4, "unsubscribe", settings=st).rsplit("/r/", 1)[1]
    assert sent == {"subject_2": real4["subject_4"], "body_2": real4["body_4"]}
    from outreach.links import parse_open_token
    assert parse_open_token(pixel.rsplit("/o/", 1)[1], settings=st) == (lead.id, 4)  # our step, not the campaign's
