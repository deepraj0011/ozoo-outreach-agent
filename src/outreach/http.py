"""Outbound HTTP helpers: explicit timeouts and bounded retry with backoff."""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional, Sequence

import httpx

logger = logging.getLogger(__name__)

# httpx logs full request URLs at INFO (may carry ids / keys); keep it quiet.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

DEFAULT_TIMEOUT = 10.0
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
RETRY_AFTER_CAP = 10.0


def make_client(**kw: Any) -> httpx.Client:
    """Every outbound client gets a 10 s timeout unless told otherwise."""
    kw.setdefault("timeout", DEFAULT_TIMEOUT)
    return httpx.Client(**kw)


def _retry_after(resp: httpx.Response) -> Optional[float]:
    raw = resp.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return min(max(float(raw), 0.0), RETRY_AFTER_CAP)
    except ValueError:
        return None  # HTTP-date form: fall back to our own backoff


def request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    attempts: int = 3,
    backoff: Sequence[float] = (0.5, 1.0, 2.0),
    sleep: Callable[[float], None] = time.sleep,
    **kw: Any,
) -> httpx.Response:
    """Send a request, retrying transport errors / timeouts and 429/5xx.

    Returns the last response (never raise_for_status). If every attempt failed at the
    transport level, the last exception is re-raised.
    """
    last_exc: Optional[Exception] = None
    resp: Optional[httpx.Response] = None
    for i in range(attempts):
        try:
            resp = client.request(method, url, **kw)
            last_exc = None
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            resp, last_exc = None, exc
        final = i == attempts - 1
        if resp is not None and resp.status_code not in RETRY_STATUSES:
            return resp
        if final:
            break
        delay = backoff[min(i, len(backoff) - 1)]
        if resp is not None:
            ra = _retry_after(resp)
            if ra is not None:
                delay = ra
            logger.info("HTTP %s %s -> %s; retry %d", method, httpx.URL(url).host, resp.status_code, i + 1)
        else:
            logger.info("HTTP %s %s -> %s; retry %d", method, httpx.URL(url).host,
                        type(last_exc).__name__, i + 1)
        sleep(delay)
    if resp is not None:
        return resp
    assert last_exc is not None
    raise last_exc
