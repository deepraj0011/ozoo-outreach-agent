from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from outreach.score import apply_points, band, decay
from tests.conftest import NOW, make_lead


def ev(kind, tag=None, verdict="human", at=NOW):
    return SimpleNamespace(kind=kind, tag=tag, verdict=verdict, occurred_at=at)


def test_opens_score_nothing(session):
    lead = make_lead(session)
    assert [apply_points(lead, ev("open")) for _ in range(5)] == [0, 0, 0, 0, 0]
    assert lead.score == 0
    assert lead.variant_flags["human_opens"] == 5


@pytest.mark.parametrize("tag,pts", [("homepage", 2), ("case_study", 5), ("product", 5),
                                     ("pricing", 10), ("booking", 10), ("unsubscribe", 0)])
def test_tag_points(session, tag, pts):
    lead = make_lead(session)
    assert apply_points(lead, ev("click", tag)) == pts
    assert lead.score == pts


def test_wa_read_counts_once(session):
    lead = make_lead(session)
    assert apply_points(lead, ev("wa_read")) == 1
    assert apply_points(lead, ev("wa_read")) == 0
    assert lead.score == 1


def test_non_human_scores_nothing(session):
    lead = make_lead(session)
    for v in ("bot", "machine", None):
        assert apply_points(lead, ev("click", "pricing", verdict=v)) == 0
    assert lead.score == 0 and lead.last_human_event_at is None


def test_other_kinds_zero(session):
    lead = make_lead(session)
    assert apply_points(lead, ev("reply")) == 0
    assert lead.last_human_event_at is None


def test_sets_last_human_event_at(session):
    lead = make_lead(session)
    t = NOW + timedelta(days=1)
    apply_points(lead, ev("click", "homepage", at=t))
    assert lead.last_human_event_at == t


def test_decay_halves_after_30_days(session):
    lead = make_lead(session, score=15, last_human_event_at=NOW)
    assert decay(lead, NOW + timedelta(days=30)) is True
    assert lead.score == 7
    assert "last_decay_at" in lead.variant_flags
    # not again immediately
    assert decay(lead, NOW + timedelta(days=31)) is False
    assert lead.score == 7
    # again after another 30 days
    assert decay(lead, NOW + timedelta(days=60)) is True
    assert lead.score == 3


def test_no_decay_at_29_days(session):
    lead = make_lead(session, score=15, last_human_event_at=NOW)
    assert decay(lead, NOW + timedelta(days=29)) is False
    assert lead.score == 15


def test_decay_anchors_on_created_at_and_skips_zero(session):
    lead = make_lead(session, score=8)
    assert decay(lead, NOW + timedelta(days=30)) is True
    z = make_lead(session, score=0)
    assert decay(z, NOW + timedelta(days=90)) is False


def test_bands():
    assert band(4) == "cold" and band(0) == "cold"
    assert band(5) == "warm" and band(14) == "warm"
    assert band(15) == "hot"
