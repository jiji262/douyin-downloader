"""Risk-control (WAF) HTTP status handling in the shared API client.

Douyin's edge fronts the web API with a WAF that answers ``403`` when a
caller trips a rate-based rule — observed in the wild after ~66
consecutive ``listcollection`` pages at ~1 req/s, with the *same*
cookies succeeding again seconds later. A genuine auth failure never
looks like this: Douyin answers those with HTTP 200 plus a non-zero
``status_code`` (see ``test_api_client_login_required``). So ``403``
must be retried like ``429``, not treated as a terminal client error.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from core import api_client as api_client_module
from core.api_client import DouyinAPIClient


class _FakeResp:
    def __init__(self, status: int, body: bytes, data: Optional[Dict[str, Any]]):
        self.status = status
        self._body = body
        self._data = data

    async def read(self) -> bytes:
        return self._body

    async def json(self, content_type=None):
        if self._data is None:
            raise ValueError("no json body")
        return self._data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _SequencedSession:
    """Serves a scripted list of responses, one per request."""

    def __init__(self, responses: List[_FakeResp]):
        self._responses = list(responses)
        self.calls: List[str] = []
        self.bodies: List[Any] = []
        self.closed = False

    def _next(self, method: str, kwargs: Dict[str, Any]) -> _FakeResp:
        self.calls.append(method)
        # Record the form body so a retry can be checked for re-sending it —
        # ``_request_json`` reuses the name ``data`` for both the request body
        # and the parsed response, so this is a real hazard, not a hypothetical.
        self.bodies.append(kwargs.get("data"))
        if not self._responses:
            raise AssertionError("session called more times than scripted")
        return self._responses.pop(0)

    def get(self, url, **kwargs):
        return self._next("GET", kwargs)

    def post(self, url, **kwargs):
        return self._next("POST", kwargs)


def _install(monkeypatch, client: DouyinAPIClient, session: _SequencedSession) -> List[int]:
    """Wire the fake session in and record (without serving) backoff sleeps."""
    slept: List[int] = []

    async def fake_ensure_session():
        client._session = session

    async def fake_sleep(delay):
        slept.append(delay)

    monkeypatch.setattr(client, "_ensure_session", fake_ensure_session)
    monkeypatch.setattr(
        client,
        "build_signed_path",
        lambda path, params, **kwargs: ("http://example.test", "ua"),
    )
    monkeypatch.setattr(api_client_module.asyncio, "sleep", fake_sleep)
    return slept


def _ok(payload: Dict[str, Any]) -> _FakeResp:
    import json as _json

    return _FakeResp(200, _json.dumps(payload).encode("utf-8"), payload)


def _waf(status: int) -> _FakeResp:
    return _FakeResp(status, b"", None)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 429])
async def test_risk_control_status_is_retried_until_it_clears(monkeypatch, status):
    """A WAF rejection is transient: retry and return the recovered page."""
    client = DouyinAPIClient({"sessionid": "x"})
    payload = {"status_code": 0, "aweme_list": [{"aweme_id": "1"}], "has_more": 1}
    session = _SequencedSession([_waf(status), _ok(payload)])
    slept = _install(monkeypatch, client, session)

    result = await client._request_json("/aweme/v1/web/aweme/listcollection/", {})

    assert result == payload
    assert session.calls == ["GET", "GET"]
    assert slept, "a risk-control retry must back off before re-requesting"


@pytest.mark.asyncio
async def test_risk_control_reuses_the_ordinary_backoff_schedule(monkeypatch):
    """Deliberately NOT a longer WAF-specific schedule. ``_request_json``
    fronts every Douyin call, so a bigger budget here blows the renderer's
    15s timeout on the my-content routes and stalls per-item loops. Pinned
    with literals so a future change to the schedule has to come here.
    """
    client = DouyinAPIClient({"sessionid": "x"})
    session = _SequencedSession([_waf(403), _waf(403), _ok({"status_code": 0})])
    slept = _install(monkeypatch, client, session)

    await client._request_json("/aweme/v1/web/aweme/listcollection/", {})

    assert slept == [1, 2]
    assert sum(slept) <= 10, "risk-control retries must stay well under the 15s client timeout"


@pytest.mark.asyncio
async def test_risk_control_and_server_error_each_get_their_own_attempt_budget(monkeypatch):
    """Mixed 5xx/403 interleavings must not leak the flag between attempts."""
    client = DouyinAPIClient({"sessionid": "x"})
    session = _SequencedSession([_waf(500), _waf(403), _ok({"status_code": 0})])
    slept = _install(monkeypatch, client, session)

    result = await client._request_json("/aweme/v1/web/aweme/listcollection/", {})

    assert result == {"status_code": 0}
    assert session.calls == ["GET", "GET", "GET"]
    assert slept == [1, 2]


@pytest.mark.asyncio
async def test_risk_control_exhausted_returns_empty_dict(monkeypatch):
    """Still no exception once retries run out — callers detect the
    failure by the empty payload, which is what they already do."""
    client = DouyinAPIClient({"sessionid": "x"})
    session = _SequencedSession([_waf(403), _waf(403), _waf(403)])
    _install(monkeypatch, client, session)

    result = await client._request_json("/aweme/v1/web/aweme/listcollection/", {})

    assert result == {}
    assert session.calls == ["GET", "GET", "GET"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 404, 418])
async def test_non_risk_control_client_errors_stay_terminal(monkeypatch, status):
    """A genuine 4xx is not worth a retry — fail fast, as before."""
    client = DouyinAPIClient({"sessionid": "x"})
    session = _SequencedSession([_waf(status)])
    slept = _install(monkeypatch, client, session)

    result = await client._request_json("/aweme/v1/web/aweme/post/", {})

    assert result == {}
    assert session.calls == ["GET"]
    assert slept == []


@pytest.mark.asyncio
async def test_post_risk_control_retry_preserves_form_body(monkeypatch):
    """``listcollection`` is a form POST whose cursor lives in the body;
    the retry must re-send it rather than silently degrade to a GET."""
    client = DouyinAPIClient({"sessionid": "x"})
    payload = {"status_code": 0, "aweme_list": [], "has_more": 0}
    session = _SequencedSession([_waf(403), _ok(payload)])
    _install(monkeypatch, client, session)

    result = await client._request_json(
        "/aweme/v1/web/aweme/listcollection/",
        {},
        method="POST",
        data={"count": 20, "cursor": 1785657343726665},
    )

    assert result == payload
    assert session.calls == ["POST", "POST"]
    # The retry must carry the same cursor. ``_request_json`` rebinds its own
    # ``data`` parameter to the parsed JSON response on a successful attempt,
    # so a body that survives the retry is a real invariant worth pinning.
    assert session.bodies == [{"count": 20, "cursor": 1785657343726665}] * 2
