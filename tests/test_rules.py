from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

import pytest

from outreach.actions import (
    AddScore, Alert, BotReply, Classify, HandOff, PauseColleagues, RefreshNextStep,
    SendReply, SetFlag, SetState, SetStepSent, SetVariant, StopAllOutreach, Suppress,
    SuppressAllChannels, SwitchChannel, ToolStop, WaCancelReminder, WaSendText, flatten,
)
from outreach.models import Event
from outreach.rules import EventSnapshot, LeadSnapshot, decide
from outreach.score import points_for
from outreach.states import ALLOWED, TERMINAL, LeadState as S
from tests.conftest import NOW, make_lead
NOW_ISO = NOW.isoformat()

EMAIL = "lead@acme.com"
PHONE = "+14155550100"

_CHANNEL = {"booking_created": "cal", "no_show": "cal", "meeting_ended": "cal", "click": "web",
            "booking_rescheduled": "cal", "booking_cancelled": "cal", "form_submitted": "cal"}


def lead_snap(**kw) -> LeadSnapshot:
    d = dict(
        id=1, state=S.IN_SEQUENCE, score=0, step_sent=1, active_channel="email",
        channel_switched=False, has_usable_email=True, has_wa_optin=False,
        email=EMAIL, phone=None, wa_human_owner=None, wa_window_open=False, flags={},
    )
    d.update(kw)
    d["flags"] = MappingProxyType(dict(d["flags"]))
    return LeadSnapshot(**d)


def both(**kw) -> LeadSnapshot:
    """Lead with usable email and WhatsApp opt-in."""
    d = dict(phone=PHONE, has_wa_optin=True)
    d.update(kw)
    return lead_snap(**d)


def wa_lead(**kw) -> LeadSnapshot:
    d = dict(active_channel="whatsapp", email=None, has_usable_email=False,
             phone=PHONE, has_wa_optin=True)
    d.update(kw)
    return lead_snap(**d)


def ev(kind: str, **kw) -> EventSnapshot:
    channel = _CHANNEL.get(kind, "whatsapp" if kind.startswith("wa_") else "email")
    d = dict(kind=kind, channel=channel, tag=None, verdict="human", step=None,
             occurred_at=NOW, data={})
    d.update(kw)
    d["data"] = MappingProxyType(dict(d["data"]))
    return EventSnapshot(**d)


def click(tag: str, **kw) -> EventSnapshot:
    return ev("click", tag=tag, **kw)


def button(payload: str) -> EventSnapshot:
    return ev("wa_button", data={"payload": payload})


def text(t: str) -> EventSnapshot:
    return ev("wa_text", data={"text": t})


def failed(code) -> EventSnapshot:
    return ev("wa_failed", data={"code": code})


def run(lead, event):
    # the per-tag click counter (final review B5) is tested in test_final_review_b.py
    return [a for a in decide(lead, event, NOW) if not (isinstance(a, SetFlag) and a.key == "click_points")]


def kinds(actions):
    return [type(a).__name__ for a in actions]


HARD = Suppress(EMAIL, "email", "hard_bounce")
STOP = StopAllOutreach()


def optout_actions(reason):
    return [SuppressAllChannels(reason), STOP, SetState(S.SUPPRESSED, reason)]


# ---------------------------------------------------------------- actions


def test_stop_all_outreach_flattens_to_toolstop_and_wa_cancel():
    assert flatten([STOP, Classify()]) == [ToolStop(), WaCancelReminder(), Classify()]


def test_alert_rejects_unknown_kind():
    with pytest.raises(ValueError):
        Alert("nope")
    for k in ("wa_send_failed", "handoff", "hot_lead", "wa_sla_missed"):
        Alert(k)


def test_actions_are_frozen():
    with pytest.raises(Exception):
        AddScore(1).points = 2  # type: ignore[misc]


# ---------------------------------------------------------------- score.points_for


