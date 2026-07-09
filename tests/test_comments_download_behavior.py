"""评论采集与已下载媒体的交互回归测试。"""

from __future__ import annotations

import json

import pytest

from auth import CookieManager
from config import ConfigLoader
from control import QueueManager, RateLimiter, RetryHandler
from core.mix_downloader import MixDownloader
from core.music_downloader import MusicDownloader
from core.user_downloader import UserDownloader
from core.video_downloader import VideoDownloader
from storage import FileManager


class _FakeAPIClient:
    def __init__(self, aweme_data):
        self.aweme_data = aweme_data
        self.headers = {"User-Agent": "pytest"}
        self.proxy = None
        self.detail_calls = 0
        self.comment_calls = 0

    async def get_video_detail(self, aweme_id: str):
        self.detail_calls += 1
        assert aweme_id == self.aweme_data["aweme_id"]
        return self.aweme_data

    async def get_aweme_comments(self, aweme_id, *, cursor, count, include_replies):
        self.comment_calls += 1
        assert aweme_id == self.aweme_data["aweme_id"]
        assert cursor == 0
        assert count == 20
        assert include_replies is False
        return {
            "items": [{"cid": "c1", "text": "已下载后补采评论"}],
            "has_more": False,
            "max_cursor": 0,
        }

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_video_download_collects_comments_when_media_is_already_downloaded(
    tmp_path, monkeypatch
):
    aweme_id = "7600224486650121526"
    # 先制造一个已存在的媒体文件，让 VideoDownloader 走原本的 skipped 分支。
    (tmp_path / f"existing_{aweme_id}.mp4").write_bytes(b"downloaded")

    config = ConfigLoader(None)
    config.update(
        path=str(tmp_path),
        comments={"enabled": True, "include_replies": False, "max_comments": 0, "page_size": 20},
        cover=False,
        music=False,
        avatar=False,
        json=False,
        transcript={"enabled": False},
    )
    api_client = _FakeAPIClient(
        {
            "aweme_id": aweme_id,
            "desc": "评论补采",
            "create_time": 1707303025,
            "author": {"nickname": "测试作者"},
            "video": {"play_addr": {"url_list": ["https://example.com/video.mp4"]}},
        }
    )
    downloader = VideoDownloader(
        config,
        api_client,
        FileManager(str(tmp_path)),
        CookieManager(str(tmp_path / ".cookies.json")),
        database=None,
        rate_limiter=RateLimiter(max_per_second=100),
        retry_handler=RetryHandler(max_retries=1),
        queue_manager=QueueManager(max_workers=1),
    )

    async def _unexpected_media_download(*_args, **_kwargs):
        raise AssertionError("已下载媒体补采评论时不应重新下载视频")

    monkeypatch.setattr(downloader, "_download_with_retry", _unexpected_media_download)

    result = await downloader.download({"aweme_id": aweme_id})

    assert result.total == 1
    assert result.success == 1
    assert result.skipped == 0
    assert api_client.detail_calls == 1
    assert api_client.comment_calls == 1

    comments_files = list(tmp_path.rglob("*_comments.json"))
    assert len(comments_files) == 1
    payload = json.loads(comments_files[0].read_text(encoding="utf-8"))
    assert payload["aweme_id"] == aweme_id
    assert payload["comments"][0]["text"] == "已下载后补采评论"


@pytest.mark.asyncio
async def test_user_mode_collects_comments_when_batch_item_media_is_already_downloaded(tmp_path):
    aweme_id = "7600224486650121527"
    (tmp_path / f"existing_{aweme_id}.mp4").write_bytes(b"downloaded")

    config = ConfigLoader(None)
    config.update(
        path=str(tmp_path),
        comments={"enabled": True, "include_replies": False, "max_comments": 0, "page_size": 20},
        cover=False,
        music=False,
        avatar=False,
        json=False,
        transcript={"enabled": False},
    )
    aweme_data = {
        "aweme_id": aweme_id,
        "desc": "批量评论补采",
        "create_time": 1707303025,
        "author": {"nickname": "测试作者"},
        "video": {"play_addr": {"url_list": ["https://example.com/video.mp4"]}},
    }
    api_client = _FakeAPIClient(aweme_data)
    downloader = UserDownloader(
        config,
        api_client,
        FileManager(str(tmp_path)),
        CookieManager(str(tmp_path / ".cookies.json")),
        database=None,
        rate_limiter=RateLimiter(max_per_second=100),
        retry_handler=RetryHandler(max_retries=1),
        queue_manager=QueueManager(max_workers=1),
    )

    result = await downloader._download_mode_items("post", [aweme_data], "测试作者")

    assert result.total == 1
    assert result.success == 1
    assert result.skipped == 0
    assert api_client.detail_calls == 0
    assert api_client.comment_calls == 1
    assert len(list(tmp_path.rglob("*_comments.json"))) == 1


