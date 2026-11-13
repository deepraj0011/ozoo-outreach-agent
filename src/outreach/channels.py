from __future__ import annotations

from typing import Optional

import phonenumbers

EMAIL_OK_STATUSES = frozenset({"valid", "catch_all", "unknown"})


def normalize_phone(raw: str, default_region: Optional[str]) -> Optional[str]:
    """Return E.164 or None if the number cannot be parsed / is invalid."""
    try:
        num = phonenumbers.parse(raw, default_region.upper() if default_region else None)
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