@pytest.mark.parametrize("kind,tag,opens,read,pts", [
    ("open", None, 0, False, 0), ("open", None, 1, False, 0), ("open", None, 2, False, 0),
    ("open", None, 3, False, 0), ("click", "homepage", 0, False, 2),
    ("click", "case_study", 0, False, 5), ("click", "product", 0, False, 5),
    ("click", "pricing", 0, False, 10), ("click", "booking", 0, False, 10),
    ("click", "unsubscribe", 0, False, 0), ("click", None, 0, False, 0),
    ("wa_read", None, 0, False, 1), ("wa_read", None, 0, True, 0), ("reply", None, 0, False, 0),
])
def test_points_for(kind, tag, opens, read, pts):
    assert points_for(kind, tag, opens, read) == pts


# ---------------------------------------------------------------- snapshots


def test_lead_snapshot_from_lead(session):
    lead = make_lead(session, email="x@acme.com", consent_email=True, verify_status="catch_all",
                     phone=PHONE, consent_whatsapp=True, state=S.ENGAGED, score=7,
                     step_sent=2, active_channel="whatsapp", channel_switched=True,
                     wa_human_owner="sam", wa_window_until=NOW.replace(hour=11),
                     variant_flags={"human_opens": 2})
    snap = LeadSnapshot.from_lead(lead, NOW)
    assert snap.id == lead.id and snap.state is S.ENGAGED and snap.score == 7
    assert snap.step_sent == 2 and snap.active_channel == "whatsapp" and snap.channel_switched
    assert snap.has_usable_email and snap.has_wa_optin and snap.wa_window_open
    assert snap.email == "x@acme.com" and snap.phone == PHONE and snap.wa_human_owner == "sam"
    assert snap.flags["human_opens"] == 2
    # snapshot is detached from the ORM dict
    lead.variant_flags["human_opens"] = 9
    assert snap.flags["human_opens"] == 2

    lead.verify_status = "invalid"
    lead.consent_whatsapp = False
    lead.wa_window_until = NOW
    snap2 = LeadSnapshot.from_lead(lead, NOW)
    assert not snap2.has_usable_email and not snap2.has_wa_optin and not snap2.wa_window_open
    lead.wa_window_until = None
    assert not LeadSnapshot.from_lead(lead, NOW).wa_window_open


def test_event_snapshot_from_event():
    e = Event(lead_id=1, channel="email", kind="open", occurred_at=NOW, received_at=NOW,
              step=2, tag=None, verdict="human", data={"x": 1})
    snap = EventSnapshot.from_event(e)
    assert (snap.kind, snap.channel, snap.tag, snap.verdict, snap.step) == (
        "open", "email", None, "human", 2)
    assert snap.occurred_at == NOW and snap.data["x"] == 1
    e2 = Event(lead_id=1, channel="email", kind="reply", occurred_at=NOW, received_at=NOW)
    assert EventSnapshot.from_event(e2).data == {}


# ---------------------------------------------------------------- email rows


def test_hard_bounce_no_wa():
    assert run(lead_snap(), ev("bounce_hard")) == [
        HARD, ToolStop(), SetState(S.SUPPRESSED, "hard_bounce")]


def test_hard_bounce_with_wa_switches():
    assert run(both(), ev("bounce_hard")) == [HARD, ToolStop(), SwitchChannel("bounce")]


def test_hard_bounce_with_wa_already_switched_suppresses():
    assert run(both(channel_switched=True), ev("bounce_hard")) == [
        HARD, ToolStop(), SetState(S.SUPPRESSED, "hard_bounce")]


def test_hard_bounce_in_booked_keeps_state():
    for st in (S.ENGAGED, S.QUALIFYING, S.BOOKED):
        assert run(both(state=st), ev("bounce_hard")) == [HARD, ToolStop()]
        assert run(lead_snap(state=st, flags={"soft_bounce_count": 2}),
                   ev("bounce_soft")) == [HARD, ToolStop()]


