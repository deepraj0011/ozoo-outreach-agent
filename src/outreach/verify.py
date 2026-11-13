from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# httpx logs full request URLs (query holds email + api key) at INFO; silence it.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

ZEROBOUNCE_URL = "https://api.zerobounce.net/v2/validate"
_MAP = {
    "valid": "valid",
    "invalid": "invalid",
    "catch-all": "catch_all",
    "spamtrap": "invalid",
    "abuse": "invalid",
    "do_not_mail": "invalid",
}


# ZeroBounce could not answer (unreachable, HTTP error, error body, no credits). Distinct from
# ZeroBounce's own "unknown" verdict: the address was never checked, so it is never sendable
# (intake holds it, the worker's retry_verification tries again).
VERIFY_ERROR = "verify_error"


class EmailVerifier:
    """ZeroBounce email verification. A failure to verify maps to VERIFY_ERROR."""

    def __init__(self, api_key: str, client: Optional[httpx.Client] = None) -> None:
        self._api_key = api_key
        self._client = client

    def check(self, email: str) -> str:
        try:
            params = {"api_key": self._api_key, "email": email, "ip_address": ""}
            if self._client is not None:
                resp = self._client.get(ZEROBOUNCE_URL, params=params, timeout=10.0)
            else:
                resp = httpx.get(ZEROBOUNCE_URL, params=params, timeout=10.0)
            if resp.status_code != 200:
                logger.warning("email verification returned HTTP %s", resp.status_code)
                return VERIFY_ERROR
            body = resp.json()
            if not isinstance(body, dict) or body.get("error") or not body.get("status"):
                # e.g. {"error": "Invalid API key or your account ran out of credits"}
                logger.warning("email verification returned an error body")
                return VERIFY_ERROR
            status = str(body.get("status", "")).lower()
            return _MAP.get(status, "unknown")
        except Exception as exc:  # noqa: BLE001 - never leak address/key; fail soft
            logger.warning("email verification failed: %s", type(exc).__name__)
            return VERIFY_ERROR


class FakeVerifier:
    """Local fake (VERIFY_MODE=fake): no network. Every address is valid, except one containing
    "bounce" or "invalid" (lets you test a rejected / undeliverable address)."""

    def check(self, email: str) -> str:
        low = (email or "").lower()
        return "invalid" if "bounce" in low or "invalid" in low else "valid"


def make_verifier(settings) -> "EmailVerifier | FakeVerifier":
    if getattr(settings, "verify_mode", "real") == "fake":
        return FakeVerifier()
    return EmailVerifier(settings.zerobounce_api_key)
