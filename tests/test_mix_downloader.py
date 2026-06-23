import pytest

from auth import CookieManager
from config import ConfigLoader
from control import QueueManager, RateLimiter, RetryHandler
from core.mix_downloader import MixDownloader
from storage import FileManager


class _FakeAPIClient:
    async def get_mix_aweme(self, _mix_id: str, cursor: int = 0, count: int = 20):
        if cursor > 0:
            return {"items": [], "has_more": False, "max_cursor": cursor, "status_code": 0}
        return {
            "items": [
                {
                    "aweme_id": "7600224486650121888",
                    "desc": "mix-item",
                    "author": {"nickname": "mix-author"},
                    "video": {"play_addr": {"url_list": ["https://example.com/video.mp4"]}},
                }
            ],
            "has_more": False,
            "max_cursor": 0,
            "status_code": 0,
        }

    async def get_mix_detail(self, _mix_id: str):
        return {"author": {"nickname": "mix-author"}}


@pytest.mark.asyncio
async def test_mix_downloader_downloads_mix_items(tmp_path, monkeypatch):
    config = ConfigLoader()
    config.update(path=str(tmp_path), number={"mix": 0})
    file_manager = FileManager(str(tmp_path))
    downloader = MixDownloader(
        config=config,
        api_client=_FakeAPIClient(),
        file_manager=file_manager,
        cookie_manager=CookieManager(str(tmp_path / ".cookies.json")),
        database=None,
        rate_limiter=RateLimiter(max_per_second=10),
        retry_handler=RetryHandler(max_retries=1),
        queue_manager=QueueManager(max_workers=1),
    )

    async def _always_true(*_args, **_kwargs):
        return True

    monkeypatch.setattr(downloader, "_should_download", _always_true)
    monkeypatch.setattr(downloader, "_download_aweme_assets", _always_true)

    result = await downloader.download({"mix_id": "123"})

    assert result.total == 1
    assert result.success == 1
    assert result.failed == 0


@pytest.mark.asyncio
async def test_mix_downloader_does_not_apply_redundant_limit_count(tmp_path, monkeypatch):
    config = ConfigLoader()
    config.update(path=str(tmp_path), number={"mix": 0})
    file_manager = FileManager(str(tmp_path))
    downloader = MixDownloader(
        config=config,
        api_client=_FakeAPIClient(),
        file_manager=file_manager,
        cookie_manager=CookieManager(str(tmp_path / ".cookies.json")),
        database=None,
        rate_limiter=RateLimiter(max_per_second=10),
        retry_handler=RetryHandler(max_retries=1),
        queue_manager=QueueManager(max_workers=1),
    )

    async def _always_true(*_args, **_kwargs):
        return True

    call_count = {"limit": 0}

    def _track_limit(items, _mode):
        call_count["limit"] += 1
        return items

    monkeypatch.setattr(downloader, "_should_download", _always_true)
    monkeypatch.setattr(downloader, "_download_aweme_assets", _always_true)
    monkeypatch.setattr(downloader, "_limit_count", _track_limit)

    result = await downloader.download({"mix_id": "123"})

    assert result.total == 1
    assert call_count["limit"] == 0
