"""人用 CLI 的退出码：登录失效 3、全部失败 1，其余保持 0。"""

import importlib
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent_mode.logging_guard import snapshot_logging
from agent_mode.version import get_version
from core import LoginRequiredError

main_module = importlib.import_module("cli.main")

VIDEO_URL = "https://www.douyin.com/video/7300000000000000000"


class _FakeAPIClient:
    def __init__(self, _cookies, proxy=None):
        self.proxy = proxy

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _FakeCookieManager:
    def set_cookies(self, cookies):
        self.cookies = cookies

    def get_cookies(self):
        return {}

    def validate_cookies(self):
        return True


class _RaisingDownloader:
    def __init__(self, exc):
        self.exc = exc

    async def download(self, parsed):
        raise self.exc


def _login_error():
    return LoginRequiredError(2483, "请先登录", "/aweme/v1/web/aweme/post/")


async def _download_with(monkeypatch, tmp_path, exc):
    monkeypatch.setattr(main_module, "DouyinAPIClient", _FakeAPIClient)
    monkeypatch.setattr(
        main_module.DownloaderFactory, "create", lambda *_a, **_k: _RaisingDownloader(exc)
    )
    config = main_module.ConfigLoader()
    config.update(path=str(tmp_path))
    return await main_module.download_url(VIDEO_URL, config, _FakeCookieManager())


@pytest.mark.asyncio
async def test_download_url_lets_login_required_through(monkeypatch, tmp_path):
    with pytest.raises(LoginRequiredError):
        await _download_with(monkeypatch, tmp_path, _login_error())


@pytest.mark.asyncio
async def test_download_url_still_swallows_other_errors(monkeypatch, tmp_path):
    assert await _download_with(monkeypatch, tmp_path, RuntimeError("boom")) is None


@pytest.fixture(autouse=True)
def _restore_logging_state():
    # main() 会调共享的 set_console_log_level，连 pytest 的捕获 handler 一起改级别；测完还原
    restore = snapshot_logging()
    yield
    restore()


def _run_main(monkeypatch, fake_main_async, argv=("-u", VIDEO_URL)):
    monkeypatch.setattr(sys, "argv", ["douyin-dl", *argv])
    monkeypatch.setattr(main_module, "main_async", fake_main_async)
    monkeypatch.setattr(main_module, "display", MagicMock())
    main_module.main()


def test_main_exits_3_when_login_cannot_be_recovered(monkeypatch):
    async def fake_main_async(args):
        raise _login_error()

    with pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, fake_main_async)
    assert excinfo.value.code == main_module.EXIT_LOGIN_REQUIRED == 3


def test_main_exits_1_when_every_url_failed(monkeypatch):
    async def fake_main_async(args):
        return main_module.EXIT_ALL_FAILED

    with pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, fake_main_async)
    assert excinfo.value.code == 1


def test_main_returns_normally_on_success_and_exits_0_on_ctrl_c(monkeypatch):
    async def ok_main_async(args):
        return None

    _run_main(monkeypatch, ok_main_async)

    async def interrupted_main_async(args):
        raise KeyboardInterrupt()

    with pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, interrupted_main_async)
    assert excinfo.value.code == 0


def _prepare_main_async(monkeypatch, tmp_path, results):
    """真实的 main_async，只把每个 URL 的下载结果换成 results 里预设的值。"""
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "config.yml"
    config_path.write_text("database: false\npath: %s\n" % (tmp_path / "out"), encoding="utf-8")
    pending = list(results)

    async def fake_download_url(*_args, **_kwargs):
        outcome = pending.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(main_module, "download_url", fake_download_url)
    monkeypatch.setattr(main_module, "CookieManager", _FakeCookieManager)
    monkeypatch.setattr(main_module, "display", MagicMock())
    monkeypatch.setattr(main_module, "can_interactive_login", lambda *, serve=False: False)
    return SimpleNamespace(
        config=str(config_path),
        url=["https://www.douyin.com/video/%d" % i for i in range(len(results))],
        path=None,
        thread=None,
        hot_board=None,
        search=None,
        serve=False,
        verbose=False,
        show_warnings=False,
    )


