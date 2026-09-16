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

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from core import api_client as api_client_module
from core.api_client import DouyinAPIClient


class _FakeContent:
    """aiohttp ``StreamReader`` 的最小替身:``read(n)`` 最多给 n 字节,读完回 b""。"""

    def __init__(self, body: bytes):
        self._body = body

    async def read(self, n: int = -1) -> bytes:
        size = len(self._body) if n < 0 else n
        chunk, self._body = self._body[:size], self._body[size:]
        return chunk


class _FakeResp:
    def __init__(self, status: int, body: bytes, data: Optional[Dict[str, Any]]):
        self.status = status
        self._body = body
        self._data = data
        self.content = _FakeContent(body)

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


# ---------------------------------------------------------------------------
# Argus 门禁的 403 不是限速(docs/spec/common-mistakes.md「Argus 门禁」)
#
# 2026-09-15 用户日志:0.11.5 直连 aweme/post/,9 次 403 全部重试,任务照样失败,
# 日志里却没有 body,看不出是 Argus 还是限速。
# ---------------------------------------------------------------------------


def _argus_403() -> _FakeResp:
    return _FakeResp(403, b"Blocked by ArgusSecurityPlugin Uifid Not Found", None)


@pytest.mark.asyncio
async def test_argus_403_is_not_retried_and_is_marked_as_rejection(monkeypatch):
    """body 带 ArgusSecurityPlugin = 请求形状被确定性拒绝,重试 / 等待都没用。"""
    from core.api_client import FailedPayload

    client = DouyinAPIClient({"sessionid": "x"})
    session = _SequencedSession([_argus_403()])
    slept = _install(monkeypatch, client, session)

    result = await client._request_json("/aweme/v1/web/aweme/post/", {})

    # 对老调用方仍是「失败回 {}」
    assert result == {} and not result
    assert session.calls == ["GET"]
    assert slept == []
    assert isinstance(result, FailedPayload)
    assert result.kind == FailedPayload.REJECTED
    assert result.status == 403
    assert result.via_bridge is False
    assert "ArgusSecurityPlugin" in result.detail


@pytest.mark.asyncio
async def test_plain_403_without_argus_body_is_still_retried(monkeypatch):
    """限速 403 仍按老规矩重试,只有带 Argus 标记的才停。"""
    client = DouyinAPIClient({"sessionid": "x"})
    session = _SequencedSession([_FakeResp(403, b"rate limited", None), _ok({"status_code": 0})])
    _install(monkeypatch, client, session)

    result = await client._request_json("/aweme/v1/web/aweme/post/", {})

    assert result == {"status_code": 0}
    assert session.calls == ["GET", "GET"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body"),
    [(403, b"rate limited"), (404, b"not here"), (403, b"Blocked by ArgusSecurityPlugin")],
)
async def test_non_200_failure_logs_body_prefix(monkeypatch, caplog, status, body):
    """403 日志必须带 body 前 80 字,否则 Argus 门禁与限速在日志里长得一样。"""
    import logging

    monkeypatch.setattr(logging.getLogger("APIClient"), "propagate", True)
    client = DouyinAPIClient({"sessionid": "x"})
    session = _SequencedSession([_FakeResp(status, body, None)] * 3)
    _install(monkeypatch, client, session)

    with caplog.at_level("INFO", logger="APIClient"):
        await client._request_json("/aweme/v1/web/aweme/post/", {})

    assert f"body={body.decode()!r}" in caplog.text


@pytest.mark.asyncio
async def test_body_prefix_is_truncated_in_logs(monkeypatch, caplog):
    import logging

    monkeypatch.setattr(logging.getLogger("APIClient"), "propagate", True)
    client = DouyinAPIClient({"sessionid": "x"})
    session = _SequencedSession([_FakeResp(404, b"x" * 500, None)])
    _install(monkeypatch, client, session)

    with caplog.at_level("INFO", logger="APIClient"):
        await client._request_json("/aweme/v1/web/aweme/post/", {})

    assert f"body={'x' * 80!r}" in caplog.text
    assert "x" * 81 not in caplog.text


class _StallingContent:
    """先给一段 body,之后既不结束也不再出数据(慢速 / 断流的错误响应)。"""

    def __init__(self, first: bytes):
        self._first = first

    async def read(self, n: int = -1) -> bytes:
        if self._first:
            chunk, self._first = self._first, b""
            return chunk
        await asyncio.Event().wait()
        return b""


class _StallingResp(_FakeResp):
    def __init__(self, status: int, first: bytes):
        super().__init__(status, b"", None)
        self.content = _StallingContent(first)

    async def read(self) -> bytes:
        await asyncio.Event().wait()
        return b""


@pytest.mark.asyncio
async def test_error_body_read_is_bounded_and_keeps_the_received_prefix(monkeypatch):
    """错误 body 不结束时不能挂到请求超时,已经收到的 Argus 标记也不能丢。"""
    from core.api_client import FailedPayload

    monkeypatch.setattr(api_client_module, "_ERROR_BODY_READ_TIMEOUT_SECONDS", 0.05)
    client = DouyinAPIClient({"sessionid": "x"})
    first = b"Blocked by ArgusSecurityPlugin Uifid Not Found" + b" " * 120
    session = _SequencedSession([_StallingResp(403, first)])
    _install(monkeypatch, client, session)

    result = await asyncio.wait_for(
        client._request_json("/aweme/v1/web/aweme/post/", {}), timeout=2
    )

    assert isinstance(result, FailedPayload)
    assert result.kind == FailedPayload.REJECTED
    assert session.calls == ["GET"]


@pytest.mark.asyncio
async def test_cancelling_while_reading_error_body_propagates(monkeypatch):
    """外部取消(用户取消任务)不能被错误 body 的有界读取吞掉。

    3.9–3.11 上 ``asyncio.wait_for`` 在「读完」与取消同时发生时会吞掉取消
    (CPython gh-86296);这里钉住基本传播,竞态本身见 docs/spec/gotchas.md。
    """
    client = DouyinAPIClient({"sessionid": "x"})
    session = _SequencedSession([_StallingResp(403, b"")])
    _install(monkeypatch, client, session)

    task = asyncio.ensure_future(client._request_json("/aweme/v1/web/aweme/post/", {}))
    await asyncio.wait({task}, timeout=0.05)
    assert task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


class _BrokenContent:
    """先给一段 body,随后断流抛异常。"""

    def __init__(self, first: bytes):
        self._first = first

    async def read(self, n: int = -1) -> bytes:
        if self._first:
            chunk, self._first = self._first, b""
            return chunk
        raise ConnectionResetError("peer closed")


@pytest.mark.asyncio
async def test_error_body_stream_failure_keeps_the_received_prefix(monkeypatch):
    from core.api_client import FailedPayload

    client = DouyinAPIClient({"sessionid": "x"})
    resp = _FakeResp(403, b"", None)
    resp.content = _BrokenContent(b"Blocked by ArgusSecurityPlugin Uifid Not Found")
    session = _SequencedSession([resp])
    _install(monkeypatch, client, session)

    result = await client._request_json("/aweme/v1/web/aweme/post/", {})

    assert isinstance(result, FailedPayload) and result.kind == FailedPayload.REJECTED
    assert session.calls == ["GET"]
