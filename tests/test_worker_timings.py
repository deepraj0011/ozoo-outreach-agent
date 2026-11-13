"""Worker delays come from settings (Task 13): defaults unchanged, overrides / TIME_SCALE honoured."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from outreach.models import Event

from .conftest import NOW
from .fakes import make_settings
from .test_worker import (  # noqa: F401 - env is a fixture
    S, add, click, env, get, new_lead, run, templates_sent, tool_names, wa_started,
)

FAST = make_settings(click_settle_s=5, booking_nudge_after_s=60, wa_reminder_after_s=120,
                     wa_no_response_after_s=120, end_of_sequence_wait_s=300)


def test_reminder_and_no_response_use_settings(env):
    lid = wa_started(env, NOW)
    run(env, NOW + timedelta(seconds=119), settings=FAST)
    assert templates_sent(env) == []
    run(env, NOW + timedelta(seconds=120), settings=FAST)
    assert templates_sent(env) == ["ozoo_reminder"]
    run(env, NOW + timedelta(seconds=240), settings=FAST)
    assert get(env, lid).state == S.NURTURE.value


def test_click_settle_and_booking_nudge_use_settings(env):
    lid = new_lead(env, verify_status="valid", active_channel="email", tool_lead_id="t-1", step_sent=1,
                   last_sent_at=NOW - timedelta(days=1))
    add(env, click(lid, "booking", NOW))
    run(env, NOW + timedelta(seconds=6), settings=FAST)
    with env.factory() as s:
        assert s.scalars(select(Event.processed).where(Event.kind == "click")).one() is True
    run(env, NOW + timedelta(seconds=61), settings=FAST)
    assert tool_names(env).count("reply") == 1


def test_end_of_sequence_uses_settings(env):
    lid = new_lead(env, verify_status="valid", active_channel="email", tool_lead_id="t-1", step_sent=4,
                   last_sent_at=NOW)
    run(env, NOW + timedelta(seconds=299), settings=FAST)
    assert get(env, lid).state == S.IN_SEQUENCE.value
    run(env, NOW + timedelta(seconds=300), settings=FAST)
    assert get(env, lid).state == S.NURTURE.value


def test_settings_without_delay_fields_fall_back_to_defaults(env):
    from types import SimpleNamespace

    bare = SimpleNamespace(eu_email_approved=False, business_hours="Mon-Fri 09:00-18:00",
                           business_tz="Asia/Kolkata", worker_batch_size=50)
    wa_started(env, NOW)
    run(env, NOW + timedelta(hours=47), settings=bare)
    assert templates_sent(env) == []
    run(env, NOW + timedelta(hours=48), settings=bare)
    assert templates_sent(env) == ["ozoo_reminder"]
