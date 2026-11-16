"""agent `login`：浏览器全部用 fake，不启动 Playwright，不发任何请求。"""

import asyncio
import json
import os
import shlex
import stat
import sys

import pytest

from agent_mode import entry, login_cmd, state

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
FAKE_SESSION = "fake-session-value-for-login-test"
SESSION_COOKIE = {"name": "sessionid", "value": FAKE_SESSION, "domain": ".douyin.com"}
DEVICE_COOKIE = {"name": "ttwid", "value": "fake-ttwid", "domain": "www.douyin.com"}
FOREIGN_COOKIE = {"name": "sessionid", "value": "other-site", "domain": ".notdouyin.com"}


class _FakePage:
    def __init__(self):
        self.visited = []

    async def goto(self, url, **kwargs):
        self.visited.append(url)

    async def evaluate(self, script):
        return ""


class _FakeContext:
    """每次 cookies() 取脚本里的下一项；最后一项一直重复。异常项会被抛出。"""

    def __init__(self, script):
        self.script = list(script)
        self.page = _FakePage()

    async def cookies(self):
        step = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(step, Exception):
            raise step
        return step

    async def new_page(self):
        return self.page


class _FakeBrowser:
    def __init__(self, script):
        self.context = _FakeContext(script)
        self.closed = False

    async def new_context(self):
        return self.context

    async def close(self):
        self.closed = True


class _FakePlaywright:
    def __init__(self, script, launch_error=None):
        self.browser = _FakeBrowser(script)
        self.launch_error = launch_error
        self.launches = []
        self.chromium = self

    async def launch(self, **kwargs):
        self.launches.append(kwargs)
        if self.launch_error is not None:
            raise self.launch_error
        return self.browser

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


