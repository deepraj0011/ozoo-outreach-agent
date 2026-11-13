from __future__ import annotations

import httpx
import pytest
import respx

from outreach.http import make_client, request_with_retry

URL = "https://api.example.com/x"


def _client():
    return httpx.Client(timeout=10.0)


@respx.mock
def test_request_with_retry_retries_503_then_ok():
    route = respx.get(URL).mock(side_effect=[httpx.Response(503), httpx.Response(200, json={"a": 1})])
    sleeps = []
    resp = request_with_retry(_client(), "GET", URL, sleep=sleeps.append)
    assert resp.status_code == 200
    assert route.call_count == 2
    assert sleeps == [0.5]


@respx.mock
def test_request_with_retry_gives_up():
    route = respx.post(URL).mock(return_value=httpx.Response(502))
    sleeps = []
    resp = request_with_retry(_client(), "POST", URL, json={}, sleep=sleeps.append)
    assert resp.status_code == 502  # last response returned, not raised
    assert route.call_count == 3
    assert sleeps == [0.5, 1.0]


@respx.mock
def test_request_with_retry_transport_error_then_raises():
    route = respx.get(URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(httpx.TransportError):
        request_with_retry(_client(), "GET", URL, sleep=lambda s: None)
    assert route.call_count == 3


@respx.mock
def test_request_with_retry_honours_retry_after_capped():
    respx.get(URL).mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "3"}),
        httpx.Response(429, headers={"Retry-After": "120"}),
        httpx.Response(200),
    ])
    sleeps = []
    assert request_with_retry(_client(), "GET", URL, sleep=sleeps.append).status_code == 200
    assert sleeps == [3.0, 10.0]


@respx.mock
def test_request_with_retry_no_retry_on_4xx():
    route = respx.get(URL).mock(return_value=httpx.Response(404))
    assert request_with_retry(_client(), "GET", URL, sleep=lambda s: None).status_code == 404
    assert route.call_count == 1


def test_make_client_has_10s_timeout():
    c = make_client()
    assert c.timeout.read == 10.0 and c.timeout.connect == 10.0


@respx.mock
def test_non_idempotent_no_retry_on_read_timeout():
    route = respx.post(URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(httpx.ReadTimeout):
        request_with_retry(_client(), "POST", URL, idempotent=False, sleep=lambda s: None)
    assert route.call_count == 1


@respx.mock
def test_non_idempotent_no_retry_on_500():
    for status in (500, 502, 504):
        route = respx.post(URL).mock(return_value=httpx.Response(status))
        resp = request_with_retry(_client(), "POST", URL, idempotent=False, sleep=lambda s: None)
        assert resp.status_code == status and route.call_count == 1
        respx.reset()


@respx.mock
def test_non_idempotent_retries_connect_error():
    route = respx.post(URL).mock(side_effect=[httpx.ConnectError("x"), httpx.ConnectTimeout("x"),
                                              httpx.Response(200)])
    assert request_with_retry(_client(), "POST", URL, idempotent=False, sleep=lambda s: None).status_code == 200
    assert route.call_count == 3


@respx.mock
def test_non_idempotent_retries_429_and_503():
    route = respx.post(URL).mock(side_effect=[httpx.Response(429), httpx.Response(503), httpx.Response(201)])
    assert request_with_retry(_client(), "POST", URL, idempotent=False, sleep=lambda s: None).status_code == 201
    assert route.call_count == 3
