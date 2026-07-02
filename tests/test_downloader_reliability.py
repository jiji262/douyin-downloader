"""Regression tests for download reliability against real-world payloads.

Covers:
  * null-safe handling of ``music`` / ``video`` / ``author`` fields that
    Douyin frequently serves as an explicit JSON ``null`` (not just missing);
  * the inclusive ``end_time`` date filter (the end day must be kept).
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest


def _build_video_downloader(tmp_path):
    from auth import CookieManager
    from config import ConfigLoader
    from control import QueueManager, RateLimiter, RetryHandler
    from core.api_client import DouyinAPIClient
    from core.video_downloader import VideoDownloader
    from storage import FileManager

    config = ConfigLoader()
    config.update(path=str(tmp_path))
    return VideoDownloader(
        config,
        DouyinAPIClient({}),
        FileManager(str(tmp_path)),
        CookieManager(str(tmp_path / ".cookies.json")),
        database=None,
        rate_limiter=RateLimiter(max_per_second=5),
        retry_handler=RetryHandler(max_retries=1),
        queue_manager=QueueManager(max_workers=1),
    )


def test_build_no_watermark_url_handles_null_video(tmp_path):
    """A payload with ``"video": null`` must resolve to "no URL" (None) rather
    than raising AttributeError on the ``video.get(...)`` fallbacks."""
    downloader = _build_video_downloader(tmp_path)
    assert downloader._build_no_watermark_url({"video": None}) is None


@pytest.mark.asyncio
async def test_download_assets_survives_null_music_and_author(tmp_path):
    """``music`` and ``author`` are commonly ``null`` in real payloads. The
    asset pipeline must download the video and finish cleanly instead of
    crashing after the media already landed on disk."""
    downloader = _build_video_downloader(tmp_path)
    downloader.config.update(cover=False, json=False, avatar=True, music=True)

    # Isolate from real network / disk side effects.
    downloader.file_manager.download_file = AsyncMock(return_value=True)
    downloader.metadata_handler.append_download_manifest = AsyncMock(return_value=None)
    downloader.api_client.get_session = AsyncMock(return_value=MagicMock())

    aweme_data = {
        "aweme_id": "7123456789012345678",
        "desc": "hello world",
        "create_time": 1700000000,
        "video": {
            "play_addr": {"uri": "v1", "url_list": ["https://cdn.example.com/v.mp4"]}
        },
        "music": None,
        "author": None,
    }

    result = await downloader._download_aweme_assets(aweme_data, "unknown")
    assert result is True
    downloader.file_manager.download_file.assert_awaited()


def test_filter_by_time_end_date_is_inclusive(tmp_path):
    """``end_time`` is a date; the whole end day must be kept. A post at noon
    on the end date used to be dropped because the bound was midnight."""
    downloader = _build_video_downloader(tmp_path)
    downloader.config.update(start_time="2024-01-31", end_time="2024-01-31")

    on_end_day = int(datetime(2024, 1, 31, 12, 0, 0).timestamp())
    before_start = int(datetime(2024, 1, 30, 12, 0, 0).timestamp())
    after_end = int(datetime(2024, 2, 1, 0, 30, 0).timestamp())

    items = [
        {"aweme_id": "a", "create_time": on_end_day},
        {"aweme_id": "b", "create_time": before_start},
        {"aweme_id": "c", "create_time": after_end},
    ]

    filtered = downloader._filter_by_time(items)
    assert [i["aweme_id"] for i in filtered] == ["a"]
