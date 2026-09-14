import asyncio
import logging
import sys
from typing import Any, Dict, List

import pytest

from control.queue_manager import QueueManager
from core.api_client import DouyinAPIClient
from core.downloader_base import DownloadResult
from core.user_downloader import UserDownloader
from core.user_modes.base_strategy import PageRequestFailedError
from storage.file_manager import FileManager


def _make_aweme(aweme_id: str, **overrides: Any) -> Dict[str, Any]:
    aweme = {
        "aweme_id": aweme_id,
        "desc": f"desc-{aweme_id}",
        "create_time": 1700000000,
        "author": {"nickname": "tester", "uid": "uid-1"},
        "video": {"play_addr": {"url_list": ["https://example.com/video.mp4"]}},
    }
    aweme.update(overrides)
    return aweme


class _FakeConfig:
    def __init__(self, data: Dict[str, Any]):
        self._data = data

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


class _FakeCookieManager:
    pass


class _NoopRateLimiter:
    async def acquire(self):
        return


class _FakeProgressReporter:
    def __init__(self):
        self.step_updates: List[tuple[str, str]] = []
        self.item_totals: List[tuple[int, str]] = []
        self.item_events: List[tuple[str, str]] = []

    def update_step(self, step: str, detail: str = "") -> None:
        self.step_updates.append((step, detail))

    def set_item_total(self, total: int, detail: str = "") -> None:
        self.item_totals.append((total, detail))

    def advance_item(self, status: str, detail: str = "") -> None:
        self.item_events.append((status, detail))


class _FakeAPIClient:
    def __init__(self):
        self.user_post_calls: List[int] = []
        self.browser_calls = 0
        self.detail_calls: List[str] = []
        self.detail_call_kwargs: List[Dict[str, Any]] = []
        self.browser_call_kwargs: List[Dict[str, Any]] = []
        self.browser_post_items: Dict[str, Dict[str, Any]] = {}
        self.browser_post_stats: Dict[str, int] = {}
        self.homepage_screenshot_calls: List[tuple[str, Any, Dict[str, Any]]] = []

    async def get_user_info(self, sec_uid: str):
        return {"uid": "uid-1", "sec_uid": sec_uid, "nickname": "tester"}

    async def save_user_homepage_screenshot(self, sec_uid: str, save_path, *, profile=None):
        self.homepage_screenshot_calls.append((sec_uid, save_path, profile))
        return True

    async def get_user_post(self, _sec_uid: str, max_cursor: int = 0, _count: int = 20):
        self.user_post_calls.append(max_cursor)
        if max_cursor == 0:
            return {
                "status_code": 0,
                "aweme_list": [_make_aweme("111")],
                "has_more": 1,
                "max_cursor": 123,
                "not_login_module": {"guide_login_tip_exist": True},
            }
        return {"status_code": 0}

    async def collect_user_post_ids_via_browser(self, *_args, **_kwargs):
        self.browser_calls += 1
        self.browser_call_kwargs.append(dict(_kwargs))
        return ["111", "222", "333"]

    async def get_video_detail(self, aweme_id: str, **kwargs):
        self.detail_calls.append(aweme_id)
        self.detail_call_kwargs.append(kwargs)
        return _make_aweme(aweme_id)

    def pop_browser_post_aweme_items(self):
        data = self.browser_post_items
        self.browser_post_items = {}
        return data

    def pop_browser_post_stats(self):
        data = self.browser_post_stats
        self.browser_post_stats = {}
        return data


def _build_downloader(
    tmp_path,
    api_client,
    browser_enabled: bool,
    progress_reporter=None,
    number_post: int = 0,
    author_url: bool = False,
    homepage_screenshot: bool = False,
    author_dir: str = "nickname",
    increase_post: bool = True,
) -> UserDownloader:
    config_data = {
        "number": {"post": number_post},
        "increase": {"post": increase_post},
        "mode": ["post"],
        "thread": 2,
        "author_url": author_url,
        "homepage_screenshot": homepage_screenshot,
        "author_dir": author_dir,
        "browser_fallback": {
            "enabled": browser_enabled,
            "headless": True,
            "max_scrolls": 10,
            "idle_rounds": 2,
            "wait_timeout_seconds": 5,
        },
    }
    config = _FakeConfig(config_data)
    file_manager = FileManager(str(tmp_path / "Downloaded"))
    downloader = UserDownloader(
        config=config,
        api_client=api_client,
        file_manager=file_manager,
        cookie_manager=_FakeCookieManager(),
        database=None,
        rate_limiter=_NoopRateLimiter(),
        retry_handler=None,
        queue_manager=QueueManager(max_workers=2),
    )
    downloader.progress_reporter = progress_reporter
    return downloader