def test_hard_bounce_while_whatsapp_active_keeps_whatsapp():
    for st in (S.IN_SEQUENCE, S.PAUSED, S.NURTURE):
        assert run(both(state=st, active_channel="whatsapp"), ev("bounce_hard")) == [
            HARD, ToolStop()]
        assert run(both(state=st, active_channel="whatsapp", channel_switched=True),
                   ev("bounce_hard")) == [HARD, ToolStop()]


def test_hard_bounce_in_nurture_suppresses():
    assert run(lead_snap(state=S.NURTURE), ev("bounce_hard")) == [
        HARD, ToolStop(), SetState(S.SUPPRESSED, "hard_bounce")]
    assert run(lead_snap(state=S.PAUSED), ev("bounce_hard")) == [
        HARD, ToolStop(), SetState(S.SUPPRESSED, "hard_bounce")]
    assert run(both(state=S.NURTURE), ev("bounce_hard")) == [
        HARD, ToolStop(), SwitchChannel("bounce")]


def test_hard_bounce_terminal_only_suppresses_email():
    for st in TERMINAL:
        assert run(lead_snap(state=st), ev("bounce_hard")) == [HARD]


def test_third_soft_bounce_is_hard():
    assert run(lead_snap(flags={"soft_bounce_count": 2}), ev("bounce_soft")) == [
        HARD, ToolStop(), SetState(S.SUPPRESSED, "hard_bounce")]
    assert run(both(flags={"soft_bounce_count": 2}), ev("bounce_soft")) == [
        HARD, ToolStop(), SwitchChannel("bounce")]


def test_soft_bounce_waits():
    assert run(lead_snap(), ev("bounce_soft")) == [SetFlag("soft_bounce_count", 1)]
    assert run(lead_snap(flags={"soft_bounce_count": 1}), ev("bounce_soft")) == [
        SetFlag("soft_bounce_count", 2)]


def test_bot_click_ignored():
    assert run(lead_snap(), click("pricing", verdict="bot")) == []


def test_machine_open_ignored():
    assert run(lead_snap(), ev("open", step=1, verdict="machine")) == []


def test_bot_wa_read_ignored():
    assert run(wa_lead(), ev("wa_read", verdict="bot")) == []


def test_human_open_is_counted_only():
    # opens are unreliable: logged for reporting, never acted on
    assert run(lead_snap(), ev("open", step=1)) == [SetFlag("human_opens", 1)]


def test_many_opens_never_act():
    out = run(lead_snap(score=14, flags={"human_opens": 2}), ev("open", step=1))
    assert out == [SetFlag("human_opens", 3)]  # no score, no variant, no subject change, no alert


def test_homepage_click():
    assert run(lead_snap(), click("homepage")) == [AddScore(2)]


def test_case_study_click():
    assert run(lead_snap(), click("case_study")) == [AddScore(5), SetVariant("case_study")]


def test_product_click():
    assert run(lead_snap(), click("product")) == [AddScore(5), SetVariant("product")]


def test_case_study_click_whatsapp_active_no_variant():
    assert run(wa_lead(), click("case_study")) == [AddScore(5)]


def test_pricing_click_hot():
    assert run(lead_snap(), click("pricing")) == [
        AddScore(10), Alert("hot_lead"), SetFlag("hot_alerted", True),
        SetVariant("booking_slots"), SendReply("booking_slots"), SetFlag("booking_slots_at", NOW_ISO)]


def test_pricing_click_already_alerted_no_second_alert():
    assert run(lead_snap(score=20, flags={"hot_alerted": True}), click("pricing")) == [
        AddScore(10), SetFlag("hot_alerted", True),
        SetVariant("booking_slots"), SendReply("booking_slots"), SetFlag("booking_slots_at", NOW_ISO)]


def test_pricing_click_crossing_hot_alerts_once():
    out = run(lead_snap(score=10), click("pricing"))
    assert kinds(out).count("Alert") == 1


def test_pricing_click_whatsapp_window_open():
    assert run(wa_lead(wa_window_open=True), click("pricing")) == [
        AddScore(10), Alert("hot_lead"), SetFlag("hot_alerted", True),
        WaSendText("booking_slots"), SetFlag("booking_slots_at", NOW_ISO)]


