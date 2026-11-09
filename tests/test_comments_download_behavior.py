"""评论采集与已下载媒体的交互回归测试。"""

from __future__ import annotations

import asyncio
import json

import pytest

from auth import CookieManager
from config import ConfigLoader
from control import QueueManager, RateLimiter, RetryHandler
from core import downloader_base
from core.api_client import DouyinAPIClient
from core.mix_downloader import MixDownloader
from core.music_downloader import MusicDownloader
from core.user_downloader import UserDownloader
from core.video_downloader import VideoDownloader
from storage import FileManager

# _request_json 碰到风控空 200 时返回 {}，归一化后就是这一页。
_FAILED_PAGE = DouyinAPIClient._normalize_paged_response({}, item_keys=["comments"])


class _FakeAPIClient:
    BASE_URL = "https://www.douyin.com"

    def __init__(self, aweme_data, comments_page=None):
        self.aweme_data = aweme_data
        self.comments_page = comments_page
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
        if self.comments_page is not None:
            return self.comments_page
        return {
            "items": [{"cid": "c1", "text": "已下载后补采评论"}],
            "has_more": False,
            "max_cursor": 0,
        }

    async def get_session(self):
        return None

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
        return [aweme_data], None

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


class _NoWaitLimiter:
    async def acquire(self):
        return None


class _CommentsOnlyAPI:
    """评论接口按 outcomes 依次返回：True 为一条评论的正常页，False 或用完后为风控空响应。"""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.comment_calls = 0

    async def get_aweme_comments(self, aweme_id, *, cursor, count, include_replies):
        self.comment_calls += 1
        if not (self.outcomes and self.outcomes.pop(0)):
            return _FAILED_PAGE
        return {"items": [{"cid": "c-" + aweme_id}], "has_more": False, "max_cursor": 0}


def _build_video_downloader(tmp_path, api_client) -> VideoDownloader:
    return VideoDownloader(
        _comments_enabled_config(tmp_path),
        api_client,
        FileManager(str(tmp_path)),
        CookieManager(str(tmp_path / ".cookies.json")),
        database=None,
        rate_limiter=_NoWaitLimiter(),
        retry_handler=RetryHandler(max_retries=1),
        queue_manager=QueueManager(max_workers=1),
    )


def _video_aweme(aweme_id):
    return {
        "aweme_id": aweme_id,
        "desc": "评论补抓",
        "create_time": 1707303025,
        "author": {"nickname": "测试作者"},
        "video": {"play_addr": {"url_list": ["https://example.com/video.mp4"]}},
    }


@pytest.mark.asyncio
async def test_backfill_refetches_legacy_empty_comments_file(tmp_path, monkeypatch):
    aweme_id = "7600224486650121530"
    (tmp_path / f"existing_{aweme_id}.mp4").write_bytes(b"downloaded")
    api_client = _FakeAPIClient(_video_aweme(aweme_id))

    async def _unexpected_media_download(*_args, **_kwargs):
        raise AssertionError("补抓评论时不应重新下载视频")

    async def _run():
        downloader = _build_video_downloader(tmp_path, api_client)
        monkeypatch.setattr(downloader, "_download_with_retry", _unexpected_media_download)
        return downloader, await downloader.download({"aweme_id": aweme_id})

    await _run()
    comments_file = next(tmp_path.rglob("*_comments.json"))
    assert json.loads(comments_file.read_text(encoding="utf-8"))["complete"] is True

    # 旧版本请求失败时写下的空文件：没有 complete 字段，0 条。
    legacy_empty = {"aweme_id": aweme_id, "count": 0, "include_replies": False, "comments": []}
    comments_file.write_text(json.dumps(legacy_empty, indent=2), encoding="utf-8")
    downloader, result = await _run()
    assert (result.success, result.skipped) == (1, 0)
    assert api_client.comment_calls == 2
    payload = json.loads(comments_file.read_text(encoding="utf-8"))
    assert payload["comments"][0]["cid"] == "c1"
    assert downloader.comments_incomplete_note() is None

    # 旧版本存下的有内容的文件视为已完成，不再请求。
    legacy_saved = {**legacy_empty, "count": 1, "comments": [{"cid": "old"}]}
    comments_file.write_text(json.dumps(legacy_saved, indent=2), encoding="utf-8")
    _, result = await _run()
    assert (result.success, result.skipped) == (0, 1)
    assert api_client.comment_calls == 2


@pytest.mark.asyncio
async def test_failed_comments_keep_media_success_without_empty_file(tmp_path, monkeypatch):
    aweme_id = "7600224486650121531"
    api_client = _FakeAPIClient(_video_aweme(aweme_id), comments_page=_FAILED_PAGE)
    downloader = _build_video_downloader(tmp_path, api_client)

    async def _fake_video_download(_candidates, save_path, _session, **_kwargs):
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_bytes(b"video")
        return True

    monkeypatch.setattr(downloader, "_download_video_with_fallback", _fake_video_download)

    result = await downloader.download({"aweme_id": aweme_id})

    assert (result.success, result.failed) == (1, 0)
    assert api_client.comment_calls == 1
    assert len(list(tmp_path.rglob("*.mp4"))) == 1
    assert not list(tmp_path.rglob("*_comments.json"))
    assert downloader.comments_incomplete_note().startswith("1 个作品的评论没取全")


@pytest.mark.asyncio
async def test_comments_breaker_stops_requests_after_repeated_failures(tmp_path):
    api_client = _CommentsOnlyAPI([])
    downloader = _build_video_downloader(tmp_path, api_client)
    cfg = downloader._comments_config()

    for index in range(5):
        path = tmp_path / f"{index}_comments.json"
        assert await downloader._save_comments(str(index), path, cfg) is False

    assert api_client.comment_calls == downloader_base._COMMENTS_BREAKER_THRESHOLD
    assert not list(tmp_path.glob("*_comments.json"))
    note = downloader.comments_incomplete_note()
    assert note.startswith("5 个作品的评论没取全")
    assert "连续失败期间部分作品没有请求评论" in note


@pytest.mark.asyncio
async def test_comments_breaker_probes_again_after_cooldown(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader_base, "_COMMENTS_BREAKER_COOLDOWN_S", 0.2)
    api_client = _CommentsOnlyAPI([False, False, False, True, True])
    downloader = _build_video_downloader(tmp_path, api_client)
    cfg = downloader._comments_config()

    async def _save(aweme_id):
        path = tmp_path / f"{aweme_id}_comments.json"
        return await downloader._save_comments(aweme_id, path, cfg)

    for aweme_id in ("a", "b", "c"):
        assert await _save(aweme_id) is False
    assert await _save("d") is False  # 熔断中，不请求
    assert api_client.comment_calls == 3

    await asyncio.sleep(0.25)
    assert await _save("e") is True  # 冷却期满放一个作品试探，成功即恢复
    assert await _save("a") is True  # 恢复后之前没取全的作品照常请求
    assert api_client.comment_calls == 5
    assert downloader.comments_incomplete_note().startswith("3 个作品的评论没取全")
