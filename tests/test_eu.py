"""EU detection and the EU email hold at intake (feedback: EU email needs approval)."""
from __future__ import annotations

import pytest

from outreach.eu import is_eu
from outreach.models import Lead
from tests.test_intake import payload, run


@pytest.mark.parametrize("country,email,phone,exp", [
    ("DE", None, None, True),
    ("de", None, None, True),
    ("Germany", None, None, True),
    ("Norway", None, None, True),       # EEA
    ("IN", None, None, False),
    ("US", "a@acme.com", "+14155550100", False),
    (None, "a@firma.de", None, True),   # email country domain
    (None, "a@acme.eu", None, True),
    (None, "a@acme.com", "+33612345678", True),  # French mobile
    (None, None, None, False),
])
def test_is_eu(country, email, phone, exp):
    assert is_eu(country, email, phone) is exp


def test_eu_email_lead_held_not_rejected(session):
    r = run(session, payload(email="anna@firma.de", country="DE"))
    assert r.outcome == "held_eu" and r.channel == "email"
    lead = session.get(Lead, r.lead_id)
    assert lead.state == "paused" and lead.pause_reason == "eu_approval"
    assert lead.is_eu and lead.email_hold == "eu_approval"


def test_eu_email_lead_accepted_when_approved(session):
    r = run(session, payload(email="anna@firma.de", country="DE"), eu_email_approved=True)
    assert r.outcome == "accepted" and r.channel == "email"
    lead = session.get(Lead, r.lead_id)
    assert lead.is_eu and lead.email_hold is None


def test_eu_lead_with_whatsapp_optin_goes_whatsapp_email_stays_held(session):
    r = run(session, payload(email="anna@firma.de", country="DE",
                             phone="+4915112345678", consent_whatsapp=True),
            primary_when_both="email")
    assert r.outcome == "accepted" and r.channel == "whatsapp"
    assert session.get(Lead, r.lead_id).email_hold == "eu_approval"


def test_eu_invalid_email_still_rejected(session):
    from tests.test_intake import FakeVerifier
    r = run(session, payload(email="anna@firma.de", country="DE"),
            FakeVerifier({"anna@firma.de": "invalid"}))
    assert r.outcome == "rejected"


def test_non_eu_unaffected(session):
    r = run(session, payload())
    lead = session.get(Lead, r.lead_id)
    assert r.outcome == "accepted" and not lead.is_eu and lead.email_hold is None


def test_email_verified_even_without_consent(session):
    from tests.test_intake import FakeVerifier
    v = FakeVerifier()
    r = run(session, payload(consent_email=False), v)
    assert r.outcome == "accepted" and v.calls == ["asha@acme.com"]
