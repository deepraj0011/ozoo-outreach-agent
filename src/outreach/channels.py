from __future__ import annotations

from typing import Optional

import phonenumbers

from .eu import country_code

EMAIL_OK_STATUSES = frozenset({"valid", "catch_all", "unknown"})


def normalize_phone(raw: str, country: Optional[str], fallback_region: Optional[str] = None) -> Optional[str]:
    """Return E.164 or None if the number cannot be parsed / is invalid.

    A number with "+" (or "00") carries its own country. Otherwise it is read in the lead's country
    (an ISO code or a common name, see eu.country_code), else in fallback_region (settings
    DEFAULT_PHONE_REGION). With neither, a number without its country code is rejected."""
    region = country_code(country) or (fallback_region.upper() if fallback_region else None)
    try:
        num = phonenumbers.parse(raw, region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(num):
        return None
    return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)


def email_usable(lead) -> bool:
    """Cold B2B email needs no prior opt-in (only an unsubscribe in every email);
    consent_email is recorded but not required. Held leads (EU before approval) are not usable."""
    return bool(
        lead.email and lead.verify_status in EMAIL_OK_STATUSES
        and not getattr(lead, "email_hold", None)
    )


def whatsapp_usable(lead) -> bool:
    return bool(lead.phone and lead.consent_whatsapp)


def choose_channel(lead, primary_when_both: str) -> Optional[str]:
    e, w = email_usable(lead), whatsapp_usable(lead)
    if e and w:
        return "email" if primary_when_both == "email" else "whatsapp"
    if e:
        return "email"
    if w:
        return "whatsapp"
    return None


def fallback_channel(lead) -> Optional[str]:
    """The other usable channel, if the lead has not switched yet."""
    if lead.channel_switched:
        return None
    active = getattr(lead, "active_channel", None)
    if active == "whatsapp" and email_usable(lead):
        return "email"
    if active == "email" and whatsapp_usable(lead):
        return "whatsapp"
    return None
