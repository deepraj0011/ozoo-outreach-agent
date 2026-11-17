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


class ReacherVerifier:
    """Reacher (open source, self-hosted: `docker run -p 8080:8080 reacherhq/backend`). It talks
    SMTP to the lead's mail server without sending anything, so it needs outbound port 25: run it
    on a server, not a home connection (most ISPs block 25; then every answer is "unknown", which
    is still sendable). A failure to answer maps to VERIFY_ERROR, like ZeroBounce."""

    TIMEOUT_S = 60.0  # SMTP checks can wait on greylisting

    def __init__(self, base_url: str, secret: str = "", client: Optional[httpx.Client] = None) -> None:
        self._url = base_url.rstrip("/") + "/v0/check_email"
        self._secret = secret
        self._client = client

    def check(self, email: str) -> str:
        try:
            headers = {"x-reacher-secret": self._secret} if self._secret else {}
            send = self._client.post if self._client is not None else httpx.post
            resp = send(self._url, json={"to_email": email}, headers=headers, timeout=self.TIMEOUT_S)
            if resp.status_code != 200:
                logger.warning("email verification (reacher) returned HTTP %s", resp.status_code)
                return VERIFY_ERROR
            body = resp.json()
            reach = str((body or {}).get("is_reachable", "")).lower() if isinstance(body, dict) else ""
            if not reach:
                logger.warning("email verification (reacher) returned an unexpected body")
                return VERIFY_ERROR
            if reach == "safe":
                return "valid"
            if reach == "invalid":
                return "invalid"
            if reach == "risky":
                misc, smtp = body.get("misc") or {}, body.get("smtp") or {}
                if misc.get("is_disposable"):
                    return "invalid"
                if smtp.get("is_catch_all"):
                    return "catch_all"
            return "unknown"  # risky (role account, full inbox) or Reacher could not tell
        except Exception as exc:  # noqa: BLE001 - never leak the address / secret; fail soft
            logger.warning("email verification (reacher) failed: %s", type(exc).__name__)
            return VERIFY_ERROR


def make_verifier(settings) -> "EmailVerifier | ReacherVerifier | FakeVerifier":
    if getattr(settings, "verify_mode", "real") == "fake":
        return FakeVerifier()
    if getattr(settings, "verify_provider", "zerobounce") == "reacher":
        return ReacherVerifier(settings.reacher_url, getattr(settings, "reacher_secret", ""))
    return EmailVerifier(settings.zerobounce_api_key)