def test_pricing_click_whatsapp_window_closed_only_alert_and_score():
    assert run(wa_lead(), click("pricing")) == [
        AddScore(10), Alert("hot_lead"), SetFlag("hot_alerted", True)]


def test_booking_click():
    assert run(lead_snap(), click("booking")) == [AddScore(10)]


def test_score_crossing_hot_alerts_once():
    out = run(lead_snap(score=13), click("homepage"))
    assert out == [AddScore(2), Alert("hot_lead"), SetFlag("hot_alerted", True)]
    assert run(lead_snap(score=13, flags={"hot_alerted": True}), click("homepage")) == [
        AddScore(2)]
    # already hot: no crossing
    assert run(lead_snap(score=15), click("homepage")) == [AddScore(2)]


def test_email_reply():
    assert run(lead_snap(), ev("reply")) == [
        STOP, SetState(S.ENGAGED, "reply"), Classify(), PauseColleagues()]


@pytest.mark.parametrize("st", [S.IN_SEQUENCE, S.PAUSED, S.NURTURE])
def test_email_reply_from_outreach_states_engages(st):
    assert run(lead_snap(state=st), ev("reply")) == [
        STOP, SetState(S.ENGAGED, "reply"), Classify(), PauseColleagues()]


@pytest.mark.parametrize("st", [S.ENGAGED, S.QUALIFYING, S.BOOKED])
def test_email_reply_in_conversation_only_classifies(st):
    assert run(lead_snap(state=st), ev("reply")) == [Classify()]


@pytest.mark.parametrize("st", list(S))
def test_unsubscribe_any_state(st):
    out = run(lead_snap(state=st), ev("unsubscribe"))
    if st in TERMINAL:
        assert out == [SuppressAllChannels("unsubscribe")]
    else:
        assert out == optout_actions("unsubscribe")


@pytest.mark.parametrize("verdict", ["human", "bot", "machine", None])
@pytest.mark.parametrize("st", list(S))
def test_unsubscribe_click_alone_does_nothing(st, verdict):
    # scanners pre-click every link: only the `unsubscribe` event kind opts out
    assert run(lead_snap(state=st), click("unsubscribe", verdict=verdict)) == []
    assert run(wa_lead(state=st, wa_window_open=True),
               click("unsubscribe", verdict=verdict)) == []


def test_sent_records_step_and_refreshes():
    assert run(lead_snap(), ev("sent", step=3)) == [SetStepSent(3), RefreshNextStep()]


def test_sent_on_whatsapp_and_wa_receipts_do_nothing():
    assert run(wa_lead(), ev("sent", channel="whatsapp", step=1)) == []
    assert run(wa_lead(), ev("wa_sent")) == []
    assert run(wa_lead(), ev("wa_delivered")) == []


# ---------------------------------------------------------------- WhatsApp rows


def test_wa_failed_not_on_whatsapp_with_email():
    assert run(both(active_channel="whatsapp"), failed(131026)) == [
        SwitchChannel("wa_undeliverable")]


def test_wa_failed_not_on_whatsapp_no_email():
    assert run(wa_lead(), failed(131026)) == [
        Suppress(PHONE, "phone", "wa_undeliverable"), SetState(S.CLOSED, "unreachable")]


def test_wa_failed_already_switched_closes():
    assert run(both(active_channel="whatsapp", channel_switched=True), failed("131026")) == [
        Suppress(PHONE, "phone", "wa_undeliverable"), SetState(S.CLOSED, "unreachable")]


def test_wa_marketing_limit():
    assert run(both(active_channel="whatsapp"), failed(131049)) == [
        SwitchChannel("wa_marketing_limit")]
    assert run(wa_lead(), failed("131049")) == [SetState(S.NURTURE, "wa_marketing_limit")]


def test_wa_failed_131047_logged_only():
    assert run(wa_lead(), failed(131047)) == []


@pytest.mark.parametrize("code", [500, "abc", None])
def test_wa_failed_other_code_alerts(code):
    assert run(wa_lead(), failed(code)) == [Alert("wa_send_failed")]


