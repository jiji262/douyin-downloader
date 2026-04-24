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


class _FakeAPIClient:
    def __init__(self):
        self.user_info_calls = []
        self.collect_calls = 0
        self.collect_mix_calls = 0

    async def get_user_info(self, _sec_uid: str):
        self.user_info_calls.append(_sec_uid)
        return {"uid": "uid-1", "nickname": "tester", "aweme_count": 99}

    async def get_user_post(self, _sec_uid: str, max_cursor: int = 0, count: int = 20):
        if max_cursor > 0:
            return {"items": [], "has_more": False, "max_cursor": max_cursor, "status_code": 0}
        return {
            "items": [_make_aweme("111"), _make_aweme("222")],
            "has_more": False,
            "max_cursor": 0,
            "status_code": 0,
        }

    async def get_user_like(self, _sec_uid: str, max_cursor: int = 0, count: int = 20):
        if max_cursor > 0:
            return {"items": [], "has_more": False, "max_cursor": max_cursor, "status_code": 0}
        return {
            "items": [_make_aweme("222"), _make_aweme("333")],
            "has_more": False,
            "max_cursor": 0,
            "status_code": 0,
        }

    async def get_user_mix(self, _sec_uid: str, max_cursor: int = 0, count: int = 20):
        return {
            "items": [_make_aweme("444")],
            "has_more": False,
            "max_cursor": 0,
            "status_code": 0,
        }

    async def get_user_music(self, _sec_uid: str, max_cursor: int = 0, count: int = 20):
        return {
            "items": [_make_aweme("555")],
            "has_more": False,
            "max_cursor": 0,
            "status_code": 0,
        }

    async def get_user_collects(
        self, _sec_uid: str, max_cursor: int = 0, count: int = 20
    ):
        self.collect_calls += 1
        return {
            "items": [{"collects_id_str": "collect-1", "collects_name": "默认收藏夹"}],
            "has_more": False,
            "max_cursor": 0,
            "status_code": 0,
        }

    async def get_collect_aweme(
        self, collects_id: str, max_cursor: int = 0, count: int = 20
    ):
        assert collects_id == "collect-1"
        return {
            "items": [_make_aweme("666")],
            "has_more": False,
            "max_cursor": 0,
            "status_code": 0,
        }


def _build_downloader(tmp_path, mode: List[str]) -> UserDownloader:
    config_data = {
        "number": {"post": 0, "like": 0, "mix": 0, "music": 0},
        "increase": {"post": False, "like": False, "mix": False, "music": False},
        "mode": mode,
        "thread": 2,
        "browser_fallback": {"enabled": False},
    }
    config = _FakeConfig(config_data)
    file_manager = FileManager(str(tmp_path / "Downloaded"))
    downloader = UserDownloader(
        config=config,
        api_client=_FakeAPIClient(),
        file_manager=file_manager,
        cookie_manager=_FakeCookieManager(),
        database=None,
        rate_limiter=_NoopRateLimiter(),
        retry_handler=None,
        queue_manager=QueueManager(max_workers=2),
    )
    return downloader


def test_user_downloader_processes_modes_and_deduplicates_across_modes(
    tmp_path, monkeypatch
):
    downloader = _build_downloader(tmp_path, mode=["post", "like"])

    async def _always_true(*_args, **_kwargs):
        return True

    async def _download_ok(*_args, **_kwargs):
        return True

    monkeypatch.setattr(downloader, "_should_download", _always_true)
    monkeypatch.setattr(downloader, "_download_aweme_assets", _download_ok)

    result = asyncio.run(downloader.download({"sec_uid": "sec_uid_x"}))

    # 去重后应仅 3 条（111,222,333）
    assert result.total == 3
    assert result.success == 3
    assert result.failed == 0
    assert result.skipped == 0


def test_user_downloader_supports_mix_and_music_modes(tmp_path, monkeypatch):
    downloader = _build_downloader(tmp_path, mode=["mix", "music"])

    async def _always_true(*_args, **_kwargs):
        return True

    async def _download_ok(*_args, **_kwargs):
        return True

    monkeypatch.setattr(downloader, "_should_download", _always_true)
    monkeypatch.setattr(downloader, "_download_aweme_assets", _download_ok)

    result = asyncio.run(downloader.download({"sec_uid": "sec_uid_x"}))

    assert result.total == 2
    assert result.success == 2


def test_user_downloader_supports_self_collect_mode(tmp_path, monkeypatch):
    downloader = _build_downloader(tmp_path, mode=["collect"])

    async def _always_true(*_args, **_kwargs):
        return True

    async def _download_ok(*_args, **_kwargs):
        return True

    monkeypatch.setattr(downloader, "_should_download", _always_true)
    monkeypatch.setattr(downloader, "_download_aweme_assets", _download_ok)

    result = asyncio.run(downloader.download({"sec_uid": "self"}))

    assert result.total == 1
    assert result.success == 1
    assert downloader.api_client.user_info_calls == []


def test_user_downloader_rejects_non_self_collect_mode(tmp_path, monkeypatch):
    downloader = _build_downloader(tmp_path, mode=["collect"])

    async def _always_true(*_args, **_kwargs):
        return True

    async def _download_ok(*_args, **_kwargs):
        return True

    monkeypatch.setattr(downloader, "_should_download", _always_true)
    monkeypatch.setattr(downloader, "_download_aweme_assets", _download_ok)

    result = asyncio.run(downloader.download({"sec_uid": "sec_uid_x"}))

    assert result.total == 0
    assert result.success == 0
    assert downloader.api_client.user_info_calls == []
    assert downloader.api_client.collect_calls == 0


def test_user_downloader_rejects_mixed_self_collect_and_regular_modes(
    tmp_path, monkeypatch
):
    downloader = _build_downloader(tmp_path, mode=["collect", "post"])

    async def _always_true(*_args, **_kwargs):
        return True

    async def _download_ok(*_args, **_kwargs):
        return True

    monkeypatch.setattr(downloader, "_should_download", _always_true)
    monkeypatch.setattr(downloader, "_download_aweme_assets", _download_ok)

    result = asyncio.run(downloader.download({"sec_uid": "self"}))

    assert result.total == 0
    assert result.success == 0
    assert downloader.api_client.user_info_calls == []
    assert downloader.api_client.collect_calls == 0