def test_increment_disabled_redownloads_existing_item(tmp_path, monkeypatch):
    aweme_id = "7412345678901234567"
    api_client = _FakeAPIClient()
    downloader = _build_downloader(
        tmp_path,
        api_client,
        browser_enabled=False,
        increase_post=False,
    )
    media_path = tmp_path / "Downloaded" / f"2026-08-21_demo_{aweme_id}.mp4"
    media_path.parent.mkdir(parents=True, exist_ok=True)
    media_path.write_bytes(b"existing-media")
    downloaded_ids: List[str] = []

    async def _record_download(item, *_args, **_kwargs):
        downloaded_ids.append(str(item.get("aweme_id")))
        return True

    monkeypatch.setattr(downloader, "_download_aweme_assets", _record_download)

    result = asyncio.run(
        downloader._download_mode_items(
            "post",
            [_make_aweme(aweme_id)],
            "tester",
        )
    )

    assert downloaded_ids == [aweme_id]
    assert result.success == 1
    assert result.skipped == 0


def test_unconfigured_mode_keeps_disk_dedupe(tmp_path, monkeypatch):
    aweme_id = "7412345678901234568"
    downloader = _build_downloader(tmp_path, _FakeAPIClient(), browser_enabled=False)
    downloader.config._data["increase"] = {}
    media_path = tmp_path / "Downloaded" / f"2026-08-21_demo_{aweme_id}.mp4"
    media_path.parent.mkdir(parents=True, exist_ok=True)
    media_path.write_bytes(b"existing-media")

    async def _unexpected_download(*_args, **_kwargs):
        raise AssertionError("unconfigured modes must keep disk dedupe")

    monkeypatch.setattr(downloader, "_download_aweme_assets", _unexpected_download)

    result = asyncio.run(
        downloader._download_mode_items(
            "collect",
            [_make_aweme(aweme_id)],
            "tester",
        )
    )

    assert result.skipped == 1
    assert result.success == 0


def test_user_post_browser_fallback_recovers_missing_pages(tmp_path, monkeypatch):
    api_client = _FakeAPIClient()
    downloader = _build_downloader(tmp_path, api_client, browser_enabled=True)

    async def _always_true(*_args, **_kwargs):
        return True

    monkeypatch.setattr(downloader, "_should_download", _always_true)
    monkeypatch.setattr(downloader, "_download_aweme_assets", _always_true)

    result = asyncio.run(
        downloader._download_user_post(
            "sec_uid_x",
            {"uid": "uid-1", "nickname": "tester", "aweme_count": 3},
        )
    )

    assert result.total == 3
    assert result.success == 3
    assert api_client.browser_calls == 1
    assert api_client.browser_call_kwargs[0].get("expected_count") == 0
    assert api_client.detail_calls == ["222", "333"]
    assert all(call.get("suppress_error") is True for call in api_client.detail_call_kwargs)


def test_user_post_browser_fallback_can_be_disabled(tmp_path, monkeypatch):
    api_client = _FakeAPIClient()
    downloader = _build_downloader(tmp_path, api_client, browser_enabled=False)

    async def _always_true(*_args, **_kwargs):
        return True

    monkeypatch.setattr(downloader, "_should_download", _always_true)
    monkeypatch.setattr(downloader, "_download_aweme_assets", _always_true)

    result = asyncio.run(
        downloader._download_user_post(
            "sec_uid_x",
            {"uid": "uid-1", "nickname": "tester", "aweme_count": 3},
        )
    )

    assert result.total == 1
    assert result.success == 1
    assert api_client.browser_calls == 0
    assert api_client.detail_calls == []
    assert api_client.detail_call_kwargs == []