@pytest.mark.parametrize("st", [S.ENGAGED, S.QUALIFYING, S.BOOKED, S.NURTURE, S.PAUSED])
def test_wa_failed_outside_in_sequence_ignored(st):
    assert run(wa_lead(state=st), failed(131026)) == []


def test_wa_failed_ignored_when_email_active():
    for code in (131026, 131049, 131047, 500):
        assert run(both(active_channel="email", channel_switched=True), failed(code)) == []
        assert run(lead_snap(active_channel=None, phone=PHONE, has_wa_optin=True),
                   failed(code)) == []
    # 131050 stays an opt-out regardless of the active channel
    assert run(both(active_channel="email"), failed(131050)) == optout_actions(
        "wa_user_stopped_marketing")


def test_wa_user_stopped_marketing():
    reason = "wa_user_stopped_marketing"
    assert run(wa_lead(), failed(131050)) == optout_actions(reason)
    assert run(wa_lead(state=S.ENGAGED), failed("131050")) == optout_actions(reason)
    assert run(wa_lead(state=S.CLOSED), failed(131050)) == [SuppressAllChannels(reason)]


def test_wa_button_book_demo():
    assert run(wa_lead(wa_window_open=True), button("BOOK_DEMO")) == [
        STOP, SetState(S.QUALIFYING, "book_demo"), WaSendText("booking_link"), PauseColleagues()]


def test_wa_button_book_demo_from_nurture():
    assert run(wa_lead(state=S.NURTURE), button("BOOK_DEMO")) == [
        STOP, SetState(S.QUALIFYING, "book_demo"), WaSendText("booking_link"), PauseColleagues()]


def test_wa_button_book_demo_from_paused_two_hops():
    assert run(wa_lead(state=S.PAUSED), button("BOOK_DEMO")) == [
        STOP, SetState(S.ENGAGED, "book_demo"), SetState(S.QUALIFYING, "book_demo"),
        WaSendText("booking_link"), PauseColleagues()]


def test_wa_button_book_demo_from_engaged():
    assert run(wa_lead(state=S.ENGAGED), button("BOOK_DEMO")) == [
        SetState(S.QUALIFYING, "book_demo"), WaSendText("booking_link")]


@pytest.mark.parametrize("st", [S.QUALIFYING, S.BOOKED])
def test_wa_button_book_demo_already_qualifying(st):
    assert run(wa_lead(state=st), button("BOOK_DEMO")) == [WaSendText("booking_link")]


def test_wa_button_ask_question():
    assert run(wa_lead(), button("ASK_QUESTION")) == [
        STOP, SetState(S.ENGAGED, "ask_question"), WaSendText("ask_away"), PauseColleagues()]


@pytest.mark.parametrize("st", [S.PAUSED, S.NURTURE])
def test_wa_button_ask_question_from_paused_nurture(st):
    assert run(wa_lead(state=st), button("ASK_QUESTION")) == [
        STOP, SetState(S.ENGAGED, "ask_question"), WaSendText("ask_away"), PauseColleagues()]


@pytest.mark.parametrize("st", [S.ENGAGED, S.QUALIFYING, S.BOOKED])
def test_wa_button_ask_question_in_conversation(st):
    assert run(wa_lead(state=st), button("ASK_QUESTION")) == [WaSendText("ask_away")]


def test_wa_button_unknown_payload():
    assert run(wa_lead(), button("SOMETHING")) == [Alert("unsure")]


@pytest.mark.parametrize("t", ["STOP ", "stop", " Unsubscribe", "OPT OUT", "optout", "Remove Me"])
def test_wa_optout_keyword(t):
    out = run(wa_lead(), text(t))
    assert out == optout_actions("wa_optout")
    assert Classify() not in out


def test_wa_optout_keyword_must_be_whole_message():
    out = run(wa_lead(), text("please stop sending"))
    assert SuppressAllChannels("wa_optout") not in out and Classify() in out


