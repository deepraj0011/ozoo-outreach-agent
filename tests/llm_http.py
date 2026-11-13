"""Bridge respx routes to the LLM SDKs' HTTP stack.

The anthropic and openai SDKs send through `httpx2`, which respx does not patch. These helpers
turn a respx router into an `httpx2.MockTransport`, so the real SDK clients (built by
`make_llm`) talk to respx mocks of the real endpoints and never reach the network.
"""
from __future__ import annotations

from typing import Any

import httpx
import httpx2
import pytest
import respx


def _to_httpx(request: httpx2.Request) -> httpx.Request:
    return httpx.Request(request.method, str(request.url), headers=list(request.headers.raw),
                         content=request.read())


def mock_client(router: respx.Router) -> httpx2.Client:
    """An httpx2.Client whose every request is answered by `router` (unmatched -> error)."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        try:
            resp = router.handler(_to_httpx(request))
        except httpx.TimeoutException as exc:
            raise httpx2.ReadTimeout(str(exc), request=request) from None
        except httpx.ConnectError as exc:
            raise httpx2.ConnectError(str(exc), request=request) from None
        resp.read()
        return httpx2.Response(resp.status_code, headers=list(resp.headers.raw), content=resp.content)

    return httpx2.Client(transport=httpx2.MockTransport(handler))


@pytest.fixture
def no_retry_sleep(monkeypatch: Any) -> None:
    """Keep the SDKs' real retry loop but skip the backoff sleep."""
    import anthropic._base_client as ab
    import openai._base_client as ob

    monkeypatch.setattr(ab.SyncAPIClient, "_calculate_retry_timeout", lambda *a, **k: 0.0)
    monkeypatch.setattr(ob.SyncAPIClient, "_calculate_retry_timeout", lambda *a, **k: 0.0)
