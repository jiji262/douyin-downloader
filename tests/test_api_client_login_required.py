"""Login-required detection in the shared API client."""

import pytest

from core import api_client as api_client_module
from core.api_client import (
    DouyinAPIClient,
    LoginRequiredError,
    _is_login_required,
    _summarize_api_response,
)


@pytest.mark.parametrize(
    "data,expected",
    [
        ({"status_code": 2483, "status_msg": "请先登录，再继续搜索吧"}, True),
        ({"status_code": 0, "status_msg": "请先登录后再试"}, True),  # msg match
        ({"status_code": 2483}, True),
        # Douyin's /profile/self/ (and other endpoints on an expired
        # session) answer status_code=8 with this exact message. Detect it
        # by message so a stray status_code=8 that means something else on
        # an unrelated endpoint does not get misread as a logout.
        ({"status_code": 8, "status_msg": "用户未登录"}, True),
        ({"status_code": 0, "status_msg": "ok"}, False),
        ({"status_code": 10000, "status_msg": "rate limited"}, False),
        ({}, False),
        ({"data": [{"aweme_info": {}}], "status_code": 0}, False),
        ("not-a-dict", False),
    ],
)
def test_is_login_required(data, expected):
    assert _is_login_required(data) is expected


def test_login_required_error_fields():
    err = LoginRequiredError(2483, "请先登录", "/aweme/v1/web/general/search/single/")
    assert err.status_code == 2483
    assert err.status_msg == "请先登录"
    assert err.path == "/aweme/v1/web/general/search/single/"
    assert "2483" in str(err)


def test_login_required_error_exported_from_core():
    from core import LoginRequiredError as Exported

    assert Exported is LoginRequiredError


class _FakeResp:
    def __init__(self, status, body, data):
        self.status = status
        self._body = body
        self._data = data

    async def read(self):
        return self._body

    async def json(self, content_type=None):
        return self._data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, resp):
        self._resp = resp
        self.closed = False

    def get(self, url, headers=None, proxy=None):
        return self._resp


def _install_fake_session(monkeypatch, client, resp):
    async def fake_ensure_session():
        client._session = _FakeSession(resp)

    monkeypatch.setattr(client, "_ensure_session", fake_ensure_session)
    monkeypatch.setattr(
        client, "build_signed_path", lambda path, params: ("http://example.test", "ua")
    )


@pytest.mark.asyncio
async def test_request_json_raises_login_required_to_caller(monkeypatch):
    client = DouyinAPIClient({"sessionid": "x"})
    resp = _FakeResp(
        200,
        '{"status_code":2483,"status_msg":"请先登录"}'.encode("utf-8"),
        {"status_code": 2483, "status_msg": "请先登录"},
    )
    _install_fake_session(monkeypatch, client, resp)

    with pytest.raises(LoginRequiredError) as excinfo:
        await client._request_json("/aweme/v1/web/general/search/single/", {})
    assert excinfo.value.status_code == 2483


@pytest.mark.asyncio
async def test_request_json_returns_dict_on_normal_response(monkeypatch):
    client = DouyinAPIClient({"sessionid": "x"})
    resp = _FakeResp(200, b'{"status_code":0}', {"status_code": 0, "data": []})
    _install_fake_session(monkeypatch, client, resp)

    result = await client._request_json("/x", {})
    assert result == {"status_code": 0, "data": []}


def test_api_response_summary_keeps_debug_fields_without_nested_payloads():
    summary = _summarize_api_response(
        {
            "status_code": 0,
            "status_msg": "ok\nnext",
            "aweme_list": [{"desc": "private-title"}, {"desc": "second-title"}],
            "has_more": 1,
            "max_cursor": 321,
            "verify_ticket": "private-ticket",
        }
    )

    assert summary["api_status"] == 0
    assert summary["status_msg"] == "ok next"
    assert summary["item_key"] == "aweme_list"
    assert summary["item_count"] == 2
    assert summary["has_more"] == 1
    assert summary["cursor"] == 321
    assert "private-title" not in repr(summary)
    assert "private-ticket" not in repr(summary)


@pytest.mark.asyncio
async def test_request_json_logs_safe_request_and_response_summary(monkeypatch):
    messages = []
    monkeypatch.setattr(
        api_client_module.logger,
        "info",
        lambda message, *args: messages.append(message % args),
    )
    client = DouyinAPIClient({"sessionid": "cookie-secret"})
    resp = _FakeResp(
        200,
        b'{"status_code":0,"aweme_list":[{},{}],"has_more":0}',
        {"status_code": 0, "aweme_list": [{}, {}], "has_more": 0},
    )
    _install_fake_session(monkeypatch, client, resp)

    await client._request_json("/aweme/v1/web/aweme/post/", {"msToken": "token-secret"})

    joined = "\n".join(messages)
    assert "Douyin API request:" in joined
    assert "Douyin API response:" in joined
    assert "path=/aweme/v1/web/aweme/post/" in joined
    assert "item_count=2" in joined
    assert "param_keys=msToken" in joined
    assert "cookie-secret" not in joined
    assert "token-secret" not in joined