def test_wa_text_first_time():
    assert run(wa_lead(), text("hi, tell me more")) == [
        STOP, SetState(S.ENGAGED, "wa_text"), Classify(), PauseColleagues()]


@pytest.mark.parametrize("st", [S.PAUSED, S.NURTURE])
def test_wa_text_from_paused_nurture_engages(st):
    assert run(wa_lead(state=st), text("hello")) == [
        STOP, SetState(S.ENGAGED, "wa_text"), Classify(), PauseColleagues()]


@pytest.mark.parametrize("st", [S.ENGAGED, S.QUALIFYING, S.BOOKED])
def test_wa_text_in_bot_chat(st):
    assert run(wa_lead(state=st), text("how much?")) == [BotReply()]


@pytest.mark.parametrize("st", [S.ENGAGED, S.QUALIFYING, S.BOOKED])
def test_wa_text_human_owns_chat(st):
    assert run(wa_lead(state=st, wa_human_owner="sam"), text("how much?")) == []


def test_wa_read_hint():
    assert run(wa_lead(), ev("wa_read")) == [AddScore(1), SetFlag("wa_read_scored", True)]
    assert run(wa_lead(flags={"wa_read_scored": True}), ev("wa_read")) == []


# ---------------------------------------------------------------- cross-channel / terminal


def test_email_reply_cancels_wa_reminder():
    out = flatten(run(both(active_channel="whatsapp", channel_switched=True), ev("reply")))
    assert WaCancelReminder() in out and ToolStop() in out


def test_wa_tap_stops_email():
    lead = both(active_channel="email", channel_switched=True)
    out = flatten(run(lead, button("BOOK_DEMO")))
    assert ToolStop() in out and WaCancelReminder() in out


@pytest.mark.parametrize("st", sorted(TERMINAL, key=lambda s: s.value))
@pytest.mark.parametrize("event", [
    click("pricing"), button("BOOK_DEMO"), button("ASK_QUESTION"), ev("reply"),
    text("hello"), ev("open", step=1), ev("wa_read"),
    ev("no_show"), ev("meeting_ended"), failed(131026), ev("sent", step=2),
], ids=lambda e: e.kind)
def test_terminal_state_no_actions(st, event):
    want = [Alert("handoff")] if (st == S.HANDED_OFF and event.kind == "no_show") else []  # Task 11 ruling
    if event.kind == "sent":
        want = [ToolStop(), Alert("tool_stop_failed")]  # final review A1: the tool should not be sending
    assert run(both(state=st, wa_window_open=True), event) == want


@pytest.mark.parametrize("st", sorted(TERMINAL, key=lambda s: s.value))
def test_terminal_booking_alerts_sales(st):
    out = run(both(state=st), ev("booking_created"))
    assert out == [Alert("booked")]
    assert out[0].text.endswith("booking from lead in terminal state")


def test_terminal_still_records_optout():
    assert run(wa_lead(state=S.CLOSED), text("stop")) == [SuppressAllChannels("wa_optout")]


def test_booked_event():
    assert run(lead_snap(), ev("booking_created")) == [
        STOP, SetState(S.BOOKED, "booking_created"), Alert("booked"), PauseColleagues()]


@pytest.mark.parametrize("st", [S.IN_SEQUENCE, S.ENGAGED, S.NURTURE, S.QUALIFYING])
def test_booking_created_states(st):
    assert run(lead_snap(state=st), ev("booking_created")) == [
        STOP, SetState(S.BOOKED, "booking_created"), Alert("booked"), PauseColleagues()]


def test_booking_created_from_paused_two_hops():
    assert run(lead_snap(state=S.PAUSED), ev("booking_created")) == [
        STOP, SetState(S.ENGAGED, "booking_created"), SetState(S.BOOKED, "booking_created"),
        Alert("booked"), PauseColleagues()]


def test_booking_created_already_booked():
    assert run(lead_snap(state=S.BOOKED), ev("booking_created")) == []


