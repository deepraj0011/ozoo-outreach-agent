"""How phone numbers are read at intake: country names, a default region, numbers without '+'."""
import pytest

from outreach.channels import normalize_phone
from outreach.demo_console import parse_leads
from outreach.eu import country_code
from outreach.intake import intake_lead
from outreach.models import Lead
from tests.conftest import NOW
from tests.fakes import make_settings


@pytest.mark.parametrize("raw,exp", [
    ("IN", "IN"), ("in", "IN"), (" India ", "IN"), ("Bharat", "IN"), ("USA", "US"),
    ("United States", "US"), ("UK", "GB"), ("United Kingdom", "GB"), ("UAE", "AE"),
    ("Germany", "DE"), ("Narnia", None), ("", None), (None, None),
])
def test_country_code(raw, exp):
    assert country_code(raw) == exp


@pytest.mark.parametrize("raw,country,fallback,exp", [
    ("9876543210", None, "IN", "+919876543210"),       # bare Indian mobile, no country: default region
    ("9876543210", "India", None, "+919876543210"),    # full country name
    ("098765 43210", None, "IN", "+919876543210"),     # trunk 0
    ("919876543210", None, "IN", "+919876543210"),     # country code without '+'
    ("+44 7911 123456", None, "IN", "+447911123456"),  # '+' always wins over the default
    ("2025550123", "US", "IN", "+12025550123"),        # the lead's country wins over the default
    ("9876543210", None, None, None),                  # no country, no default: cannot tell
    ("12345", None, "IN", None),                       # invalid
])
def test_normalize_phone(raw, country, fallback, exp):
    assert normalize_phone(raw, country, fallback) == exp


class _V:
    def check(self, email):
        return "valid"


def test_intake_uses_default_phone_region(session):
    p = dict(name="Asha", company="Acme", role="CTO", source="form", phone="98765 43210", consent_whatsapp=True)
    r = intake_lead(session, p, _V(), NOW, default_phone_region="IN")
    assert r.outcome == "accepted" and r.channel == "whatsapp"
    assert session.get(Lead, r.lead_id).phone == "+919876543210"


def test_default_phone_region_setting():
    assert make_settings().default_phone_region == "IN"
    assert make_settings(default_phone_region="us").default_phone_region == "US"
    assert make_settings(default_phone_region="").default_phone_region is None
    with pytest.raises(ValueError):
        make_settings(default_phone_region="XX")


def test_import_keeps_trunk_zero_numbers_local():
    data = b"Name,Company,Phone,WhatsApp opt-in\nA B,Acme,09876543210,yes\n"
    (row,) = parse_leads("leads.csv", data)["leads"]
    assert row["payload"]["phone"] == "09876543210"  # not "+0987...": intake reads it with the region
