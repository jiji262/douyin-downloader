import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from auth import CookieManager
from config import ConfigLoader
from control import QueueManager, RateLimiter, RetryHandler
from core.api_client import DouyinAPIClient
from core.metadata import extract_video_cover_urls
from core.video_downloader import VideoDownloader
from storage import FileManager


class _FakeProgressReporter:
    def __init__(self):
        self.step_updates = []
        self.item_totals = []
        self.item_events = []

    def update_step(self, step: str, detail: str = "") -> None:
        self.step_updates.append((step, detail))

    def set_item_total(self, total: int, detail: str = "") -> None:
        self.item_totals.append((total, detail))

    def advance_item(self, status: str, detail: str = "") -> None:
        self.item_events.append((status, detail))


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


def test_extract_video_cover_urls_prefers_original_cover():
    aweme = {
        "video": {
            "origin_cover": {"url_list": ["https://example.com/original.jpg"]},
            "cover": {"url_list": ["https://example.com/preview.jpg"]},
        }
    }

    assert extract_video_cover_urls(aweme) == ["https://example.com/original.jpg"]


def test_extract_video_cover_urls_falls_back_to_preview_cover():
    aweme = {
        "video": {
            "origin_cover": {"url_list": []},
            "cover": {"url_list": ["https://example.com/preview.jpg"]},
        }
    }

    assert extract_video_cover_urls(aweme) == ["https://example.com/preview.jpg"]


@pytest.mark.asyncio
async def test_video_downloader_skip_counts_total(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path)

    async def _fake_should_download(self, _):
        return False

    downloader._should_download = _fake_should_download.__get__(downloader, VideoDownloader)

    result = await downloader.download({"aweme_id": "123"})

    assert result.total == 1
    assert result.skipped == 1
    assert result.success == 0
    assert result.failed == 0

    await api_client.close()


@pytest.mark.asyncio
async def test_video_downloader_reports_item_progress(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path)
    reporter = _FakeProgressReporter()
    downloader.progress_reporter = reporter

    async def _fake_should_download(self, _aweme_id):
        return True

    async def _fake_get_video_detail(_aweme_id: str):
        return {"aweme_id": "123", "author": {"nickname": "tester"}}

    async def _fake_download_aweme(self, _aweme_data):
        return True

    downloader._should_download = _fake_should_download.__get__(downloader, VideoDownloader)
    monkeypatch.setattr(api_client, "get_video_detail", _fake_get_video_detail)
    downloader._download_aweme = _fake_download_aweme.__get__(downloader, VideoDownloader)

    result = await downloader.download({"aweme_id": "123"})

    assert result.total == 1
    assert result.success == 1
    assert reporter.item_totals == [(1, "单作品下载")]
    assert ("下载作品", "单作品资源下载中") in reporter.step_updates
    assert reporter.item_events == [("success", "123")]

    await api_client.close()


