import asyncio
from typing import Any, Dict, List

from control.queue_manager import QueueManager
from core.user_downloader import UserDownloader
from storage.file_manager import FileManager


def _make_aweme(aweme_id: str) -> Dict[str, Any]:
    return {
        "aweme_id": aweme_id,
        "desc": f"desc-{aweme_id}",
        "create_time": 1700000000,
        "author": {"nickname": "tester", "uid": "uid-1"},
        "video": {"play_addr": {"url_list": ["https://example.com/video.mp4"]}},
    }


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
) -> UserDownloader:
    config_data = {
        "number": {"post": number_post},
        "increase": {"post": False},
        "mode": ["post"],
        "thread": 2,
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


def test_user_post_browser_fallback_expected_count_uses_number_limit(
    tmp_path, monkeypatch
):
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


def test_user_post_reports_step_and_item_progress(tmp_path, monkeypatch):
    api_client = _FakeAPIClient()
    reporter = _FakeProgressReporter()
    downloader = _build_downloader(
        tmp_path,
        api_client,
        browser_enabled=True,
        progress_reporter=reporter,
    )

    async def _fake_should_download(aweme_id):
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