def test_no_show_first_email():
    assert run(lead_snap(state=S.BOOKED), ev("no_show")) == [
        SetFlag("no_show_count", 1), SetState(S.QUALIFYING, "no_show"), Alert("no_show"),
        SendReply("reschedule")]


def test_no_show_first_whatsapp_window_open():
    assert run(wa_lead(state=S.BOOKED, wa_window_open=True), ev("no_show")) == [
        SetFlag("no_show_count", 1), SetState(S.QUALIFYING, "no_show"), Alert("no_show"),
        WaSendText("reschedule")]


def test_no_show_first_whatsapp_window_closed_needs_human():
    out = run(wa_lead(state=S.BOOKED), ev("no_show"))
    assert out == [SetFlag("no_show_count", 1), SetState(S.QUALIFYING, "no_show"),
                   Alert("no_show"), Alert("wa_human_needed")]


def test_no_show_second_goes_nurture():
    assert run(lead_snap(state=S.BOOKED, flags={"no_show_count": 1}), ev("no_show")) == [
        SetFlag("no_show_count", 2), SetState(S.NURTURE, "no_show_x2"), Alert("no_show")]


def test_meeting_ended_from_booked_hands_off():
    assert run(lead_snap(state=S.BOOKED), ev("meeting_ended")) == [
        SetState(S.HANDED_OFF, "attended"), HandOff()]


@pytest.mark.parametrize("st", [S.IN_SEQUENCE, S.ENGAGED, S.QUALIFYING, S.NURTURE, S.PAUSED])
def test_meeting_ended_other_states_nothing(st):
    assert run(lead_snap(state=st), ev("meeting_ended")) == []


def test_unknown_event_kind_nothing():
    assert run(lead_snap(), ev("mystery")) == []


# ---------------------------------------------------------------- invariants


_ALL_EVENTS = [
    ev("sent", step=1), ev("open", step=1), click("homepage"), click("case_study"),
    click("pricing"), click("booking"), click("unsubscribe"), ev("reply"), ev("bounce_hard"),
    ev("bounce_soft"), ev("unsubscribe"), ev("wa_sent"), ev("wa_delivered"), ev("wa_read"),
    failed(131026), failed(131049), failed(131050), failed(131047), failed(1),
    button("BOOK_DEMO"), button("ASK_QUESTION"), button("X"), text("stop"), text("hello"),
    ev("booking_created"), ev("no_show"), ev("meeting_ended"),
    ev("booking_created", data={"uid": "u1", "end": "E"}), ev("booking_rescheduled", data={"uid": "u2", "end": "E"}),
    ev("booking_cancelled", data={"uid": "u1"}), ev("booking_cancelled", data={"uid": "other"}),
    ev("form_submitted", data={"fit": "low"}), ev("form_submitted", data={"fit": "high"}),
    ev("form_submitted", data={"fit": "medium"}),
]
_LEADS = [
    lead_snap(), both(), wa_lead(), wa_lead(wa_window_open=True),
    both(active_channel="whatsapp", channel_switched=True),
    lead_snap(flags={"soft_bounce_count": 2, "no_show_count": 1, "human_opens": 2}),
    wa_lead(wa_human_owner="sam"),
    lead_snap(flags={"booking_uid": "u1"}),
]


@pytest.mark.parametrize("st", list(S))
def test_every_set_state_chain_is_allowed_and_never_self(st):
    for base in _LEADS:
        lead = replace(base, state=st)
        for event in _ALL_EVENTS:
            cur = st
            for a in run(lead, event):
                if isinstance(a, SetState):
                    assert a.to != cur, (st, event.kind)
                    assert a.to in ALLOWED[cur], (st, event.kind, a.to)
                    cur = a.to


def test_never_two_hot_alerts_and_deterministic():
    for base in _LEADS:
        for score in (0, 5, 10, 14, 15):
            lead = replace(base, score=score)
            for event in _ALL_EVENTS:
                out = run(lead, event)
                assert out == run(lead, event)
                assert sum(1 for a in out if isinstance(a, Alert) and a.kind == "hot_lead") <= 1


