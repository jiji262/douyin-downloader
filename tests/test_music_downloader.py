import pytest

from auth import CookieManager
from config import ConfigLoader
from control import QueueManager, RateLimiter, RetryHandler
from core.music_downloader import MusicDownloader
from storage import FileManager


class _FakeAPIClient:
    BASE_URL = "https://www.douyin.com"
    headers = {"User-Agent": "UnitTestAgent/1.0"}

    async def get_music_detail(self, _music_id: str):
        return {
            "title": "test-music",
            "author_name": "test-author",
            "play_url": {"url_list": ["https://example.com/music.mp3"]},
        }

    async def get_session(self):
        return object()


@pytest.mark.asyncio
async def test_music_downloader_downloads_music_asset(tmp_path, monkeypatch):
    config = ConfigLoader()
    config.update(path=str(tmp_path), cover=False, json=False)
    file_manager = FileManager(str(tmp_path))
    downloader = MusicDownloader(
        config=config,
        api_client=_FakeAPIClient(),
        file_manager=file_manager,
        cookie_manager=CookieManager(str(tmp_path / ".cookies.json")),
        database=None,
        rate_limiter=RateLimiter(max_per_second=10),
        retry_handler=RetryHandler(max_retries=1),
        queue_manager=QueueManager(max_workers=1),
    )

    saved_paths = []

    async def _fake_download_with_retry(self, _url, save_path, _session, **_kwargs):
        saved_paths.append(save_path)
        return True

    monkeypatch.setattr(
        downloader,
        "_download_with_retry",
        _fake_download_with_retry.__get__(downloader, MusicDownloader),
    )

    result = await downloader.download({"music_id": "7600224486650121999"})

    assert result.total == 1
    assert result.success == 1
    assert any(path.suffix == ".mp3" for path in saved_paths)


@pytest.mark.asyncio
async def test_music_downloader_uses_extension_from_music_url(tmp_path, monkeypatch):
    class _FakeM4AAPIClient(_FakeAPIClient):
        async def get_music_detail(self, _music_id: str):
            return {
                "title": "test-music",
                "author_name": "test-author",
                "play_url": {"url_list": ["https://example.com/music_track.m4a?x=1"]},
            }

    config = ConfigLoader()
    config.update(path=str(tmp_path), cover=False, json=False)
    file_manager = FileManager(str(tmp_path))
    downloader = MusicDownloader(
        config=config,
        api_client=_FakeM4AAPIClient(),
        file_manager=file_manager,
        cookie_manager=CookieManager(str(tmp_path / ".cookies.json")),
        database=None,
        rate_limiter=RateLimiter(max_per_second=10),
        retry_handler=RetryHandler(max_retries=1),
        queue_manager=QueueManager(max_workers=1),
    )

    saved_paths = []

    async def _fake_download_with_retry(self, _url, save_path, _session, **_kwargs):
        saved_paths.append(save_path)
        return True

    monkeypatch.setattr(
        downloader,
        "_download_with_retry",
        _fake_download_with_retry.__get__(downloader, MusicDownloader),
    )

    result = await downloader.download({"music_id": "7600224486650122000"})

    assert result.success == 1
    assert any(path.suffix == ".m4a" for path in saved_paths)
