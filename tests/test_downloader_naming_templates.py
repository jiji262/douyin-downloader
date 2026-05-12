"""End-to-end check that custom `filename_template` / `folder_template` /
`folderstyle` settings actually change the on-disk paths a downloader writes
to.

This complements `test_naming.py` (unit tests for the helper) and
`test_server_settings_naming.py` (PATCH/GET round-trips). Here we exercise the
real `_download_aweme_assets` code path with a monkey-patched `_download_with_retry`
so no network I/O happens.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from auth import CookieManager
from config import ConfigLoader
from control import QueueManager, RateLimiter, RetryHandler
from core.api_client import DouyinAPIClient
from core.video_downloader import VideoDownloader
from storage import FileManager


def _build_downloader(tmp_path):
    config = ConfigLoader()
    config.update(path=str(tmp_path))

    file_manager = FileManager(str(tmp_path))
    cookie_manager = CookieManager(str(tmp_path / ".cookies.json"))
    api_client = DouyinAPIClient({})

    downloader = VideoDownloader(
        config,
        api_client,
        file_manager,
        cookie_manager,
        database=None,
        rate_limiter=RateLimiter(max_per_second=5),
        retry_handler=RetryHandler(max_retries=1),
        queue_manager=QueueManager(max_workers=1),
    )

    return downloader, api_client


def _make_aweme(publish_ts: int, aweme_id: str = "7600000000000000000"):
    return {
        "aweme_id": aweme_id,
        "desc": "登山日记",
        "create_time": publish_ts,
        "author": {"nickname": "爬山佬", "sec_uid": "MS4wLjABAAA"},
        "video": {"play_addr": {"url_list": ["https://example.com/v.mp4"]}},
    }


@pytest.mark.asyncio
async def test_default_template_matches_legacy_filename(tmp_path, monkeypatch):
    """Users who don't touch the new settings see zero behaviour change."""
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(music=False, cover=False, avatar=False, json=False, folderstyle=True)

    saved = []

    async def _fake_download(self, _url, save_path, _session, **_):
        saved.append(save_path)
        return True

    downloader._download_with_retry = _fake_download.__get__(downloader, VideoDownloader)

    async def _fake_session():
        return object()

    monkeypatch.setattr(api_client, "get_session", _fake_session)

    publish_ts = int(datetime(2024, 3, 15, 18, 30).timestamp())
    aweme = _make_aweme(publish_ts, aweme_id="7412345678901234567")

    assert await downloader._download_aweme_assets(aweme, author_name="爬山佬", mode="post") is True

    assert len(saved) == 1
    save_path = saved[0]
    assert save_path.name == "2024-03-15_登山日记_7412345678901234567.mp4"
    assert save_path.parent.name == "2024-03-15_登山日记_7412345678901234567"

    await api_client.close()


@pytest.mark.asyncio
async def test_custom_filename_template_applies(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(
        music=False,
        cover=False,
        avatar=False,
        json=False,
        folderstyle=True,
        filename_template="{author}_{date}_{id}",
        folder_template="{year}-{month}_{id}",
    )

    saved = []

    async def _fake_download(self, _url, save_path, _session, **_):
        saved.append(save_path)
        return True

    downloader._download_with_retry = _fake_download.__get__(downloader, VideoDownloader)

    async def _fake_session():
        return object()

    monkeypatch.setattr(api_client, "get_session", _fake_session)

    publish_ts = int(datetime(2024, 7, 4, 9, 15).timestamp())
    aweme = _make_aweme(publish_ts, aweme_id="7419999999999999999")

    assert await downloader._download_aweme_assets(aweme, author_name="爬山佬", mode="post") is True

    assert len(saved) == 1
    save_path = saved[0]
    assert save_path.name == "爬山佬_2024-07-04_7419999999999999999.mp4"
    assert save_path.parent.name == "2024-07_7419999999999999999"

    await api_client.close()


@pytest.mark.asyncio
async def test_folderstyle_false_skips_subdirectory(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(
        music=False,
        cover=False,
        avatar=False,
        json=False,
        folderstyle=False,
    )

    saved = []

    async def _fake_download(self, _url, save_path, _session, **_):
        saved.append(save_path)
        return True

    downloader._download_with_retry = _fake_download.__get__(downloader, VideoDownloader)

    async def _fake_session():
        return object()

    monkeypatch.setattr(api_client, "get_session", _fake_session)

    publish_ts = int(datetime(2024, 1, 1, 0, 0).timestamp())
    aweme = _make_aweme(publish_ts, aweme_id="7401010101010101010")

    assert await downloader._download_aweme_assets(aweme, author_name="爬山佬", mode="post") is True

    # With folderstyle=False the save_dir is just `{base}/{author}/{mode}`
    # — no per-aweme subdir.
    save_path = saved[0]
    assert save_path.parent.name == "post"
    assert save_path.parent.parent.name == "爬山佬"

    await api_client.close()