def test_add_score_only_positive():
    for base in _LEADS:
        for event in _ALL_EVENTS:
            for a in run(replace(base, flags={"human_opens": 5, "wa_read_scored": True}), event):
                if isinstance(a, AddScore):
                    assert a.points > 0


# ---------------------------------------------------------------- Task 12: worker-detected conditions

def test_booking_click_not_booked_email_sends_slots():
    out = decide(lead_snap(), ev("booking_click_not_booked", channel="system"), NOW)
    assert out == [SendReply("booking_slots")]


def test_booking_click_not_booked_whatsapp_window_open_sends_text():
    out = decide(wa_lead(wa_window_open=True), ev("booking_click_not_booked", channel="system"), NOW)
    assert out == [WaSendText("booking_slots")]


def test_booking_click_not_booked_whatsapp_window_closed_alerts():
    out = decide(wa_lead(), ev("booking_click_not_booked", channel="system"), NOW)
    assert [type(a) for a in out] == [Alert] and out[0].kind == "wa_human_needed"


@pytest.mark.parametrize("st", [S.BOOKED, S.PAUSED, S.HANDED_OFF, S.SUPPRESSED])
def test_booking_click_not_booked_quiet_states(st):
    assert decide(lead_snap(state=st), ev("booking_click_not_booked", channel="system"), NOW) == []


def test_sent_maps_the_campaign_step_through_the_resume_offset():
    lead = lead_snap(step_sent=2, flags={"step_offset": 2})
    assert decide(lead, ev("sent", step=1), NOW) == [SetStepSent(3), RefreshNextStep()]


def test_last_real_step_sent_after_resume_stops_the_tool():
    lead = lead_snap(step_sent=3, flags={"step_offset": 2})
    assert decide(lead, ev("sent", step=2), NOW) == [SetStepSent(4), RefreshNextStep(), ToolStop()]


def test_sent_beyond_the_last_real_step_is_ignored_but_stops_the_tool():
    lead = lead_snap(step_sent=4, flags={"step_offset": 2})
    assert decide(lead, ev("sent", step=3), NOW) == [ToolStop()]


# ---------------------------------------------------------------- task 18: clicks alone need a check

def checked(**kw) -> LeadSnapshot:
    return lead_snap(clicks_need_check=True, **kw)


def test_click_check_pricing_click_alerts_check_not_sales():
    out = run(checked(), click("pricing"))
    assert out == [AddScore(10), Alert("click_check"), SetFlag("click_check_alerted", True),
                   SetVariant("booking_slots"), SendReply("booking_slots"), SetFlag("booking_slots_at", NOW_ISO)]
    assert "hot_lead" not in [a.kind for a in out if isinstance(a, Alert)]


def test_click_check_score_crossing_alerts_check_once():
    assert run(checked(score=13), click("homepage")) == [
        AddScore(2), Alert("click_check"), SetFlag("click_check_alerted", True)]
    assert run(checked(score=15, flags={"click_check_alerted": True}), click("pricing"))[:1] == [AddScore(10)]
    assert not [a for a in run(checked(score=15, flags={"click_check_alerted": True}), click("pricing"))
                if isinstance(a, Alert)]


def test_click_check_below_hot_no_alert():
    assert run(checked(), click("case_study")) == [AddScore(5), SetVariant("case_study")]


def test_click_check_not_applied_once_lead_replied():
    """ENGAGED / QUALIFYING / BOOKED: a person already replied, so a click is a real hot signal."""
    for state in (S.ENGAGED, S.QUALIFYING, S.BOOKED):
        out = decide(checked(state=state), click("pricing"), NOW)
        assert "hot_lead" in [a.kind for a in out if isinstance(a, Alert)], state
        assert "click_check" not in [a.kind for a in out if isinstance(a, Alert)], state


def test_click_check_off_keeps_hot_lead_alert():
    out = run(lead_snap(clicks_need_check=False), click("pricing"))
    assert Alert("hot_lead") in out and Alert("click_check") not in out