@pytest.fixture
def home(monkeypatch, tmp_path):
    path = tmp_path / "home"
    monkeypatch.setenv("DOUYIN_DL_HOME", str(path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(login_cmd, "display_missing", lambda platform, environ: False)
    monkeypatch.setattr(login_cmd, "POLL_INTERVAL_S", 0.0)
    monkeypatch.setattr(login_cmd, "SETTLE_SECONDS", 0.0)
    return path


def _install_browser(monkeypatch, script, launch_error=None):
    fake = _FakePlaywright(script, launch_error)
    monkeypatch.setattr(login_cmd, "_load_playwright", lambda: lambda: fake)
    return fake


def _run(capsys, argv):
    code = entry.main(argv)
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert FAKE_SESSION not in out
    return code, json.loads(out)


def _mode(path):
    return stat.S_IMODE(os.stat(str(path)).st_mode)


def _poll(script, timeout_s):
    """用假时钟跑 wait_for_session，返回 (结果, 每次 sleep 的秒数)。"""
    context = _FakeContext(script)
    now = [0.0]
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    result = asyncio.run(
        login_cmd.wait_for_session(
            context.cookies, timeout_s=timeout_s, sleep=sleep, clock=lambda: now[0]
        )
    )
    return result, sleeps


def test_poll_waits_for_session_then_settles():
    script = [[], [DEVICE_COOKIE], [SESSION_COOKIE], [SESSION_COOKIE, DEVICE_COOKIE]]
    result, sleeps = _poll(script, timeout_s=300)
    assert result == {"sessionid": FAKE_SESSION, "ttwid": "fake-ttwid"}
    assert sleeps == [login_cmd.POLL_INTERVAL_S] * 2 + [login_cmd.SETTLE_SECONDS]


def test_poll_accepts_sessionid_ss_and_ignores_other_domains():
    result, _ = _poll([[FOREIGN_COOKIE], [dict(SESSION_COOKIE, name="sessionid_ss")]], 300)
    assert result == {"sessionid_ss": FAKE_SESSION}


def test_poll_times_out_without_session():
    result, sleeps = _poll([[DEVICE_COOKIE], [FOREIGN_COOKIE]], timeout_s=5)
    assert result is None
    assert sum(sleeps) == 5
    assert max(sleeps) <= login_cmd.POLL_INTERVAL_S


def test_display_missing_only_on_headless_linux():
    assert login_cmd.display_missing("linux", {}) is True
    assert login_cmd.display_missing("linux", {"DISPLAY": " "}) is True
    assert login_cmd.display_missing("linux", {"DISPLAY": ":0"}) is False
    assert login_cmd.display_missing("linux", {"WAYLAND_DISPLAY": "wayland-0"}) is False
    assert not any(login_cmd.display_missing(name, {}) for name in ("darwin", "win32"))


@posix_only
def test_browser_login_saves_cookies_privately(home, monkeypatch, capsys):
    fake = _install_browser(monkeypatch, [[], [SESSION_COOKIE, DEVICE_COOKIE, FOREIGN_COOKIE]])
    code, result = _run(capsys, ["login"])
    assert code == 0
    assert result["data"] == {
        "saved_to": str(home / "cookies.json"),
        "keys_present": ["sessionid", "ttwid"],
        "login_status": "logged_in",
    }
    assert fake.launches == [{"headless": False}]
    assert fake.browser.context.page.visited == [login_cmd.LOGIN_URL]
    assert fake.browser.closed is True
    assert _mode(home / "cookies.json") == 0o600
    assert _mode(home) == 0o700
    assert state.read_cookies() == {"sessionid": FAKE_SESSION, "ttwid": "fake-ttwid"}
    assert sorted(p.name for p in home.parent.iterdir()) == ["home"]


def test_browser_login_timeout_is_retryable_login_required(home, monkeypatch, capsys):
    fake = _install_browser(monkeypatch, [[DEVICE_COOKIE]])

    async def never_logged_in(read_cookies, *, timeout_s):
        assert timeout_s == 7
        return None

    monkeypatch.setattr(login_cmd, "wait_for_session", never_logged_in)
    code, result = _run(capsys, ["login", "--timeout", "7"])
    assert code == 3
    assert result["error"]["code"] == "LOGIN_REQUIRED"
    assert result["error"]["retryable"] is True
    assert fake.browser.closed is True
    assert not home.exists()


def test_browser_closed_by_user_is_login_required(home, monkeypatch, capsys):
    closed = Exception("Target page, context or browser has been closed")
    _install_browser(monkeypatch, [[], closed])
    code, result = _run(capsys, ["login"])
    assert code == 3
    assert result["error"]["retryable"] is True
    assert not home.exists()


def test_missing_playwright_is_dependency_missing(home, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)
    monkeypatch.setattr("agent_mode.envelope._pip_available", lambda: True)
    code, result = _run(capsys, ["login"])
    assert code == 7
    action = result["error"]["action"]
    assert result["error"]["code"] == "DEPENDENCY_MISSING"
    assert action["kind"] == "install"
    assert action["command"] == "%s -m pip install playwright" % shlex.quote(sys.executable)
    assert "%s -m playwright install chromium" % shlex.quote(sys.executable) in action["hint"]


def test_missing_chromium_is_dependency_missing(home, monkeypatch, capsys):
    error = Exception("BrowserType.launch: Executable doesn't exist at /x/chrome")
    _install_browser(monkeypatch, [[]], launch_error=error)
    code, result = _run(capsys, ["login"])
    assert code == 7
    assert result["error"]["action"] == {
        "kind": "install",
        "command": "%s -m playwright install chromium" % shlex.quote(sys.executable),
    }


def test_no_display_is_usage_pointing_at_from_file(home, monkeypatch, capsys):
    monkeypatch.setattr(login_cmd, "display_missing", lambda platform, environ: True)
    monkeypatch.setattr(login_cmd, "_load_playwright", lambda: pytest.fail("must not load"))
    code, result = _run(capsys, ["login"])
    assert code == 2
    assert result["error"]["code"] == "USAGE"
    assert result["error"]["action"]["kind"] == "fix_usage"
    assert "--from-file" in result["error"]["action"]["hint"]


@posix_only
def test_from_file_mapping_is_written_0600_without_a_display(home, monkeypatch, capsys):
    monkeypatch.setattr(login_cmd, "display_missing", lambda platform, environ: True)
    export = home.parent / "export.json"
    export.write_text(json.dumps({"sessionid": FAKE_SESSION, "msToken": "m", "blank": ""}))
    code, result = _run(capsys, ["login", "--from-file", "export.json"])
    assert code == 0
    assert result["data"]["keys_present"] == ["sessionid", "msToken"]
    assert result["data"]["login_status"] == "logged_in"
    assert _mode(home / "cookies.json") == 0o600
    assert state.read_cookies() == {"sessionid": FAKE_SESSION, "msToken": "m"}


def _import(home, capsys, content):
    export = home.parent / "export.json"
    export.write_text(content if isinstance(content, str) else json.dumps(content))
    return _run(capsys, ["login", "--from-file", str(export)])


def test_from_file_browser_export_list_keeps_only_douyin(home, capsys):
    export = [DEVICE_COOKIE, FOREIGN_COOKIE, {"name": "odin_tt", "value": 1}]
    code, result = _import(home, capsys, export)  # 没有已保存会话：只有设备 cookie 也接受
    assert code == 0
    assert result["data"]["keys_present"] == ["ttwid", "odin_tt"]
    assert result["data"]["login_status"] == "device_only"
    assert state.read_cookies() == {"ttwid": "fake-ttwid", "odin_tt": "1"}
    assert len(result["warnings"]) == 2


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '"just a string"',
        "{}",
        json.dumps({"sessionid": {"nested": FAKE_SESSION}}),
        json.dumps([{"name": "sessionid"}]),
        json.dumps([["sessionid", FAKE_SESSION]]),
        json.dumps([FOREIGN_COOKIE]),
        json.dumps({"name": "pkg", "version": "1.0.0"}),
    ],
)
def test_from_file_rejects_bad_content(home, capsys, content):
    code, result = _import(home, capsys, content)
    assert code == 2
    assert result["error"]["code"] == "USAGE"
    assert not home.exists()


@pytest.mark.parametrize("content", [{"ttwid": "new-device"}, {"sessionid": "", "foo": "bar"}])
def test_from_file_refuses_to_downgrade_a_saved_login(home, capsys, content):
    assert _import(home, capsys, {"sessionid": FAKE_SESSION, "ttwid": "t"})[0] == 0
    saved = home / "cookies.json"
    before = (saved.read_bytes(), saved.stat().st_mtime_ns)
    code, result = _import(home, capsys, content)
    assert (code, result["error"]["code"]) == (2, "USAGE")
    assert (saved.read_bytes(), saved.stat().st_mtime_ns) == before
    assert state.login_status(state.read_cookies()) == "logged_in"
    if "foo" not in content:  # 设备 cookie 导出：提示重新导出或走浏览器登录
        assert result["error"]["action"]["kind"] == "fix_usage"
        assert "douyin-dl agent login" in result["error"]["action"]["hint"]


@pytest.mark.parametrize("argv", [["--timeout", "901"], ["--from-file", "nope.json"]])
def test_timeout_above_limit_and_missing_path_are_usage(home, capsys, argv):
    code, result = _run(capsys, ["login", *argv])
    assert code == 2
    assert result["error"]["code"] == "USAGE"
