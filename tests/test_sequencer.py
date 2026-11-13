from __future__ import annotations

import copy
from datetime import timedelta

import pytest

from outreach import sequencer
from outreach.actions import Alert, SetState, SwitchChannel
from outreach.emailtool.fake import FakeEmailTool
from outreach.links import parse_token
from outreach.sequencer import (
    END_WAIT_DAYS, SCHEDULE_DAYS, SequencerConfigError, end_of_sequence_route, enroll,
    load_templates, refresh_next_step, render_reply, render_step,
)
from outreach.states import LeadState
from tests.conftest import NOW, make_lead
from tests.fakes import make_settings

VARIANTS = ["standard", "new_subject", "case_study", "product", "team_invite", "booking_slots"]
SPY = ("opened", "clicked", "read your", "saw you")


@pytest.fixture
def st():
    return make_settings()


@pytest.fixture
def tpl():
    return load_templates()


def _all_text(tpl):
    for step, blocks in tpl["steps"].items():
        for v, d in blocks.items():
            for k, t in d.items():
                yield t
    for d in tpl["replies"].values():
        yield d["body"]


def test_constants():
    assert SCHEDULE_DAYS == {1: 0, 2: 4, 3: 9, 4: 15}
    assert END_WAIT_DAYS == 5


def test_templates_have_all_keys(tpl):
    for step in (1, 2, 3, 4):
        blocks = tpl["steps"][step]
        for v in VARIANTS:
            if v == "new_subject" and step == 1:
                continue
            assert v in blocks, (step, v)
            assert blocks[v]["subject"]
            if v != "new_subject":
                assert "{booking_link}" in blocks[v]["body"]
                assert "{unsubscribe_link}" in blocks[v]["body"]
    for k in ("booking_link", "booking_slots", "reschedule"):
        body = tpl["replies"][k]["body"]
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

    exp = lambda v: tpl["steps"][1][v]["subject"].format(first_name="Test", company="Acme")
    assert body([])["subject_1"] == exp("standard")
    assert body(["new_subject"])["subject_1"] == exp("standard")
    assert body(["team_invite"])["subject_1"] == exp("team_invite")
    assert body(["product", "team_invite"])["subject_1"] == exp("product")
    assert body(["case_study", "product"])["subject_1"] == exp("case_study")
    assert body(["booking_slots", "case_study", "product", "team_invite"])["subject_1"] == exp("booking_slots")


def test_new_subject_only_when_previous_unopened(session, st, tpl):
    lead = make_lead(session, variant_flags={"opened_steps": [1]})
    assert render_step(lead, 2, templates=tpl, settings=st)["subject_2"] == "Following up, Test"
    lead2 = make_lead(session, variant_flags={})
    assert render_step(lead2, 2, templates=tpl, settings=st)["subject_2"] == "A different angle for Acme"
    # step 1 never uses new_subject
    assert render_step(lead2, 1, templates=tpl, settings=st)["subject_1"] == "Quick idea for Acme"


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
    assert set(tool.calls[0][2]) == {"subject_2", "body_2"}


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
    bad2["steps"][1]["standard"]["body"] += " {bogus}"
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
    track, acts = end_of_sequence_route(lead, 3, 2)
    assert track == "sales_personal"
    assert acts[0] == SetState(LeadState.NURTURE, "end_hot")
    assert any(isinstance(a, Alert) and a.kind == "hot_no_reply" for a in acts)


def test_end_route_never_opened_switches_to_whatsapp(session):
    lead = make_lead(session, phone="+911234567890", consent_whatsapp=True, active_channel="email")
    track, acts = end_of_sequence_route(lead, 0, 0)
    assert track == "whatsapp"
    assert acts == [SwitchChannel("never_opened")]


def test_end_route_never_opened_no_wa_goes_slow(session):
    lead = make_lead(session, active_channel="email")
    assert end_of_sequence_route(lead, 0, 0)[0] == "slow_3m"
    lead2 = make_lead(session, phone="+911234567891", consent_whatsapp=True,
                      active_channel="email", channel_switched=True)
    assert end_of_sequence_route(lead2, 0, 0)[0] == "slow_3m"
    lead3 = make_lead(session, phone="+911234567892", consent_whatsapp=False, active_channel="email")
    assert end_of_sequence_route(lead3, 0, 0)[0] == "slow_3m"


def test_end_route_clicked(session):
    track, acts = end_of_sequence_route(make_lead(session, score=6), 1, 1)
    assert track == "warm_2_3w" and acts == [SetState(LeadState.NURTURE, "end_clicked")]


def test_end_route_opened(session):
    track, acts = end_of_sequence_route(make_lead(session, score=2), 2, 0)
    assert track == "monthly" and acts == [SetState(LeadState.NURTURE, "end_opened")]


def test_end_route_silent(session):
    track, acts = end_of_sequence_route(make_lead(session), 0, 0)
    assert track == "slow_3m" and acts == [SetState(LeadState.NURTURE, "end_silent")]
