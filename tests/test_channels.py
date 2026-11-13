from __future__ import annotations

from types import SimpleNamespace

from outreach.channels import choose_channel, fallback_channel


def L(**kw):
    d = dict(email=None, verify_status=None, consent_email=False, phone=None,
             consent_whatsapp=False, channel_switched=False)
    d.update(kw)
    return SimpleNamespace(**d)


def test_email_only():
    lead = L(email="a@acme.com", verify_status="valid", consent_email=True)
    assert choose_channel(lead, "whatsapp") == "email"
    assert fallback_channel(lead) is None


def test_email_only_catch_all_and_unknown():
    for st in ("catch_all", "unknown"):
        lead = L(email="a@acme.com", verify_status=st, consent_email=True)
        assert choose_channel(lead, "whatsapp") == "email"


def test_whatsapp_only_unusable_email():
    lead = L(email="a@acme.com", verify_status="invalid", consent_email=True,
             phone="+919876543210", consent_whatsapp=True)
    assert choose_channel(lead, "email") == "whatsapp"
    assert fallback_channel(lead) is None


def test_both_default_whatsapp():
    lead = L(email="a@acme.com", verify_status="valid", consent_email=True,
             phone="+919876543210", consent_whatsapp=True)
    assert choose_channel(lead, "whatsapp") == "whatsapp"


def test_primary_when_both_email_flag():
    lead = L(email="a@acme.com", verify_status="valid", consent_email=True,
             phone="+919876543210", consent_whatsapp=True)
    assert choose_channel(lead, "email") == "email"


def test_phone_without_optin_plus_email_goes_email():
    lead = L(email="a@acme.com", verify_status="valid", consent_email=True,
             phone="+919876543210", consent_whatsapp=False)
    assert choose_channel(lead, "whatsapp") == "email"
    assert fallback_channel(lead) is None


def test_phone_without_optin_no_email_none():
    lead = L(phone="+919876543210", consent_whatsapp=False)
    assert choose_channel(lead, "whatsapp") is None


def test_email_without_consent_still_usable():
    # cold B2B email needs no opt-in; only WhatsApp does
    lead = L(email="a@acme.com", verify_status="valid", consent_email=False)
    assert choose_channel(lead, "whatsapp") == "email"


def test_email_on_hold_unusable():
    lead = L(email="a@acme.com", verify_status="valid", email_hold="eu_approval")
    assert choose_channel(lead, "whatsapp") is None


def test_fallback_is_other_channel_when_both():
    lead = L(email="a@acme.com", verify_status="valid", consent_email=True,
             phone="+919876543210", consent_whatsapp=True)
    lead.active_channel = "whatsapp"
    assert fallback_channel(lead) == "email"
    lead.active_channel = "email"
    assert fallback_channel(lead) == "whatsapp"


def test_fallback_none_after_switch():
    lead = L(email="a@acme.com", verify_status="valid", consent_email=True,
             phone="+919876543210", consent_whatsapp=True, channel_switched=True)
    lead.active_channel = "whatsapp"
    assert fallback_channel(lead) is None