def test_user_post_browser_fallback_prefers_browser_aweme_items(tmp_path, monkeypatch):
    api_client = _FakeAPIClient()
    api_client.browser_post_items = {
        "222": _make_aweme("222"),
        "333": _make_aweme("333"),
    }
    downloader = _build_downloader(tmp_path, api_client, browser_enabled=True)

    async def _always_true(*_args, **_kwargs):
        return True

    monkeypatch.setattr(downloader, "_should_download", _always_true)
    monkeypatch.setattr(downloader, "_download_aweme_assets", _always_true)

    result = asyncio.run(
        downloader._download_user_post(
            "sec_uid_x",
            {"uid": "uid-1", "nickname": "tester", "aweme_count": 3},
        )
    )

    assert result.total == 3
    assert result.success == 3
    assert api_client.detail_calls == []


def test_user_post_browser_fallback_expected_count_uses_number_limit(tmp_path, monkeypatch):
    api_client = _FakeAPIClient()
    downloader = _build_downloader(
        tmp_path,
        api_client,
        browser_enabled=True,
        number_post=2,
    )

    async def _always_true(*_args, **_kwargs):
        return True

    monkeypatch.setattr(downloader, "_should_download", _always_true)
    monkeypatch.setattr(downloader, "_download_aweme_assets", _always_true)

    result = asyncio.run(
        downloader._download_user_post(
            "sec_uid_x",
            {"uid": "uid-1", "nickname": "tester", "aweme_count": 999},
        )
    )

    assert result.total == 2
    assert api_client.browser_calls == 1
    assert api_client.browser_call_kwargs[0].get("expected_count") == 2


def test_user_post_skips_pinned_before_number_limit(tmp_path, monkeypatch):
    class _PinnedAPIClient(_FakeAPIClient):
        async def get_user_post(self, _sec_uid: str, max_cursor: int = 0, _count: int = 20):
            self.user_post_calls.append(max_cursor)
            if max_cursor == 0:
                return {
                    "status_code": 0,
                    "aweme_list": [
                        _make_aweme("111", is_top=1),
                        _make_aweme("222", is_top=1),
                        _make_aweme("333", is_top=0),
                    ],
                    "has_more": 1,
                    "max_cursor": 456,
                }
            return {
                "status_code": 0,
                "aweme_list": [_make_aweme("444", is_top=0)],
                "has_more": 0,
                "max_cursor": max_cursor,
            }

    api_client = _PinnedAPIClient()
    downloader = _build_downloader(
        tmp_path,
        api_client,
        browser_enabled=False,
        number_post=2,
    )

    downloaded_ids: List[str] = []

    async def _always_true(*_args, **_kwargs):
        return True

    async def _download_aweme_assets(item, *_args, **_kwargs):
        downloaded_ids.append(str(item.get("aweme_id")))
        return True

    monkeypatch.setattr(downloader, "_should_download", _always_true)
    monkeypatch.setattr(downloader, "_download_aweme_assets", _download_aweme_assets)

    result = asyncio.run(
        downloader._download_user_post(
            "sec_uid_x",
            {"uid": "uid-1", "nickname": "tester", "aweme_count": 4},
        )
    )

    assert result.total == 2
    assert result.success == 2
    assert downloaded_ids == ["333", "444"]


def test_user_post_reports_step_and_item_progress(tmp_path, monkeypatch):
    api_client = _FakeAPIClient()
    reporter = _FakeProgressReporter()
    downloader = _build_downloader(
        tmp_path,
        api_client,
        browser_enabled=True,
        progress_reporter=reporter,
    )

    async def _fake_should_download(aweme_id, **_kwargs):
        return aweme_id != "222"

    async def _fake_download_aweme_assets(item, *_args, **_kwargs):
        return item.get("aweme_id") != "333"

    monkeypatch.setattr(downloader, "_should_download", _fake_should_download)
    monkeypatch.setattr(downloader, "_download_aweme_assets", _fake_download_aweme_assets)

    result = asyncio.run(
        downloader._download_user_post(
            "sec_uid_x",
            {"uid": "uid-1", "nickname": "tester", "aweme_count": 3},
        )
    )

    assert result.total == 3
    assert result.success == 1
    assert result.skipped == 1
    assert result.failed == 1
    assert reporter.item_totals == [(3, "作品待下载")]
    assert ("下载作品", "待处理 3 条") in reporter.step_updates
    statuses = [status for status, _detail in reporter.item_events]
    assert statuses.count("success") == 1
    assert statuses.count("skipped") == 1
    assert statuses.count("failed") == 1


