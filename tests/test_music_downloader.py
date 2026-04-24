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


@pytest.mark.asyncio
async def test_music_downloader_falls_back_to_first_aweme_when_direct_audio_missing(
    tmp_path, monkeypatch
):
    class _FallbackAPIClient(_FakeAPIClient):
        async def get_music_detail(self, _music_id: str):
            return {
                "title": "fallback-music",
                "author_name": "fallback-author",
            }

        async def get_music_aweme(self, _music_id: str, cursor: int = 0, count: int = 1):
            assert cursor == 0
            assert count == 1
            return {
                "items": [
                    {
                        "aweme_id": "fallback-aweme-1",
                        "author": {"nickname": "fallback-author"},
                        "video": {
                            "play_addr": {"url_list": ["https://example.com/video.mp4"]}
                        },
                    }
                ]
            }

    config = ConfigLoader()
    config.update(path=str(tmp_path), cover=False, json=False)
    file_manager = FileManager(str(tmp_path))
    downloader = MusicDownloader(
        config=config,
        api_client=_FallbackAPIClient(),
        file_manager=file_manager,
        cookie_manager=CookieManager(str(tmp_path / ".cookies.json")),
        database=None,
        rate_limiter=RateLimiter(max_per_second=10),
        retry_handler=RetryHandler(max_retries=1),
        queue_manager=QueueManager(max_workers=1),
    )

    downloaded_awemes = []

    async def _fake_should_download(self, _aweme_id: str):
        return True

    async def _fake_download_aweme_assets(self, aweme_data, author_name, mode=None):
        downloaded_awemes.append((aweme_data["aweme_id"], author_name, mode))
        return True

    monkeypatch.setattr(
        downloader,
        "_should_download",
        _fake_should_download.__get__(downloader, MusicDownloader),
    )
    monkeypatch.setattr(
        downloader,
        "_download_aweme_assets",
        _fake_download_aweme_assets.__get__(downloader, MusicDownloader),
    )

    result = await downloader.download({"music_id": "7600224486650122001"})

    assert result.total == 1
    assert result.success == 1
    assert downloaded_awemes == [("fallback-aweme-1", "fallback-author", "music")]
