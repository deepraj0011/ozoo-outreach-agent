from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

ZEROBOUNCE_URL = "https://api.zerobounce.net/v2/validate"
_MAP = {
    "valid": "valid",
    "invalid": "invalid",
    "catch-all": "catch_all",
    "spamtrap": "invalid",
    "abuse": "invalid",
    "do_not_mail": "invalid",
}


class EmailVerifier:
    """ZeroBounce email verification. Any failure maps to "unknown"."""

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
                return "unknown"
            status = str(resp.json().get("status", "")).lower()
            return _MAP.get(status, "unknown")
        except Exception as exc:  # noqa: BLE001 - never leak address/key; fail soft
            logger.warning("email verification failed: %s", type(exc).__name__)
            return "unknown"