@pytest.mark.asyncio
async def test_video_downloader_downloads_note_video_fallback(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(music=False, cover=False, avatar=False, json=False, folderstyle=True)

    aweme_id = "7646971177114611826"

    async def _fake_should_download(self, _aweme_id):
        return True

    async def _fake_get_video_detail(_aweme_id: str):
        assert _aweme_id == aweme_id
        return {
            "aweme_id": aweme_id,
            "aweme_type": 68,
            "desc": "note 视频作品",
            "video": {
                "play_addr_h264": {"url_list": ["https://v3-web.douyinvod.com/note-h264.mp4"]}
            },
        }

    async def _fake_get_session():
        return object()

    saved = []

    async def _fake_download_with_retry(self, url, save_path, _session, **_kwargs):
        saved.append((url, save_path))
        return True

    downloader._should_download = _fake_should_download.__get__(downloader, VideoDownloader)
    monkeypatch.setattr(api_client, "get_video_detail", _fake_get_video_detail)
    monkeypatch.setattr(api_client, "get_session", _fake_get_session)
    downloader._download_with_retry = _fake_download_with_retry.__get__(downloader, VideoDownloader)

    result = await downloader.download({"type": "gallery", "aweme_id": aweme_id})

    assert result.total == 1
    assert result.success == 1
    assert result.failed == 0
    assert saved[0][0] == "https://v3-web.douyinvod.com/note-h264.mp4"
    assert saved[0][1].suffix == ".mp4"

    await api_client.close()


@pytest.mark.asyncio
async def test_build_no_watermark_url_signs_with_headers(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path)

    signed_url = "https://www.douyin.com/aweme/v1/play/?video_id=1&X-Bogus=signed"

    def _fake_sign(url: str):
        return signed_url, "UnitTestAgent/1.0"

    monkeypatch.setattr(api_client, "sign_url", _fake_sign)

    aweme = {
        "aweme_id": "1",
        "video": {
            "play_addr": {
                "url_list": ["https://www.douyin.com/aweme/v1/play/?video_id=1&watermark=0"]
            }
        },
    }

    url, headers = downloader._build_no_watermark_url(aweme)

    assert url == signed_url
    assert headers["User-Agent"] == "UnitTestAgent/1.0"
    assert headers["Accept"] == "*/*"
    assert headers["Referer"].startswith("https://www.douyin.com")

    await api_client.close()


@pytest.mark.asyncio
async def test_build_no_watermark_url_avoids_playwm_when_uri_can_be_signed(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path)

    signed_url = "https://www.douyin.com/aweme/v1/play/?video_id=clean&watermark=0"

    def _fake_build_signed_path(path, params):
        assert path == "/aweme/v1/play/"
        assert params["video_id"] == "clean"
        assert params["watermark"] == "0"
        return signed_url, "UnitTestAgent/2.0"

    monkeypatch.setattr(api_client, "build_signed_path", _fake_build_signed_path)

    aweme = {
        "aweme_id": "1",
        "video": {
            "play_addr": {
                "uri": "clean",
                "url_list": ["https://v3-web.douyinvod.com/playwm/abc.mp4?watermark=1"],
            }
        },
    }

    url, headers = downloader._build_no_watermark_url(aweme)

    assert url == signed_url
    assert headers["User-Agent"] == "UnitTestAgent/2.0"

    await api_client.close()


@pytest.mark.asyncio
async def test_build_no_watermark_url_prefers_signed_uri_when_variant_exists(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path)

    signed_url = "https://www.douyin.com/aweme/v1/play/?video_id=clean&watermark=0"

    def _fake_build_signed_path(path, params):
        assert path == "/aweme/v1/play/"
        assert params["video_id"] == "clean"
        return signed_url, "UnitTestAgent/2.1"

    monkeypatch.setattr(api_client, "build_signed_path", _fake_build_signed_path)

    aweme = {
        "aweme_id": "1",
        "video": {
            "play_addr_h264": {"url_list": ["https://v3-web.douyinvod.com/direct-h264.mp4"]},
            "play_addr": {
                "uri": "clean",
                "url_list": ["https://v3-web.douyinvod.com/playwm/abc.mp4?watermark=1"],
            },
        },
    }

    url, headers = downloader._build_no_watermark_url(aweme)

    assert url == signed_url
    assert headers["User-Agent"] == "UnitTestAgent/2.1"

    await api_client.close()


@pytest.mark.asyncio
async def test_gallery_mirrors_are_single_attempt_each(tmp_path, monkeypatch):
    """图集镜像沿用 _download_first_available 的原则：多镜像时镜像列表本身
    就是重试机制（每镜像单次尝试、早期失败降噪），单镜像才保留退避重试。
    否则死镜像 × 每镜像 4 次退避嵌套，一张图最坏能拖 3-7 分钟。"""
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(music=False, cover=False, avatar=False, json=False, folderstyle=True)

    aweme_id = "7646971177114611827"

    async def _fake_should_download(self, _aweme_id):
        return True

    async def _fake_get_video_detail(_aweme_id: str):
        return {
            "aweme_id": aweme_id,
            "aweme_type": 68,
            "desc": "图集作品",
            "images": [
                {
                    "url_list": [
                        "https://p3-sign.douyinpic.com/a-mirror1.jpeg",
                        "https://p9-sign.douyinpic.com/a-mirror2.jpeg",
                    ]
                },
                {"url_list": ["https://p3-sign.douyinpic.com/b-single.jpeg"]},
            ],
        }

    async def _fake_get_session():
        return object()

    calls = []

    async def _fake_download_with_retry(self, url, save_path, _session, **kwargs):
        calls.append((url, kwargs))
        return "a-mirror1" not in url  # 首个镜像失败，其余成功

    downloader._should_download = _fake_should_download.__get__(downloader, VideoDownloader)
    monkeypatch.setattr(api_client, "get_video_detail", _fake_get_video_detail)
    monkeypatch.setattr(api_client, "get_session", _fake_get_session)
    downloader._download_with_retry = _fake_download_with_retry.__get__(downloader, VideoDownloader)

    result = await downloader.download({"type": "gallery", "aweme_id": aweme_id})

    assert result.success == 1
    by_url = {url: kwargs for url, kwargs in calls}
    kwargs_multi_1 = by_url["https://p3-sign.douyinpic.com/a-mirror1.jpeg"]
    assert kwargs_multi_1.get("retry") is False
    assert kwargs_multi_1.get("optional") is True  # 非末位镜像失败降噪
    kwargs_multi_2 = by_url["https://p9-sign.douyinpic.com/a-mirror2.jpeg"]
    assert kwargs_multi_2.get("retry") is False
    kwargs_single = by_url["https://p3-sign.douyinpic.com/b-single.jpeg"]
    assert kwargs_single.get("retry", True) is True  # 单镜像保留退避

    await api_client.close()


@pytest.mark.asyncio
async def test_build_no_watermark_url_prefers_direct_cdn_over_inlist_play(tmp_path):
    """url_list 同时含直连 CDN 与已签名 /aweme/v1/play/ 时必须选直连：
    play 端点 302 后可能落到打不通的 PCDN 节点（*.qtaeixd.com 高位端口），
    直连域名走标准 CDN。此前循环内对 douyin.com 候选提前 return，
    打破了 commit 099aae5 声明的直连优先。"""
    downloader, api_client = _build_downloader(tmp_path)

    aweme = {
        "aweme_id": "1",
        "video": {
            "play_addr": {
                "uri": "clean",
                "url_list": [
                    "https://www.douyin.com/aweme/v1/play/?video_id=clean&file_id=f"
                    "&sign=s&is_play_url=1&X-Bogus=abc",
                    "https://v3-web.douyinvod.com/direct.mp4",
                ],
            }
        },
    }

    url, _headers = downloader._build_no_watermark_url(aweme)

    assert url == "https://v3-web.douyinvod.com/direct.mp4"

    await api_client.close()


@pytest.mark.asyncio
async def test_build_video_url_candidates_keeps_play_as_fallback(tmp_path):
    """直连 CDN 之外要保留 play 端点作为降级候选，直连失败时还有救。"""
    downloader, api_client = _build_downloader(tmp_path)

    play_url = (
        "https://www.douyin.com/aweme/v1/play/?video_id=clean&file_id=f"
        "&sign=s&is_play_url=1&X-Bogus=abc"
    )
    aweme = {
        "aweme_id": "1",
        "video": {
            "play_addr": {
                "uri": "clean",
                "url_list": [play_url, "https://v3-web.douyinvod.com/direct.mp4"],
            }
        },
    }

    candidates = downloader._build_video_url_candidates(aweme)

    assert [url for url, _ in candidates] == [
        "https://v3-web.douyinvod.com/direct.mp4",
        play_url,
    ]

    await api_client.close()


@pytest.mark.asyncio
async def test_build_video_url_candidates_includes_all_direct_mirrors(tmp_path):
    """url_list 常带 2-3 个直连镜像（v3/v9 等不同主机）；只取第一个会在
    镜像 1 挂掉时直接进 play 端点的 PCDN 抽签，健康的镜像 2 反而被丢弃。
    全部净版直连镜像都要进候选，按 url_list 原序排在 play 端点之前。"""
    downloader, api_client = _build_downloader(tmp_path)

    play_url = (
        "https://www.douyin.com/aweme/v1/play/?video_id=clean&file_id=f"
        "&sign=s&is_play_url=1&X-Bogus=abc"
    )
    aweme = {
        "aweme_id": "1",
        "video": {
            "play_addr": {
                "uri": "clean",
                "url_list": [
                    "https://v3-web.douyinvod.com/direct.mp4",
                    "https://v9-web.douyinvod.com/direct.mp4",
                    play_url,
                ],
            }
        },
    }

    candidates = downloader._build_video_url_candidates(aweme)

    assert [url for url, _ in candidates] == [
        "https://v3-web.douyinvod.com/direct.mp4",
        "https://v9-web.douyinvod.com/direct.mp4",
        play_url,
    ]

    await api_client.close()


@pytest.mark.asyncio
async def test_video_download_falls_back_to_play_url_when_direct_fails(tmp_path):
    downloader, api_client = _build_downloader(tmp_path)

    attempts = []

    async def _fake_download_with_retry(self, url, save_path, _session, **kwargs):
        attempts.append(url)
        # 轮扫内必须禁用单 URL 退避重试（否则嵌套重试会把死节点等待
        # 放大回本修复要消除的量级），失败降噪走 optional。
        assert kwargs.get("retry") is False
        assert kwargs.get("optional") is True
        return url.startswith("https://www.douyin.com/aweme/v1/play/")

    downloader._download_with_retry = _fake_download_with_retry.__get__(downloader, VideoDownloader)

    candidates = [
        ("https://v3-web.douyinvod.com/direct.mp4", {}),
        ("https://www.douyin.com/aweme/v1/play/?video_id=x&X-Bogus=b", {}),
    ]

    ok = await downloader._download_video_with_fallback(candidates, tmp_path / "v.mp4", object())

    assert ok is True
    assert attempts == [candidates[0][0], candidates[1][0]]

    await api_client.close()


@pytest.mark.asyncio
async def test_video_download_fallback_retries_rounds_then_fails(tmp_path, monkeypatch):
    """整轮候选都失败时按 RetryHandler 退避重跑整轮，穷尽后返回 False。"""
    downloader, api_client = _build_downloader(tmp_path)

    async def _no_sleep(_delay):
        return None

    monkeypatch.setattr("control.retry_handler.asyncio.sleep", _no_sleep)

    attempts = []

    async def _fake_download_with_retry(self, url, save_path, _session, **_kwargs):
        attempts.append(url)
        return False

    downloader._download_with_retry = _fake_download_with_retry.__get__(downloader, VideoDownloader)

    candidates = [
        ("https://v3-web.douyinvod.com/direct.mp4", {}),
        ("https://www.douyin.com/aweme/v1/play/?video_id=x&X-Bogus=b", {}),
    ]

    ok = await downloader._download_video_with_fallback(candidates, tmp_path / "v.mp4", object())

    # fixture 的 RetryHandler(max_retries=1) → 2 轮 × 2 个候选
    assert ok is False
    assert len(attempts) == 4

    await api_client.close()


@pytest.mark.asyncio
async def test_should_download_skips_when_aweme_exists_locally(tmp_path):
    downloader, api_client = _build_downloader(tmp_path)
    aweme_id = "7600223638943468863"

    existing_file = tmp_path / f"2026-02-18_demo_{aweme_id}.mp4"
    existing_file.write_bytes(b"1")

    should_download = await downloader._should_download(aweme_id)
    assert should_download is False

    await api_client.close()


@pytest.mark.asyncio
async def test_should_download_does_not_treat_cover_as_primary_media(tmp_path):
    downloader, api_client = _build_downloader(tmp_path)
    aweme_id = "7600223638943468864"

    cover_file = tmp_path / f"2026-02-18_demo_{aweme_id}_cover.jpg"
    cover_file.write_bytes(b"1")

    should_download = await downloader._should_download(aweme_id)
    assert should_download is True

    await api_client.close()


@pytest.mark.asyncio
async def test_download_aweme_assets_uses_publish_date_and_writes_manifest(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(music=False, cover=False, avatar=False, json=False, folderstyle=True)

    async def _fake_get_session():
        return object()

    monkeypatch.setattr(api_client, "get_session", _fake_get_session)

    saved_paths = []

    async def _fake_download_with_retry(self, _url, save_path, _session, **_kwargs):
        saved_paths.append(save_path)
        return True

    downloader._download_with_retry = _fake_download_with_retry.__get__(downloader, VideoDownloader)

    aweme_id = "7600224486650121526"
    publish_ts = 1707303025
    expected_date_prefix = datetime.fromtimestamp(publish_ts).strftime("%Y-%m-%d")
    aweme_data = {
        "aweme_id": aweme_id,
        "desc": "测试下载日期文件名",
        "create_time": publish_ts,
        "text_extra": [{"hashtag_name": "测试标签"}],
        "video": {"play_addr": {"url_list": ["https://example.com/video.mp4"]}},
    }

    success = await downloader._download_aweme_assets(
        aweme_data, author_name="测试作者", mode="post"
    )

    assert success is True
    assert len(saved_paths) == 1

    save_path = saved_paths[0]
    assert save_path.name.startswith(f"{expected_date_prefix}_")
    assert aweme_id in save_path.name
    assert save_path.parent.name.startswith(f"{expected_date_prefix}_")

    manifest_path = tmp_path / "download_manifest.jsonl"
    assert manifest_path.exists()
    lines = manifest_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1

    manifest_entry = json.loads(lines[0])
    assert manifest_entry["date"] == expected_date_prefix
    assert manifest_entry["aweme_id"] == aweme_id
    assert manifest_entry["tags"] == ["测试标签"]
    assert save_path.name in manifest_entry["file_names"]

    await api_client.close()


@pytest.mark.asyncio
async def test_download_aweme_assets_keeps_success_when_transcript_skipped(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(
        music=False,
        cover=False,
        avatar=False,
        json=False,
        folderstyle=True,
        transcript={
            "enabled": True,
            "api_key_env": "OPENAI_API_KEY",
            "api_key": "",
            "output_dir": "",
            "response_formats": ["txt", "json"],
        },
    )

    async def _fake_get_session():
        return object()

    monkeypatch.setattr(api_client, "get_session", _fake_get_session)

    async def _fake_download_with_retry(self, _url, _save_path, _session, **_kwargs):
        return True

    downloader._download_with_retry = _fake_download_with_retry.__get__(downloader, VideoDownloader)

    aweme_data = {
        "aweme_id": "7600224486650121527",
        "desc": "转写缺 key 也不应影响下载",
        "video": {"play_addr": {"url_list": ["https://example.com/video.mp4"]}},
    }

    success = await downloader._download_aweme_assets(
        aweme_data, author_name="测试作者", mode="post"
    )

    assert success is True

    await api_client.close()


@pytest.mark.asyncio
async def test_download_aweme_assets_video_writes_cover_avatar_and_json(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(
        music=False,
        cover=True,
        avatar=True,
        json=True,
        folderstyle=True,
        transcript={"enabled": False},
    )

    async def _fake_get_session():
        return object()

    monkeypatch.setattr(api_client, "get_session", _fake_get_session)

    saved_paths = []

    async def _fake_download_with_retry(self, _url, save_path, _session, **_kwargs):
        saved_paths.append(save_path)
        return True

    downloader._download_with_retry = _fake_download_with_retry.__get__(downloader, VideoDownloader)

    aweme_data = {
        "aweme_id": "7600224486650121527",
        "desc": "附加资源",
        "create_time": 1707303025,
        "author": {
            "nickname": "测试作者",
            "avatar_larger": {"url_list": ["https://example.com/avatar.jpg"]},
        },
        "video": {
            "play_addr": {"url_list": ["https://example.com/video.mp4"]},
            "cover": {"url_list": ["https://example.com/cover.jpg"]},
        },
    }

    success = await downloader._download_aweme_assets(
        aweme_data, author_name="测试作者", mode="post"
    )

    assert success is True
    assert any(path.name.endswith(".mp4") for path in saved_paths)
    assert any(path.name.endswith("_cover.jpg") for path in saved_paths)
    assert any(path.name.endswith("_avatar.jpg") for path in saved_paths)
    metadata_files = list(tmp_path.rglob("*_data.json"))
    assert len(metadata_files) == 1

    await api_client.close()


@pytest.mark.asyncio
async def test_video_false_skips_mp4_but_keeps_selected_sidecars(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(
        video=False,
        music=True,
        cover=True,
        avatar=False,
        json=True,
        folderstyle=True,
        transcript={"enabled": False},
    )

    async def _fake_get_session():
        return object()

    monkeypatch.setattr(api_client, "get_session", _fake_get_session)

    attempted = []
    saved_paths = []

    async def _fake_download_with_retry(self, url, save_path, _session, **_kwargs):
        attempted.append(url)
        saved_paths.append(save_path)
        return True

    downloader._download_with_retry = _fake_download_with_retry.__get__(downloader, VideoDownloader)

    aweme_data = {
        "aweme_id": "7600224486650121600",
        "desc": "仅归档附加资源",
        "author": {"nickname": "测试作者"},
        "music": {"play_url": {"url_list": ["https://example.com/music.mp3"]}},
        "video": {
            "play_addr": {"url_list": ["https://example.com/video.mp4"]},
            "origin_cover": {"url_list": ["https://example.com/original.jpg"]},
            "cover": {"url_list": ["https://example.com/preview.jpg"]},
        },
    }

    success = await downloader._download_aweme_assets(
        aweme_data, author_name="测试作者", mode="post"
    )

    assert success is True
    assert "https://example.com/video.mp4" not in attempted
    assert "https://example.com/original.jpg" in attempted
    assert "https://example.com/preview.jpg" not in attempted
    assert "https://example.com/music.mp3" in attempted
    assert not any(path.suffix == ".mp4" for path in saved_paths)
    assert len(list(tmp_path.rglob("*_data.json"))) == 1

    await api_client.close()


@pytest.mark.asyncio
async def test_video_download_remains_enabled_when_config_key_is_omitted(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(
        music=False,
        cover=False,
        avatar=False,
        json=False,
        folderstyle=True,
        transcript={"enabled": False},
    )
    downloader.config.config.pop("video", None)

    async def _fake_get_session():
        return object()

    monkeypatch.setattr(api_client, "get_session", _fake_get_session)

    attempted = []

    async def _fake_download_with_retry(self, url, _save_path, _session, **_kwargs):
        attempted.append(url)
        return True

    downloader._download_with_retry = _fake_download_with_retry.__get__(downloader, VideoDownloader)

    success = await downloader._download_aweme_assets(
        {
            "aweme_id": "7600224486650121601",
            "desc": "旧配置默认下载视频",
            "video": {"play_addr": {"url_list": ["https://example.com/video.mp4"]}},
        },
        author_name="测试作者",
        mode="post",
    )

    assert success is True
    assert "https://example.com/video.mp4" in attempted

    await api_client.close()


@pytest.mark.asyncio
async def test_video_false_does_not_disable_gallery_images(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(
        video=False,
        music=False,
        cover=False,
        avatar=False,
        json=False,
        folderstyle=True,
    )

    async def _fake_get_session():
        return object()

    monkeypatch.setattr(api_client, "get_session", _fake_get_session)

    attempted = []

    async def _fake_download_with_retry(self, url, _save_path, _session, **_kwargs):
        attempted.append(url)
        return True

    downloader._download_with_retry = _fake_download_with_retry.__get__(downloader, VideoDownloader)

    success = await downloader._download_aweme_assets(
        {
            "aweme_id": "7600224486650121602",
            "aweme_type": 68,
            "desc": "图集不受视频开关影响",
            "images": [{"url_list": ["https://example.com/gallery.jpg"]}],
        },
        author_name="测试作者",
        mode="post",
    )

    assert success is True
    assert attempted == ["https://example.com/gallery.jpg"]

    await api_client.close()


@pytest.mark.asyncio
async def test_download_aweme_assets_cover_falls_back_across_mirrors(tmp_path, monkeypatch):
    """Cover/avatar downloads must try every mirror in ``url_list``.

    Douyin returns multiple CDN mirrors per image (p3/p9); the first sometimes
    returns 403 while a later one succeeds. The download must fall back instead
    of giving up after ``url_list[0]`` and silently dropping the real cover.
    """
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(
        music=False,
        cover=True,
        avatar=True,
        json=False,
        folderstyle=True,
        transcript={"enabled": False},
    )

    async def _fake_get_session():
        return object()

    monkeypatch.setattr(api_client, "get_session", _fake_get_session)

    attempted = []

    async def _fake_download_with_retry(self, url, _save_path, _session, **_kwargs):
        attempted.append(url)
        # First mirror hard-fails (simulates 403); a later mirror succeeds.
        return "mirror-fail" not in url

    downloader._download_with_retry = _fake_download_with_retry.__get__(downloader, VideoDownloader)

    aweme_data = {
        "aweme_id": "7600224486650121599",
        "desc": "封面首镜像失败应回退",
        "author": {
            "nickname": "测试作者",
            "avatar_larger": {
                "url_list": [
                    "https://mirror-fail.douyinpic.com/avatar.jpg",
                    "https://p9-ok.douyinpic.com/avatar.jpg",
                ]
            },
        },
        "video": {
            "play_addr": {"url_list": ["https://example.com/video.mp4"]},
            "cover": {
                "url_list": [
                    "https://mirror-fail.douyinpic.com/cover.jpg",
                    "https://p9-ok.douyinpic.com/cover.jpg",
                ]
            },
        },
    }

    success = await downloader._download_aweme_assets(
        aweme_data, author_name="测试作者", mode="post"
    )

    assert success is True
    # Both cover mirrors attempted, and the working fallback was reached.
    assert "https://mirror-fail.douyinpic.com/cover.jpg" in attempted
    assert "https://p9-ok.douyinpic.com/cover.jpg" in attempted
    # Same fallback behaviour for the avatar.
    assert "https://p9-ok.douyinpic.com/avatar.jpg" in attempted

    await api_client.close()


@pytest.mark.asyncio
async def test_download_aweme_assets_gallery_downloads_live_photo_videos(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(music=False, cover=False, avatar=False, json=False, folderstyle=True)

    async def _fake_get_session():
        return object()

    monkeypatch.setattr(api_client, "get_session", _fake_get_session)

    saved_paths = []

    async def _fake_download_with_retry(self, _url, save_path, _session, **_kwargs):
        saved_paths.append(save_path)
        return True

    downloader._download_with_retry = _fake_download_with_retry.__get__(downloader, VideoDownloader)

    aweme_data = {
        "aweme_id": "7600224486650121528",
        "desc": "实况图文",
        "image_post_info": {
            "images": [
                {
                    "display_image": {"url_list": ["https://example.com/1.webp"]},
                    "video": {"play_addr": {"url_list": ["https://example.com/1_live.mp4"]}},
                },
                {
                    "video": {"play_addr": {"url_list": ["https://example.com/2_live.mp4"]}},
                },
            ]
        },
    }

    success = await downloader._download_aweme_assets(
        aweme_data, author_name="测试作者", mode="post"
    )

    assert success is True
    assert any(path.suffix == ".webp" for path in saved_paths)
    assert sum(path.suffix == ".mp4" for path in saved_paths) == 2
    assert any("_live_1.mp4" in path.name for path in saved_paths)
    assert any("_live_2.mp4" in path.name for path in saved_paths)

    await api_client.close()


@pytest.mark.asyncio
async def test_download_aweme_assets_gallery_preserves_real_image_extensions(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(music=False, cover=False, avatar=False, json=False, folderstyle=True)

    async def _fake_get_session():
        return object()

    monkeypatch.setattr(api_client, "get_session", _fake_get_session)

    saved_paths = []

    async def _fake_download_with_retry(self, _url, save_path, _session, **_kwargs):
        saved_paths.append(save_path)
        return True

    downloader._download_with_retry = _fake_download_with_retry.__get__(downloader, VideoDownloader)

    aweme_data = {
        "aweme_id": "7600224486650121991",
        "desc": "图集后缀归一化",
        "image_post_info": {
            "images": [
                {
                    "display_image": {
                        "url_list": ["https://example.com/gallery_1.png~tplv-obj.image?x=1"]
                    }
                },
                {
                    "display_image": {
                        "url_list": ["https://example.com/gallery_2.jpeg~tplv-resize:1080:0.image"]
                    }
                },
                {
                    "display_image": {
                        "url_list": ["https://example.com/gallery_3.jpg?from=unit-test"]
                    }
                },
            ]
        },
    }

    success = await downloader._download_aweme_assets(
        aweme_data, author_name="测试作者", mode="post"
    )

    assert success is True
    assert [path.suffix for path in saved_paths] == [".png", ".jpeg", ".jpg"]

    await api_client.close()


@pytest.mark.asyncio
async def test_download_aweme_assets_gallery_uses_response_content_type_for_suffix(
    tmp_path, monkeypatch
):
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(music=False, cover=False, avatar=False, json=False, folderstyle=True)

    content = b"fake png content"
    publish_ts = 1707303025
    publish_date = datetime.fromtimestamp(publish_ts).strftime("%Y-%m-%d")
    aweme_id = "7600224486650121992"

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.content_length = len(content)
    mock_response.headers = {"Content-Type": "image/png; charset=binary"}

    async def iter_chunked(_size):
        yield content

    mock_response.content = MagicMock()
    mock_response.content.iter_chunked = iter_chunked

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_response)
    ctx.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get.return_value = ctx

    async def _fake_get_session():
        return mock_session

    monkeypatch.setattr(api_client, "get_session", _fake_get_session)

    aweme_data = {
        "aweme_id": aweme_id,
        "desc": "响应头决定后缀",
        "create_time": publish_ts,
        "image_post_info": {
            "images": [{"display_image": {"url_list": ["https://example.com/gallery_1.image?x=1"]}}]
        },
    }

    success = await downloader._download_aweme_assets(
        aweme_data, author_name="测试作者", mode="post"
    )

    assert success is True
    save_dir = tmp_path / "测试作者" / "post" / f"{publish_date}_响应头决定后缀_{aweme_id}"
    saved_files = sorted(path.name for path in save_dir.iterdir() if path.is_file())
    assert saved_files == [f"{publish_date}_响应头决定后缀_{aweme_id}_1.png"]

    manifest_path = tmp_path / "download_manifest.jsonl"
    lines = manifest_path.read_text(encoding="utf-8").strip().splitlines()
    manifest_entry = json.loads(lines[-1])
    assert manifest_entry["file_names"] == saved_files

    await api_client.close()


@pytest.mark.asyncio
async def test_download_aweme_assets_gallery_tries_next_image_candidate(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(music=False, cover=False, avatar=False, json=False, folderstyle=True)

    async def _fake_get_session():
        return object()

    monkeypatch.setattr(api_client, "get_session", _fake_get_session)

    attempted_urls = []

    async def _fake_download_with_retry(self, url, _save_path, _session, **_kwargs):
        attempted_urls.append(url)
        return url.endswith("good.jpeg")

    downloader._download_with_retry = _fake_download_with_retry.__get__(downloader, VideoDownloader)

    aweme_data = {
        "aweme_id": "7600224486650121993",
        "desc": "候选图回退",
        "image_post_info": {
            "images": [
                {
                    "download_url_list": [
                        "https://example.com/bad.jpg",
                        "https://example.com/good.jpeg",
                    ],
                    "url_list": ["https://example.com/preview.webp"],
                }
            ]
        },
    }

    success = await downloader._download_aweme_assets(
        aweme_data, author_name="测试作者", mode="post"
    )

    assert success is True
    assert attempted_urls == [
        "https://example.com/preview.webp",
        "https://example.com/bad.jpg",
        "https://example.com/good.jpeg",
    ]

    await api_client.close()


def test_collect_image_urls_prefers_jpeg_over_webp_companion(tmp_path):
    downloader, api_client = _build_downloader(tmp_path)

    aweme_data = {
        "aweme_id": "100006",
        "images": [
            {
                "download_url_list": [
                    "https://example.com/image.webp",
                    "https://example.com/image.jpeg",
                ],
            },
        ],
    }

    urls = downloader._collect_image_urls(aweme_data)

    assert urls == ["https://example.com/image.jpeg"]

    asyncio.run(api_client.close())


def test_collect_image_urls_prefers_highest_resolution_clean_source(tmp_path):
    downloader, api_client = _build_downloader(tmp_path)

    aweme_data = {
        "aweme_id": "100007",
        "image_post_info": {
            "images": [
                {
                    "url_list": ["https://cdn.example.com/preview-720.jpg"],
                    "width": 720,
                    "height": 1280,
                    "display_image": {
                        "url_list": ["https://cdn.example.com/display-1080.jpg"],
                        "width": 1080,
                        "height": 1920,
                    },
                    "origin_image": {
                        "url_list": ["https://cdn.example.com/origin-1440.jpg"],
                        "width": 1440,
                        "height": 2560,
                    },
                    "owner_watermark_image": {
                        "url_list": ["https://cdn.example.com/highres-2160.jpg"],
                        "width": 2160,
                        "height": 3840,
                    },
                },
            ]
        },
    }

    candidates = downloader._collect_image_url_candidates(aweme_data)[0]

    assert candidates == [
        "https://cdn.example.com/origin-1440.jpg",
        "https://cdn.example.com/display-1080.jpg",
        "https://cdn.example.com/preview-720.jpg",
        "https://cdn.example.com/highres-2160.jpg",
    ]
    assert downloader._collect_image_urls(aweme_data) == ["https://cdn.example.com/origin-1440.jpg"]

    asyncio.run(api_client.close())


def test_collect_image_urls_ranks_watermark_free_list_by_resolution(tmp_path):
    downloader, api_client = _build_downloader(tmp_path)

    aweme_data = {
        "aweme_id": "100008",
        "image_post_info": {
            "images": [
                {
                    "watermark_free_download_url_list": [
                        "https://cdn.example.com/clean-free-720.jpg"
                    ],
                    "url_list": ["https://cdn.example.com/preview-720.jpg"],
                    "width": 720,
                    "height": 1280,
                    "display_image": {
                        "url_list": ["https://cdn.example.com/display-1080.jpg"],
                        "width": 1080,
                        "height": 1920,
                    },
                    "origin_image": {
                        "url_list": ["https://cdn.example.com/origin-1440.jpg"],
                        "width": 1440,
                        "height": 2560,
                    },
                    "download_url": {
                        "url_list": ["https://cdn.example.com/fallback-2160.jpg"],
                        "width": 2160,
                        "height": 3840,
                    },
                },
            ]
        },
    }

    candidates = downloader._collect_image_url_candidates(aweme_data)[0]

    assert candidates == [
        "https://cdn.example.com/origin-1440.jpg",
        "https://cdn.example.com/display-1080.jpg",
        "https://cdn.example.com/clean-free-720.jpg",
        "https://cdn.example.com/preview-720.jpg",
        "https://cdn.example.com/fallback-2160.jpg",
    ]

    asyncio.run(api_client.close())


@pytest.mark.asyncio
async def test_download_aweme_assets_gallery_succeeds_with_only_live_videos(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(music=False, cover=False, avatar=False, json=False, folderstyle=True)

    async def _fake_get_session():
        return object()

    monkeypatch.setattr(api_client, "get_session", _fake_get_session)

    saved_paths = []

    async def _fake_download_with_retry(self, _url, save_path, _session, **_kwargs):
        saved_paths.append(save_path)
        return True

    downloader._download_with_retry = _fake_download_with_retry.__get__(downloader, VideoDownloader)

    aweme_data = {
        "aweme_id": "7600224486650121529",
        "desc": "仅实况图文",
        "image_post_info": {
            "images": [
                {"video": {"play_addr": {"url_list": ["https://example.com/only_live_1.mp4"]}}},
                {"video": {"play_addr": {"url_list": ["https://example.com/only_live_2.mp4"]}}},
            ]
        },
    }

    success = await downloader._download_aweme_assets(
        aweme_data, author_name="测试作者", mode="post"
    )

    assert success is True
    assert len(saved_paths) == 2
    assert all(path.suffix == ".mp4" for path in saved_paths)
    assert any("_live_1.mp4" in path.name for path in saved_paths)
    assert any("_live_2.mp4" in path.name for path in saved_paths)

    await api_client.close()


@pytest.mark.asyncio
async def test_download_aweme_assets_gallery_fails_when_live_video_download_fails(
    tmp_path, monkeypatch
):
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(music=False, cover=False, avatar=False, json=False, folderstyle=True)

    async def _fake_get_session():
        return object()

    monkeypatch.setattr(api_client, "get_session", _fake_get_session)

    saved_paths = []

    async def _fake_download_with_retry(self, _url, save_path, _session, **_kwargs):
        saved_paths.append(save_path)
        if save_path.name.endswith("_live_2.mp4"):
            return False
        return True

    downloader._download_with_retry = _fake_download_with_retry.__get__(downloader, VideoDownloader)

    aweme_data = {
        "aweme_id": "7600224486650121530",
        "desc": "实况下载失败场景",
        "image_post_info": {
            "images": [
                {
                    "display_image": {"url_list": ["https://example.com/ok.webp"]},
                    "video": {"play_addr": {"url_list": ["https://example.com/live_ok.mp4"]}},
                },
                {"video": {"play_addr": {"url_list": ["https://example.com/live_fail.mp4"]}}},
            ]
        },
    }

    success = await downloader._download_aweme_assets(
        aweme_data, author_name="测试作者", mode="post"
    )

    assert success is False
    assert any(path.name.endswith(".webp") for path in saved_paths)
    assert any(path.name.endswith("_live_1.mp4") for path in saved_paths)
    assert any(path.name.endswith("_live_2.mp4") for path in saved_paths)

    await api_client.close()


def test_detect_media_type_by_aweme_type(tmp_path):
    """aweme_type 2/68/150 should be detected as gallery even without images key."""
    downloader, api_client = _build_downloader(tmp_path)

    for aweme_type in (2, 68, 150):
        assert downloader._detect_media_type({"aweme_type": aweme_type}) == "gallery"

    assert downloader._detect_media_type({"aweme_type": 4}) == "video"
    assert downloader._detect_media_type({"aweme_type": 0}) == "video"
    assert downloader._detect_media_type({}) == "video"

    asyncio.run(api_client.close())


def test_collect_image_urls_old_format_url_list(tmp_path):
    """Old format: items have url_list directly."""
    downloader, api_client = _build_downloader(tmp_path)

    aweme_data = {
        "aweme_id": "100001",
        "images": [
            {"url_list": ["https://example.com/img1.webp"]},
            {"url_list": ["https://example.com/img2.webp"]},
        ],
    }

    urls = downloader._collect_image_urls(aweme_data)
    assert urls == [
        "https://example.com/img1.webp",
        "https://example.com/img2.webp",
    ]

    asyncio.run(api_client.close())


def test_collect_image_urls_old_format_prefers_url_list(tmp_path):
    """Old format: url_list is the no-watermark image source."""
    downloader, api_client = _build_downloader(tmp_path)

    aweme_data = {
        "aweme_id": "100002",
        "images": [
            {
                "url_list": ["https://example.com/preview1.webp"],
                "download_url_list": ["https://example.com/download1.webp"],
            },
        ],
    }

    urls = downloader._collect_image_urls(aweme_data)
    assert urls == ["https://example.com/preview1.webp"]

    asyncio.run(api_client.close())


def test_collect_image_urls_new_format_prefers_display_image(tmp_path):
    """New format: display_image is the no-watermark image source."""
    downloader, api_client = _build_downloader(tmp_path)

    aweme_data = {
        "aweme_id": "100003",
        "image_post_info": {
            "images": [
                {
                    "download_url": {"url_list": ["https://cdn.example.com/download.webp"]},
                    "display_image": {"url_list": ["https://cdn.example.com/display.webp"]},
                },
            ]
        },
    }

    urls = downloader._collect_image_urls(aweme_data)
    assert urls == ["https://cdn.example.com/display.webp"]

    asyncio.run(api_client.close())


def test_collect_image_urls_prefers_aweme_image_url_list_before_display_image(tmp_path):
    downloader, api_client = _build_downloader(tmp_path)

    aweme_data = {
        "aweme_id": "100003-url-list",
        "image_post_info": {
            "images": [
                {
                    "url_list": ["https://cdn.example.com/clean-from-aweme.webp"],
                    "display_image": {
                        "url_list": ["https://cdn.example.com/tplv-dy-water-v2/display.webp"]
                    },
                    "download_url_list": ["https://cdn.example.com/tplv-dy-water-v2/download.webp"],
                },
            ]
        },
    }

    urls = downloader._collect_image_urls(aweme_data)
    assert urls == ["https://cdn.example.com/clean-from-aweme.webp"]

    asyncio.run(api_client.close())


def test_collect_image_urls_prefers_non_watermark_gallery_fields(tmp_path):
    downloader, api_client = _build_downloader(tmp_path)

    aweme_data = {
        "aweme_id": "100004",
        "image_post_info": {
            "images": [
                {
                    "display_image": {"url_list": ["https://cdn.example.com/clean-display.webp"]},
                    "download_url": {
                        "url_list": ["https://cdn.example.com/tplv-dy-water-v2/water-download.webp"]
                    },
                    "owner_watermark_image": {
                        "url_list": ["https://cdn.example.com/owner_watermark_image.webp"]
                    },
                },
                {
                    "url_list": ["https://cdn.example.com/clean-top.webp"],
                    "download_url_list": [
                        "https://cdn.example.com/tplv-dy-water-v2/water-list.webp"
                    ],
                },
            ]
        },
    }

    urls = downloader._collect_image_urls(aweme_data)

    assert urls == [
        "https://cdn.example.com/clean-display.webp",
        "https://cdn.example.com/clean-top.webp",
    ]

    asyncio.run(api_client.close())


def test_iter_gallery_items_image_list_key(tmp_path):
    """Some responses use image_list instead of images."""
    downloader, api_client = _build_downloader(tmp_path)

    aweme_data = {
        "aweme_id": "100004",
        "image_post_info": {
            "image_list": [{"display_image": {"url_list": ["https://example.com/img.webp"]}}]
        },
    }

    items = downloader._iter_gallery_items(aweme_data)
    assert len(items) == 1
    assert items[0]["display_image"]["url_list"][0] == "https://example.com/img.webp"

    asyncio.run(api_client.close())


def test_iter_gallery_items_top_level_image_list(tmp_path):
    """Fallback: top-level image_list key."""
    downloader, api_client = _build_downloader(tmp_path)

    aweme_data = {
        "aweme_id": "100005",
        "image_list": [{"url_list": ["https://example.com/top.webp"]}],
    }

    items = downloader._iter_gallery_items(aweme_data)
    assert len(items) == 1

    asyncio.run(api_client.close())


def _paid_aweme_without_direct_urls():
    """付费作品：play_addr 无可用直连 URL，只剩 uri 与 download_addr 可构造。"""
    return {
        "aweme_id": "7640058716376583458",
        "charge_info": {
            "is_charge_content": True,
            "has_paid": False,
            "preview_config": {"is_preview": True, "start_time": 0, "end_time": 180000},
        },
        "video": {
            "play_addr": {"uri": "", "url_list": [], "data_size": 163679958},
            "download_addr": {
                "uri": "v0d00fg10000d83f1svog65hmig36qeg",
                "url_list": [],
                "data_size": 59874426,
            },
        },
    }


async def test_paid_content_never_falls_back_to_download_addr(tmp_path):
    """付费作品的 download_addr 是 CENC 密文，不能作为兜底源。"""
    downloader, _ = _build_downloader(tmp_path)
    aweme = _paid_aweme_without_direct_urls()

    assert downloader._build_video_url_candidates(aweme) == []


async def test_free_content_still_falls_back_to_download_addr(tmp_path):
    """免费作品的 play_addr 与 download_addr 是同一资产，兜底行为保持不变。"""
    downloader, _ = _build_downloader(tmp_path)
    aweme = _paid_aweme_without_direct_urls()
    aweme["charge_info"] = None

    candidates = downloader._build_video_url_candidates(aweme)

    assert len(candidates) == 1
    assert "v0d00fg10000d83f1svog65hmig36qeg" in candidates[0][0]


async def test_encrypted_download_is_discarded(tmp_path):
    """落盘的 CENC 密文必须删除并判失败，而不是当作下载成功。"""
    import struct

    downloader, _ = _build_downloader(tmp_path)

    def box(box_type, payload=b""):
        return struct.pack(">I", 8 + len(payload)) + box_type + payload

    sinf = box(
        b"sinf",
        box(b"frma", b"avc1") + box(b"schm", b"\x00\x00\x00\x00" + b"cenc" + b"\x00\x01\x00\x00"),
    )
    stsd = box(
        b"stsd",
        b"\x00\x00\x00\x00" + struct.pack(">I", 1) + box(b"encv", b"\x00" * 78 + sinf),
    )
    moov = box(b"moov", box(b"trak", box(b"mdia", box(b"minf", box(b"stbl", stsd)))))
    video_path = tmp_path / "paid.mp4"
    video_path.write_bytes(box(b"ftyp", b"isom") + moov)

    assert downloader._discard_if_encrypted(video_path, "7640058716376583458") is False
    assert not video_path.exists()


async def test_plaintext_download_is_kept(tmp_path):
    """明文 mp4 不受影响，检测失败也不该误删正常文件。"""
    import struct

    downloader, _ = _build_downloader(tmp_path)

    def box(box_type, payload=b""):
        return struct.pack(">I", 8 + len(payload)) + box_type + payload

    stsd = box(
        b"stsd",
        b"\x00\x00\x00\x00" + struct.pack(">I", 1) + box(b"avc1", b"\x00" * 78),
    )
    moov = box(b"moov", box(b"trak", box(b"mdia", box(b"minf", box(b"stbl", stsd)))))
    video_path = tmp_path / "free.mp4"
    video_path.write_bytes(box(b"ftyp", b"isom") + moov)

    assert downloader._discard_if_encrypted(video_path, "123") is True
    assert video_path.exists()


async def test_paid_content_skips_original_quality_probe(tmp_path):
    """付费作品不做 ratio=default 原画探测：探到的是同一份试看资产（实测
    大小逐字节相等），真正的「原片」是要不起的 CENC 全长正片。"""
    downloader, _ = _build_downloader(tmp_path)
    aweme = {
        "charge_info": {"is_charge_content": True, "has_paid": False},
        "video": {"play_addr": {"uri": "v0200abc", "data_size": 163679958}},
    }
    candidates = [("https://v26-web.douyinvod.com/plain.mp4", {})]
    probed = False

    async def _fail_if_probed(*args, **kwargs):
        nonlocal probed
        probed = True
        return ("https://cdn.example.com/original.mp4", 10**12)

    downloader._probe_original_play_source = _fail_if_probed

    result = await downloader._maybe_promote_original_candidate(aweme, candidates, None)

    assert probed is False
    assert result == candidates


async def test_free_content_still_probes_original_quality(tmp_path):
    """免费作品的原画探测行为保持不变。"""
    downloader, _ = _build_downloader(tmp_path)
    aweme = {
        "charge_info": None,
        "video": {"play_addr": {"uri": "v0300abc", "data_size": 1000}},
    }
    candidates = [("https://v26-web.douyinvod.com/plain.mp4", {})]

    async def _probe(*args, **kwargs):
        return ("https://cdn.example.com/original.mp4", 99999)

    downloader._probe_original_play_source = _probe

    result = await downloader._maybe_promote_original_candidate(aweme, candidates, None)

    assert result[0][0] == "https://cdn.example.com/original.mp4"


async def test_encrypted_video_aborts_before_recording_success(tmp_path, monkeypatch):
    """密文被丢弃后，这条作品不得进入 DB / 本地索引，也不得留下文件。"""
    import struct

    downloader, _ = _build_downloader(tmp_path)

    def box(box_type, payload=b""):
        return struct.pack(">I", 8 + len(payload)) + box_type + payload

    sinf = box(
        b"sinf",
        box(b"frma", b"avc1") + box(b"schm", b"\x00\x00\x00\x00" + b"cenc" + b"\x00\x01\x00\x00"),
    )
    stsd = box(
        b"stsd",
        b"\x00\x00\x00\x00" + struct.pack(">I", 1) + box(b"encv", b"\x00" * 78 + sinf),
    )
    ciphertext = box(b"ftyp", b"isom") + box(
        b"moov", box(b"trak", box(b"mdia", box(b"minf", box(b"stbl", stsd))))
    )

    async def _fake_download(candidates, save_path, session, **kwargs):
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_bytes(ciphertext)
        return True

    marked = []
    monkeypatch.setattr(downloader, "_download_video_with_fallback", _fake_download)
    monkeypatch.setattr(downloader, "_mark_local_aweme_downloaded", lambda i: marked.append(i))
    monkeypatch.setattr(downloader.api_client, "get_session", AsyncMock(return_value=None))

    aweme = {
        "aweme_id": "7640058716376583458",
        "desc": "paid",
        "create_time": 1747353600,
        "charge_info": {"is_charge_content": True, "has_paid": False},
        "video": {"play_addr": {"uri": "v0200abc", "url_list": ["https://cdn/x.mp4"]}},
    }

    ok = await downloader._download_aweme_assets(aweme, "作者", "post")

    assert ok is False
    assert marked == []
    assert not list(tmp_path.rglob("*.mp4"))
