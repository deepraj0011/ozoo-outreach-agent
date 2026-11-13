from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from outreach.score import band, decay, points_for
from tests.conftest import NOW, make_lead


def ev(kind, tag=None, verdict="human", at=NOW):
    return SimpleNamespace(kind=kind, tag=tag, verdict=verdict, occurred_at=at)


def test_opens_score_nothing():
    assert points_for("open", None, 5, False) == 0


@pytest.mark.parametrize("tag,pts", [("homepage", 2), ("case_study", 5), ("product", 5),
                                     ("pricing", 10), ("booking", 10), ("unsubscribe", 0)])
def test_tag_points(tag, pts):
    assert points_for("click", tag, 0, False) == pts


def test_wa_read_counts_once():
    assert points_for("wa_read", None, 0, False) == 1
    assert points_for("wa_read", None, 0, True) == 0


def test_other_kinds_zero():
    assert points_for("reply", None, 0, False) == 0


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
