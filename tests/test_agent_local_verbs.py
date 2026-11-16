"""doctor --online、inspect 短链、NDJSON 事件输出：网络层全部用假 client，不发任何请求。"""

import io
import json

import pytest

from agent_mode import entry, events, state

FAKE_SESSION = "fake-session-value-for-verb-test"
SHORT_URL = "https://v.douyin.com/AbCdEfG/"


class _FakeClient:
    """按测试设置的类属性应答；记录收到的调用。"""

    self_info = None
    self_error = None
    resolved = None
    calls = []

    def __init__(self, cookies, proxy=None):
        type(self).calls.append(("init", sorted(cookies), proxy))

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get_self_info(self):
        type(self).calls.append(("get_self_info",))
        if type(self).self_error is not None:
            raise type(self).self_error
        return type(self).self_info

    async def resolve_short_url(self, url):
        type(self).calls.append(("resolve_short_url", url))
        return type(self).resolved


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DOUYIN_DL_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("DOUYIN_PROXY", raising=False)
    monkeypatch.chdir(tmp_path)
    fake = type("FakeClient", (_FakeClient,), {"calls": []})
    monkeypatch.setattr("core.api_client.DouyinAPIClient", fake)
    return fake


def _run(capsys, argv):
    code = entry.main(argv)
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    return code, json.loads(out)


def _online(capsys):
    code, result = _run(capsys, ["doctor", "--online"])
    assert code == 0
    assert result["ok"] is True
    return result["data"]["online_check"]


def test_doctor_online_without_session_sends_nothing(client, capsys):
    check = _online(capsys)
    assert check["status"] == "skipped"
    assert check["logged_in"] is False
    assert client.calls == []


def test_doctor_online_logged_in_makes_one_request(client, capsys):
    state.write_cookies({"sessionid": FAKE_SESSION})
    client.self_info = {"nickname": "someone", "sec_uid": "MS4w"}
    check = _online(capsys)
    assert check["status"] == "logged_in"
    assert check["logged_in"] is True
    assert check["endpoint"] == "/aweme/v1/web/user/profile/self/"
    assert client.calls == [("init", ["sessionid"], ""), ("get_self_info",)]


def test_doctor_online_expired_session_still_exits_0(client, capsys):
    from core.api_client import LoginRequiredError

    state.write_cookies({"sessionid": FAKE_SESSION})
    client.self_error = LoginRequiredError(8, "用户未登录", "/aweme/v1/web/user/profile/self/")
    check = _online(capsys)
    assert check["status"] == "login_required"
    assert check["logged_in"] is False


def test_doctor_online_reports_empty_and_errors_without_leaking(client, capsys):
    state.write_cookies({"sessionid": FAKE_SESSION})
    assert _online(capsys)["status"] == "empty"
    client.self_error = RuntimeError("boom with sessionid=%s" % FAKE_SESSION)
    check = _online(capsys)
    assert check["status"] == "error"
    assert check["detail"] == "RuntimeError"


def test_doctor_online_uses_proxy_from_env(client, capsys, monkeypatch):
    monkeypatch.setenv("DOUYIN_PROXY", "http://127.0.0.1:8899")
    state.write_cookies({"sessionid": FAKE_SESSION})
    _online(capsys)
    assert client.calls[0] == ("init", ["sessionid"], "http://127.0.0.1:8899")


def test_inspect_resolves_short_link_once_without_cookies(client, capsys):
    state.write_cookies({"sessionid": FAKE_SESSION})
    client.resolved = "https://www.douyin.com/user/MS4wLjABAAAAxyz?from=share"
    code, result = _run(capsys, ["inspect", "v.douyin.com/AbCdEfG/"])
    assert code == 0
    assert result["data"]["input"] == "v.douyin.com/AbCdEfG/"
    assert result["data"]["resolved_url"] == client.resolved
    assert result["data"]["url_type"] == "user"
    assert result["data"]["ids"] == {"sec_uid": "MS4wLjABAAAAxyz"}
    assert client.calls == [("init", [], ""), ("resolve_short_url", SHORT_URL)]


@pytest.mark.parametrize("link", ["http://v.douyin.com/AbCdEfG/", "HTTP://v.douyin.com/AbCdEfG/"])
def test_inspect_upgrades_http_short_link_to_https(client, capsys, link):
    client.resolved = "https://live.douyin.com/123456789"
    code, result = _run(capsys, ["inspect", link])
    assert (code, result["data"]["input"]) == (0, link)
    assert client.calls == [("init", [], ""), ("resolve_short_url", SHORT_URL)]


def test_public_url_helpers_refuse_non_douyin_short_hosts(client):
    import asyncio

    from agent_mode import inspect_cmd

    assert inspect_cmd.extract_url("看看 http://v.douyin.com/AbCdEfG/ 复制") == (
        "http://v.douyin.com/AbCdEfG/"
    )
    assert asyncio.run(inspect_cmd.resolve_short_url("http://bit.ly/x")) is None
    assert client.calls == []


def test_inspect_short_link_to_gated_type_is_argus_gated(client, capsys):
    client.resolved = "https://www.douyin.com/video/7300000000000000000"
    code, result = _run(capsys, ["inspect", SHORT_URL])
    assert code == 4
    assert result["error"]["code"] == "ARGUS_GATED"
    assert result["data"]["ids"] == {"aweme_id": "7300000000000000000"}


@pytest.mark.parametrize("resolved", [None, "", SHORT_URL])
def test_inspect_unresolvable_short_link_is_upstream_empty(client, capsys, resolved):
    client.resolved = resolved
    code, result = _run(capsys, ["inspect", SHORT_URL])
    assert code == 5
    assert result["error"]["code"] == "UPSTREAM_EMPTY"
    assert result["error"]["retryable"] is True
    assert result["error"]["action"]["kind"] == "retry_later"
    assert result["error"]["action"]["retry_after_s"] > 0
    assert result["data"] == {"input": SHORT_URL, "resolved_url": None}
    assert len([c for c in client.calls if c[0] == "resolve_short_url"]) == 1


def test_inspect_long_url_never_builds_a_client(client, capsys):
    code, result = _run(capsys, ["inspect", "https://live.douyin.com/123456789"])
    assert code == 0
    assert result["data"]["ids"] == {"room_id": "123456789"}
    assert client.calls == []


def test_ndjson_reporter_throttles_progress_but_not_phase_changes():
    stream, now = io.StringIO(), [0.0]
    reporter = events.NdjsonReporter(stream, clock=lambda: now[0])
    reporter.update_step("load\nmodel", "base")
    reporter.set_item_total(3, "files")
    reporter.advance_item("failed", "a.mp4", reason="boom")
    for second, phase in ((0, "recording"), (1, "recording"), (6, "recording"), (7, "finalizing")):
        now[0] = float(second)
        reporter.on_live_progress(phase=phase, recorded_seconds=second, bytes=1, segments=0)
    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert lines[0] == {"event": "step", "step": "load model", "detail": "base"}
    assert lines[1] == {"event": "progress", "total": 3, "detail": "files"}
    assert lines[2] == {"event": "item", "status": "failed", "id": "a.mp4", "reason": "boom"}
    assert [(e["phase"], e["recorded_s"]) for e in lines[3:]] == [
        ("recording", 0),
        ("recording", 6),
        ("finalizing", 7),
    ]
    assert isinstance(events.make_reporter(False), events.SilentReporter)
    assert isinstance(events.make_reporter(True), events.NdjsonReporter)
