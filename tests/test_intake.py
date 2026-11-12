from __future__ import annotations

import httpx
import respx

from tests.conftest import NOW, make_lead
from outreach.intake import intake_lead
from outreach.models import Event, Lead, Suppression
from outreach.states import LeadState
from outreach.verify import EmailVerifier


class FakeVerifier:
    def __init__(self, statuses=None):
        self.statuses = statuses or {}
        self.calls = []

    def check(self, email):
        self.calls.append(email)
        return self.statuses.get(email, "valid")


def payload(**kw):
    d = dict(name="Asha", company="Acme", role="CTO", source="form",
             email="asha@acme.com", consent_email=True, country="IN")
    d.update(kw)
    return d


def run(session, p, v=None, **kw):
    return intake_lead(session, p, v or FakeVerifier(), NOW, **kw)


def test_accepted_email(session):
    r = run(session, payload())
    assert r.outcome == "accepted" and r.channel == "email" and r.lead_id
    lead = session.get(Lead, r.lead_id)
    assert lead.state == "in_sequence"
    assert lead.active_channel == "email" and lead.channel_switched is False
    assert lead.score == 0 and lead.step_sent == 0 and lead.created_at == NOW
    assert lead.domain == "acme.com" and lead.verify_status == "valid"
    assert session.query(Event).filter_by(lead_id=lead.id, kind="state_change").count() == 1


def test_missing_required_field_rejected(session):
    for f in ("name", "company", "role", "source"):
        p = payload()
        del p[f]
        r = run(session, p)
        assert r.outcome == "rejected" and f in r.reason and r.lead_id is None
    p = payload()
    del p["email"]
    assert run(session, p).outcome == "rejected"
    assert session.query(Lead).count() == 0


def test_overlong_field_rejected(session):
    r = run(session, payload(company="x" * 201))
    assert r.outcome == "rejected" and "company" in r.reason


def test_invalid_email_no_phone_rejected(session):
    r = run(session, payload(), FakeVerifier({"asha@acme.com": "invalid"}))
    assert r.outcome == "rejected" and r.lead_id is None


def test_invalid_email_with_wa_phone_goes_whatsapp(session):
    p = payload(phone="+91 98765 43210", consent_whatsapp=True)
    r = run(session, p, FakeVerifier({"asha@acme.com": "invalid"}))
    assert r.outcome == "accepted" and r.channel == "whatsapp"
    lead = session.get(Lead, r.lead_id)
    assert lead.phone == "+919876543210"
    assert lead.email == "asha@acme.com" and lead.verify_status == "invalid"


def test_catch_all_accepted(session):
    r = run(session, payload(), FakeVerifier({"asha@acme.com": "catch_all"}))
    assert r.outcome == "accepted" and r.channel == "email"
    assert session.get(Lead, r.lead_id).verify_status == "catch_all"


def test_duplicate_email_skipped(session):
    first = run(session, payload())
    r = run(session, payload(email="ASHA@Acme.com"))
    assert r.outcome == "skipped_duplicate" and r.lead_id == first.lead_id


def test_duplicate_phone_skipped(session):
    first = run(session, payload(email=None, phone="+91 98765 43210", consent_whatsapp=True))
    assert first.outcome == "accepted"
    r = run(session, payload(email=None, phone="098765 43210", consent_whatsapp=True))
    assert r.outcome == "skipped_duplicate" and r.lead_id == first.lead_id


def test_suppressed_email_skipped(session):
    session.add(Suppression(value="asha@acme.com", kind="email", created_at=NOW))
    session.flush()
    r = run(session, payload())
    assert r.outcome == "skipped_suppressed" and r.lead_id is None


def test_suppressed_phone_skipped(session):
    session.add(Suppression(value="+919876543210", kind="phone", created_at=NOW))
    session.flush()
    r = run(session, payload(phone="098765 43210", consent_whatsapp=True))
    assert r.outcome == "skipped_suppressed"


def test_colleague_engaged_held(session):
    for st in (LeadState.ENGAGED, LeadState.QUALIFYING, LeadState.BOOKED):
        make_lead(session, domain="held.com", state=st)
    r = run(session, payload(email="new@held.com"))
    assert r.outcome == "held_account" and r.channel == "email"
    lead = session.get(Lead, r.lead_id)
    assert lead.state == "paused" and lead.pause_reason == "account"
    assert lead.active_channel == "email"
    ev = session.query(Event).filter_by(lead_id=lead.id, kind="state_change").one()
    assert ev.data["to"] == "paused"


def test_colleague_in_sequence_does_not_hold(session):
    make_lead(session, domain="calm.com", state=LeadState.IN_SEQUENCE)
    r = run(session, payload(email="new@calm.com"))
    assert r.outcome == "accepted"


def test_free_mail_domain_not_used_for_account_rule(session):
    make_lead(session, domain="gmail.com", state=LeadState.ENGAGED)
    r = run(session, payload(email="someone@gmail.com"))
    assert r.outcome == "accepted"


def test_no_usable_channel_rejected(session):
    r = run(session, payload(email=None, phone="+919876543210", consent_whatsapp=False))
    assert r.outcome == "rejected" and r.lead_id is None
    r = run(session, payload(consent_email=False))
    assert r.outcome == "rejected"


def test_primary_when_both_param(session):
    p = payload(phone="+919876543210", consent_whatsapp=True)
    assert run(session, p, primary_when_both="email").channel == "email"


def test_zerobounce_error_returns_unknown():
    with respx.mock:
        respx.get("https://api.zerobounce.net/v2/validate").mock(return_value=httpx.Response(500))
        assert EmailVerifier("k").check("a@b.com") == "unknown"


def test_zerobounce_exception_returns_unknown():
    with respx.mock:
        respx.get("https://api.zerobounce.net/v2/validate").mock(side_effect=httpx.ConnectError("x"))
        assert EmailVerifier("k").check("a@b.com") == "unknown"


def test_zerobounce_status_mapping():
    with respx.mock:
        route = respx.get("https://api.zerobounce.net/v2/validate")
        for zb, want in [("valid", "valid"), ("invalid", "invalid"), ("catch-all", "catch_all"),
                         ("unknown", "unknown"), ("spamtrap", "invalid"), ("abuse", "invalid"),
                         ("do_not_mail", "invalid"), ("weird", "unknown")]:
            route.mock(return_value=httpx.Response(200, json={"status": zb}))
            assert EmailVerifier("k").check("a@b.com") == want


def test_spamtrap_maps_invalid():
    with respx.mock:
        respx.get("https://api.zerobounce.net/v2/validate").mock(
            return_value=httpx.Response(200, json={"status": "do_not_mail", "sub_status": "spamtrap"}))
        assert EmailVerifier("k").check("a@b.com") == "invalid"
        respx.get("https://api.zerobounce.net/v2/validate").mock(
            return_value=httpx.Response(200, json={"status": "spamtrap"}))
        assert EmailVerifier("k").check("a@b.com") == "invalid"


def test_unique_race_maps_to_duplicate(session, monkeypatch):
    from outreach import intake as intake_mod

    make_lead(session, email="asha@acme.com")
    monkeypatch.setattr(intake_mod, "_find_existing", lambda *a, **k: None)
    r = run(session, payload())
    assert r.outcome == "skipped_duplicate" and r.lead_id is None
    assert session.query(Lead).count() == 1
