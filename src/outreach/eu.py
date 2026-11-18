"""EU / EEA detection. Cold email to EU leads needs approval first (GDPR), so intake
marks these leads and email stays on hold until EU_EMAIL_APPROVED is switched on.

Conservative: any EU signal (country, phone region, email country domain) counts."""
from __future__ import annotations

from typing import Optional

import phonenumbers

# EU-27 plus the EEA (Iceland, Liechtenstein, Norway): same rules apply.
EU_EEA = frozenset({
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE",
    "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
    "IS", "LI", "NO",
})
_NAMES = {
    "austria": "AT", "belgium": "BE", "bulgaria": "BG", "croatia": "HR", "cyprus": "CY",
    "czechia": "CZ", "czech republic": "CZ", "denmark": "DK", "estonia": "EE", "finland": "FI",
    "france": "FR", "germany": "DE", "greece": "GR", "hungary": "HU", "ireland": "IE",
    "italy": "IT", "latvia": "LV", "lithuania": "LT", "luxembourg": "LU", "malta": "MT",
    "netherlands": "NL", "the netherlands": "NL", "poland": "PL", "portugal": "PT",
    "romania": "RO", "slovakia": "SK", "slovenia": "SI", "spain": "ES", "sweden": "SE",
    "iceland": "IS", "liechtenstein": "LI", "norway": "NO",
}
# other countries leads commonly come from (names only; any ISO 3166 two-letter code also works)
_OTHER_NAMES = {
    "india": "IN", "bharat": "IN", "united states": "US", "united states of america": "US",
    "usa": "US", "us": "US", "america": "US", "united kingdom": "GB", "uk": "GB",
    "great britain": "GB", "england": "GB", "canada": "CA", "switzerland": "CH",
    "united arab emirates": "AE", "uae": "AE", "singapore": "SG", "australia": "AU",
    "saudi arabia": "SA", "qatar": "QA", "oman": "OM", "kuwait": "KW", "bahrain": "BH",
    "nepal": "NP", "bangladesh": "BD", "sri lanka": "LK", "south africa": "ZA",
}
_ALIASES = {"UK": "GB"}  # common but not ISO

# email country domains; Greece uses .gr, plus the shared .eu domain
_TLDS = {code.lower() for code in EU_EEA} | {"eu"}


def country_code(country: Optional[str]) -> Optional[str]:
    """ISO 3166 two-letter code from a code ("in", "UK") or a common English name ("India"), else None."""
    if not country:
        return None
    c = country.strip()
    name = c.lower()
    if name in _NAMES or name in _OTHER_NAMES:
        return _NAMES.get(name) or _OTHER_NAMES[name]
    if len(c) == 2 and c.isalpha():
        code = _ALIASES.get(c.upper(), c.upper())
        return code if code in phonenumbers.SUPPORTED_REGIONS else None
    return None


def is_eu(country: Optional[str], email: Optional[str], phone_e164: Optional[str]) -> bool:
    if country_code(country) in EU_EEA:
        return True
    if email and "@" in email:
        tld = email.rsplit(".", 1)[-1].lower()
        if tld in _TLDS:
            return True
    if phone_e164:
        try:
            region = phonenumbers.region_code_for_number(phonenumbers.parse(phone_e164, None))
        except phonenumbers.NumberParseException:
            region = None
        if region in EU_EEA:
            return True
    return False