def _result(success=0, failed=0, skipped=0):
    return SimpleNamespace(
        total=success + failed + skipped, success=success, failed=failed, skipped=skipped
    )


@pytest.mark.parametrize(
    "case",
    [
        ([None, None], 1),
        ([_result(failed=1)], 1),  # 例如视频链接被门禁拒：结果对象是真值，但一条都没成
        ([_result(failed=2), None], 1),
        ([_result(skipped=3)], None),  # 增量重跑，全部已下载过
        ([_result()], None),  # 时间范围内 0 条，没有失败
        ([_result(success=1), _result(failed=1)], None),
        ([None, _result(success=1)], None),
        ([None, _result(skipped=1)], None),
    ],
)
@pytest.mark.asyncio
async def test_main_async_exit_code_follows_batch_outcome(monkeypatch, tmp_path, case):
    results, expected = case
    args = _prepare_main_async(monkeypatch, tmp_path, results)
    assert await main_module.main_async(args) == expected


@pytest.mark.asyncio
async def test_main_async_missing_config_still_returns_none(monkeypatch, tmp_path):
    args = _prepare_main_async(monkeypatch, tmp_path, [])
    args.config = str(tmp_path / "missing.yml")
    assert await main_module.main_async(args) is None


def _track_relogin_and_notifications(monkeypatch, *, interactive):
    calls = {"relogin": 0, "notified": []}

    async def fake_relogin():
        calls["relogin"] += 1
        return None

    async def fake_notify(_config, total_result, url_count):
        calls["notified"].append((total_result, url_count))

    monkeypatch.setattr(main_module, "can_interactive_login", lambda *, serve=False: interactive)
    monkeypatch.setattr(main_module, "interactive_relogin", fake_relogin)
    monkeypatch.setattr(main_module, "_dispatch_notifications", fake_notify)
    return calls


@pytest.mark.parametrize("interactive", [False, True])
@pytest.mark.asyncio
async def test_main_async_login_failure_does_not_abort_the_batch(
    monkeypatch, tmp_path, interactive
):
    results = [_login_error(), _login_error(), _result(success=1)]
    args = _prepare_main_async(monkeypatch, tmp_path, results)
    calls = _track_relogin_and_notifications(monkeypatch, interactive=interactive)

    assert await main_module.main_async(args) == main_module.EXIT_LOGIN_REQUIRED == 3

    fake_display = main_module.display
    assert fake_display.start_url.call_count == len(results)
    assert fake_display.complete_url.call_count == 1
    assert fake_display.fail_url.call_count == 2
    # 交互下只有第一次登录失败会尝试重新登录，之后不再弹浏览器；非交互从不尝试
    assert calls["relogin"] == (1 if interactive else 0)
    assert fake_display.show_result.call_count == 1
    assert len(calls["notified"]) == 1 and calls["notified"][0][1] == len(results)


@pytest.mark.asyncio
async def test_main_async_login_failure_outranks_all_failed(monkeypatch, tmp_path):
    args = _prepare_main_async(monkeypatch, tmp_path, [_login_error(), None, _result(failed=1)])
    assert await main_module.main_async(args) == main_module.EXIT_LOGIN_REQUIRED


def test_version_flag_reads_pyproject_version(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["douyin-dl", "--version"])
    with pytest.raises(SystemExit) as excinfo:
        main_module.main()
    assert excinfo.value.code == 0
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text("utf-8")
    expected = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE).group(1)
    assert capsys.readouterr().out.strip() == get_version() == expected


def test_agent_argv_is_dispatched_before_human_argparse(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["douyin-dl", "agent", "--bogus"])
    with pytest.raises(SystemExit) as excinfo:
        main_module.main()
    assert excinfo.value.code == 2
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert json.loads(out)["error"]["code"] == "USAGE"