def test_homepage_artifacts_disabled_do_not_save(tmp_path, monkeypatch):
    api_client = _FakeAPIClient()
    downloader = _build_downloader(tmp_path, api_client, browser_enabled=False)

    async def _mode_result(*_args, **_kwargs):
        result = DownloadResult()
        result.total = 1
        result.success = 1
        return result

    monkeypatch.setattr(downloader, "_download_mode_logged", _mode_result)

    result = asyncio.run(downloader.download({"sec_uid": "sec_uid_x"}))

    assert result.success == 1
    assert api_client.homepage_screenshot_calls == []
    assert not (tmp_path / "Downloaded" / "tester" / "author_url.txt").exists()


def test_author_url_enabled_saves_without_screenshot(tmp_path, monkeypatch):
    api_client = _FakeAPIClient()
    downloader = _build_downloader(
        tmp_path,
        api_client,
        browser_enabled=False,
        author_url=True,
    )

    async def _mode_result(*_args, **_kwargs):
        return DownloadResult()

    monkeypatch.setattr(downloader, "_download_mode_logged", _mode_result)

    asyncio.run(downloader.download({"sec_uid": "sec_uid_x"}))

    assert api_client.homepage_screenshot_calls == []
    author_url_path = tmp_path / "Downloaded" / "tester" / "author_url.txt"
    assert author_url_path.read_text(encoding="utf-8") == (
        "https://www.douyin.com/user/sec_uid_x\n"
    )


def test_homepage_screenshot_uses_configured_author_root(tmp_path, monkeypatch):
    api_client = _FakeAPIClient()
    downloader = _build_downloader(
        tmp_path,
        api_client,
        browser_enabled=False,
        homepage_screenshot=True,
        author_dir="nickname_uid",
    )

    async def _mode_result(*_args, **_kwargs):
        return DownloadResult()

    monkeypatch.setattr(downloader, "_download_mode_logged", _mode_result)

    asyncio.run(downloader.download({"sec_uid": "sec_uid_x"}))

    assert len(api_client.homepage_screenshot_calls) == 1
    screenshot_sec_uid, screenshot_path, screenshot_profile = api_client.homepage_screenshot_calls[
        0
    ]
    assert screenshot_sec_uid == "sec_uid_x"
    assert screenshot_profile == {"uid": "uid-1", "sec_uid": "sec_uid_x", "nickname": "tester"}
    author_root = tmp_path / "Downloaded" / "tester_sec_uid_x"
    assert screenshot_path == (author_root / "主页截图.png").resolve()
    assert not (author_root / "author_url.txt").exists()


def test_author_url_overwrites_existing_file(tmp_path):
    downloader = _build_downloader(
        tmp_path,
        _FakeAPIClient(),
        browser_enabled=False,
        author_url=True,
        author_dir="nickname_uid",
    )
    target = tmp_path / "Downloaded" / "tester_sec_uid_x" / "author_url.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("stale\n", encoding="utf-8")

    asyncio.run(
        downloader._save_author_home_url(
            "sec_uid_x",
            {"sec_uid": "sec_uid_x", "nickname": "tester"},
            ["post"],
        )
    )

    assert target.read_text(encoding="utf-8") == ("https://www.douyin.com/user/sec_uid_x\n")


def test_author_url_skips_collect_only_context(tmp_path):
    downloader = _build_downloader(
        tmp_path,
        _FakeAPIClient(),
        browser_enabled=False,
        author_url=True,
    )

    for mode in ("collect", "collectmix"):
        asyncio.run(
            downloader._save_author_home_url(
                "sec_uid_x",
                {"sec_uid": "sec_uid_x", "nickname": "tester"},
                [mode],
            )
        )
        assert not (tmp_path / "Downloaded" / "tester" / "author_url.txt").exists()


