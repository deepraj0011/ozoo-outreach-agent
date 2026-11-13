"""Task 12: mailbox health (bounce rate per campaign over 7 days, from our own events)."""
from __future__ import annotations

import itertools
from datetime import timedelta

from sqlalchemy import select

from outreach.emailtool.base import ToolUnsupported
from outreach.emailtool.fake import FakeEmailTool
from outreach.health import check_mailboxes
from outreach.models import Alert as AlertRow
from outreach.models import Event

from .conftest import NOW
from .fakes import FakeAlerter

_ids = itertools.count(1)


def add_events(session, kind, n, campaign="c1", at=None):
    for _ in range(n):
        session.add(Event(lead_id=None, channel="email", kind=kind, provider_event_id=f"h{next(_ids)}",
                          occurred_at=at or NOW - timedelta(days=1), received_at=NOW, verdict="n/a",
                          processed=True, data={"campaign_id": campaign}))
    session.flush()


class PausingTool(FakeEmailTool):
    def pause_campaign(self, campaign_id: str) -> None:
        self._rec("pause_campaign", campaign_id)


def test_bounce_rate_over_2pct_pauses(session):
    add_events(session, "sent", 100)
    add_events(session, "bounce_hard", 2)
    add_events(session, "bounce_soft", 1)
    tool, alerter = PausingTool(), FakeAlerter()
    assert check_mailboxes(session, tool, alerter, NOW) == ["c1"]
    assert ("pause_campaign", "c1") in tool.calls
    assert alerter.calls == [("send", "mailbox_paused", None)]
    text = session.scalars(select(AlertRow.text)).one()
    assert "paused" in text and "3.0%" in text


def test_bounce_rate_over_2pct_without_pause_support_alerts(session):
    add_events(session, "sent", 60)
    add_events(session, "bounce_hard", 5)
    tool, alerter = FakeEmailTool(), FakeAlerter()
    assert check_mailboxes(session, tool, alerter, NOW) == ["c1"]
    assert alerter.calls == [("send", "mailbox_paused", None)]
    assert "manually" in session.scalars(select(AlertRow.text)).one()


def test_pause_unsupported_error_alerts(session):
    class Refusing(FakeEmailTool):
        def pause_campaign(self, campaign_id):
            raise ToolUnsupported("no")

    add_events(session, "sent", 60)
    add_events(session, "bounce_hard", 5)
    alerter = FakeAlerter()
    assert check_mailboxes(session, Refusing(), alerter, NOW) == ["c1"]
    assert "manually" in session.scalars(select(AlertRow.text)).one()


def test_under_50_sends_not_judged(session):
    add_events(session, "sent", 49)
    add_events(session, "bounce_hard", 10)
    tool, alerter = PausingTool(), FakeAlerter()
    assert check_mailboxes(session, tool, alerter, NOW) == []
    assert alerter.calls == [] and tool.calls == []


def test_exactly_2pct_not_flagged_and_campaigns_separate(session):
    add_events(session, "sent", 100, campaign="c1")
    add_events(session, "bounce_hard", 2, campaign="c1")
    add_events(session, "sent", 50, campaign="c2")
    add_events(session, "bounce_hard", 2, campaign="c2")
    assert check_mailboxes(session, PausingTool(), FakeAlerter(), NOW) == ["c2"]


def test_events_older_than_7_days_ignored(session):
    add_events(session, "sent", 100, at=NOW - timedelta(days=8))
    add_events(session, "bounce_hard", 10, at=NOW - timedelta(days=8))
    add_events(session, "sent", 10)
    assert check_mailboxes(session, PausingTool(), FakeAlerter(), NOW) == []