@pytest.mark.asyncio
async def test_mix_collects_comments_when_item_media_is_already_downloaded(tmp_path, monkeypatch):
    aweme_id = "7600224486650121528"
    (tmp_path / f"existing_{aweme_id}.mp4").write_bytes(b"downloaded")
    aweme_data = {
        "aweme_id": aweme_id,
        "desc": "合集评论补采",
        "create_time": 1707303025,
        "author": {"nickname": "合集作者"},
        "video": {"play_addr": {"url_list": ["https://example.com/video.mp4"]}},
    }
    api_client = _FakeAPIClient(aweme_data)
    downloader = _build_mix_downloader(tmp_path, api_client)

    async def _mix_items(_mix_id: str):
        return [aweme_data]

    async def _mix_detail(_mix_id: str):
        return {"author": {"nickname": "合集作者"}, "mix_name": "评论合集"}

    async def _unexpected_media_download(*_args, **_kwargs):
        raise AssertionError("已下载合集作品补采评论时不应重新下载媒体")

    monkeypatch.setattr(downloader, "_collect_mix_aweme_list", _mix_items)
    monkeypatch.setattr(downloader, "_get_mix_detail", _mix_detail)
    monkeypatch.setattr(downloader, "_download_aweme_assets", _unexpected_media_download)

    result = await downloader.download({"mix_id": "mix-1"})

    assert result.total == 1
    assert result.success == 1
    assert result.skipped == 0
    assert api_client.comment_calls == 1
    assert len(list(tmp_path.rglob("*_comments.json"))) == 1


@pytest.mark.asyncio
async def test_music_fallback_collects_comments_when_aweme_media_is_already_downloaded(
    tmp_path,
    monkeypatch,
):
    aweme_id = "7600224486650121529"
    (tmp_path / f"existing_{aweme_id}.mp4").write_bytes(b"downloaded")
    aweme_data = {
        "aweme_id": aweme_id,
        "desc": "音乐回退评论补采",
        "create_time": 1707303025,
        "author": {"nickname": "音乐作者"},
        "video": {"play_addr": {"url_list": ["https://example.com/video.mp4"]}},
    }
    api_client = _FakeAPIClient(aweme_data)
    downloader = _build_music_downloader(tmp_path, api_client)

    async def _music_detail(_music_id: str):
        return {"title": "无直链音乐"}

    async def _first_aweme(_music_id: str):
        return aweme_data

    async def _unexpected_media_download(*_args, **_kwargs):
        raise AssertionError("已下载音乐回退作品补采评论时不应重新下载媒体")

    monkeypatch.setattr(downloader, "_get_music_detail", _music_detail)
    monkeypatch.setattr(downloader, "_get_first_music_aweme", _first_aweme)
    monkeypatch.setattr(downloader, "_download_aweme_assets", _unexpected_media_download)

    result = await downloader.download({"music_id": "music-1"})

    assert result.total == 1
    assert result.success == 1
    assert result.skipped == 0
    assert api_client.comment_calls == 1
    assert len(list(tmp_path.rglob("*_comments.json"))) == 1


def _comments_enabled_config(tmp_path) -> ConfigLoader:
    config = ConfigLoader(None)
    config.update(
        path=str(tmp_path),
        comments={"enabled": True, "include_replies": False, "max_comments": 0, "page_size": 20},
        cover=False,
        music=False,
        avatar=False,
        json=False,
        transcript={"enabled": False},
    )
    return config


def _build_mix_downloader(tmp_path, api_client) -> MixDownloader:
    return MixDownloader(
        _comments_enabled_config(tmp_path),
        api_client,
        FileManager(str(tmp_path)),
        CookieManager(str(tmp_path / ".cookies.json")),
        database=None,
        rate_limiter=RateLimiter(max_per_second=100),
        retry_handler=RetryHandler(max_retries=1),
        queue_manager=QueueManager(max_workers=1),
    )


def _build_music_downloader(tmp_path, api_client) -> MusicDownloader:
    return MusicDownloader(
        _comments_enabled_config(tmp_path),
        api_client,
        FileManager(str(tmp_path)),
        CookieManager(str(tmp_path / ".cookies.json")),
        database=None,
        rate_limiter=RateLimiter(max_per_second=100),
        retry_handler=RetryHandler(max_retries=1),
        queue_manager=QueueManager(max_workers=1),
    )