def test_author_url_write_failure_does_not_raise(tmp_path, monkeypatch, caplog):
    downloader = _build_downloader(
        tmp_path,
        _FakeAPIClient(),
        browser_enabled=False,
        author_url=True,
    )

    def _fail_open(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("core.user_downloader.aiofiles.open", _fail_open)
    monkeypatch.setattr(logging.getLogger("UserDownloader"), "propagate", True)
    with caplog.at_level(logging.WARNING, logger="UserDownloader"):
        asyncio.run(
            downloader._save_author_home_url(
                "sec_uid_x",
                {"sec_uid": "sec_uid_x", "nickname": "tester"},
                ["post"],
            )
        )

    assert "Author homepage URL failed" in caplog.text


def test_homepage_screenshot_failure_does_not_fail_download(tmp_path, monkeypatch):
    api_client = _FakeAPIClient()
    downloader = _build_downloader(
        tmp_path,
        api_client,
        browser_enabled=False,
        homepage_screenshot=True,
    )

    async def _screenshot_failure(*_args, **_kwargs):
        raise RuntimeError("browser unavailable")

    async def _mode_result(*_args, **_kwargs):
        result = DownloadResult()
        result.total = 2
        result.success = 2
        return result

    monkeypatch.setattr(api_client, "save_user_homepage_screenshot", _screenshot_failure)
    monkeypatch.setattr(downloader, "_download_mode_logged", _mode_result)

    result = asyncio.run(downloader.download({"sec_uid": "sec_uid_x"}))

    assert result.total == 2
    assert result.success == 2
    assert result.failed == 0


def test_homepage_screenshot_skips_collect_context(tmp_path):
    api_client = _FakeAPIClient()
    downloader = _build_downloader(
        tmp_path,
        api_client,
        browser_enabled=False,
        homepage_screenshot=True,
    )

    asyncio.run(
        downloader._save_homepage_screenshot(
            "sec_uid_x",
            {"sec_uid": "sec_uid_x", "nickname": "tester"},
            ["collect"],
        )
    )

    assert api_client.homepage_screenshot_calls == []


def test_browser_recovery_reports_missing_backend(tmp_path, monkeypatch):
    """发行版裁掉了 playwright，回补必须明说不可用而不是静默空转。"""
    api_client = _FakeAPIClient()
    reporter = _FakeProgressReporter()
    downloader = _build_downloader(
        tmp_path,
        api_client,
        browser_enabled=True,
        progress_reporter=reporter,
    )
    monkeypatch.setattr(downloader, "_browser_backend_missing", lambda: True)
    # 发行版 = frozen 运行时，提示语才是「未内置」（源码运行时是「未安装」）。
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    asyncio.run(
        downloader._recover_user_post_with_browser(
            "sec_uid_x",
            {"sec_uid": "sec_uid_x", "nickname": "tester"},
            [_make_aweme("111")],
        )
    )

    assert api_client.browser_calls == 0
    details = "\n".join(detail for _step, detail in reporter.step_updates)
    assert "浏览器回补不可用" in details
    assert "未内置浏览器组件" in details


def test_browser_backend_missing_only_applies_to_real_api_client(tmp_path, monkeypatch):
    from core.api_client import DouyinAPIClient

    monkeypatch.setattr("core.user_downloader.find_spec", lambda _name: None)

    fake_client = _build_downloader(tmp_path, _FakeAPIClient(), browser_enabled=True)
    assert fake_client._browser_backend_missing() is False

    real_client = _build_downloader(tmp_path, DouyinAPIClient({}), browser_enabled=True)
    assert real_client._browser_backend_missing() is True

    monkeypatch.setattr("core.user_downloader.find_spec", lambda _name: object())
    assert real_client._browser_backend_missing() is False


def test_browser_recovery_unavailable_reason_reports_disabled_setting(tmp_path):
    downloader = _build_downloader(tmp_path, _FakeAPIClient(), browser_enabled=False)
    assert downloader._browser_recovery_unavailable_reason() == "已在设置中关闭"


def test_incomplete_reason_reaches_download_result(tmp_path, monkeypatch):
    """列表被硬截断时，计数字段全是 0 失败，只能靠 incomplete_reason 上报。"""

    class _TruncatedStrategy:
        incomplete_reason = "第 2 页请求失败（可能被限流），作品列表不完整，请稍后重试"

        async def download_mode(self, _sec_uid, _user_info, seen_aweme_ids=None):
            result = DownloadResult()
            result.total = 1
            result.success = 1
            return result

    downloader = _build_downloader(tmp_path, _FakeAPIClient(), browser_enabled=False)
    monkeypatch.setattr(downloader, "_get_mode_strategy", lambda _mode: _TruncatedStrategy())

    mode_result = asyncio.run(
        downloader._download_mode_logged("post", "sec_uid_x", {"nickname": "tester"}, set())
    )
    merged = DownloadResult()
    UserDownloader._merge_result(merged, mode_result)

    assert mode_result.incomplete_reason == _TruncatedStrategy.incomplete_reason
    assert merged.incomplete_reason == _TruncatedStrategy.incomplete_reason
    assert merged.failed == 0


class _PostThenFailingLikeAPI:
    """post 正常走完，like 第 1 页请求失败（``_request_json`` 重试耗尽回 ``{}``）。"""

    def __init__(self):
        self.like_calls = 0

    async def get_user_info(self, sec_uid: str):
        return {"uid": "uid-1", "sec_uid": sec_uid, "nickname": "tester"}

    async def get_user_post(self, _sec_uid: str, max_cursor: int = 0, count: int = 20):
        return DouyinAPIClient._normalize_paged_response(
            {
                "status_code": 0,
                "aweme_list": [_make_aweme("111")],
                "has_more": 0,
                "max_cursor": 0,
            },
            item_keys=["aweme_list"],
        )

    async def get_user_like(self, _sec_uid: str, max_cursor: int = 0, count: int = 20):
        self.like_calls += 1
        return DouyinAPIClient._normalize_paged_response({}, item_keys=["aweme_list"])


def _run_multi_mode_download(tmp_path, monkeypatch, api_client, modes):
    downloader = _build_downloader(tmp_path, api_client, browser_enabled=False)
    downloader.config._data["mode"] = modes

    async def _always_true(*_args, **_kwargs):
        return True

    monkeypatch.setattr(downloader, "_should_download", _always_true)
    monkeypatch.setattr(downloader, "_download_aweme_assets", _always_true)
    return asyncio.run(downloader.download({"sec_uid": "sec_uid_x"}))


def test_later_mode_page_failure_keeps_earlier_mode_counts(tmp_path, monkeypatch):
    """like 翻页失败不能把 post 已经下好的作品一起丢掉。

    异常逃出 ``download()`` 时 server/jobs.py 只走异常分支，计数字段全留 0
    （docs/spec/gotchas.md「计数器恒为 0」），用户会在文件已落盘时看到「失败 · 0 项」。
    """
    api_client = _PostThenFailingLikeAPI()

    result = _run_multi_mode_download(tmp_path, monkeypatch, api_client, ["post", "like"])

    assert result.total == 1 and result.success == 1
    assert api_client.like_calls == 1
    assert result.incomplete_reason and "喜欢列表" in result.incomplete_reason


def test_first_mode_page_failure_still_fails_the_job(tmp_path, monkeypatch):
    """没有任何成果时不能装成「成功 0 项」，异常照旧抛出去按失败结案。"""
    api_client = _PostThenFailingLikeAPI()

    with pytest.raises(PageRequestFailedError):
        _run_multi_mode_download(tmp_path, monkeypatch, api_client, ["like"])


def test_browser_backend_missing_reason_matches_the_runtime(tmp_path, monkeypatch):
    """打包版裁掉了浏览器组件（装不了），源码 / CLI 只是没装可选依赖。"""
    downloader = _build_downloader(tmp_path, _FakeAPIClient(), browser_enabled=True)

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert downloader._browser_backend_missing_reason() == "当前版本未内置浏览器组件"

    monkeypatch.delattr(sys, "frozen", raising=False)
    assert "playwright" in downloader._browser_backend_missing_reason()
