import json
from datetime import datetime

import pytest
from auth import CookieManager
from config import ConfigLoader
from control import QueueManager, RateLimiter, RetryHandler
from core.api_client import DouyinAPIClient
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


@pytest.mark.asyncio
async def test_video_downloader_skip_counts_total(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path)

    async def _fake_should_download(self, _):
        return False

    downloader._should_download = _fake_should_download.__get__(
        downloader, VideoDownloader
    )

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

    downloader._should_download = _fake_should_download.__get__(
        downloader, VideoDownloader
    )
    monkeypatch.setattr(api_client, "get_video_detail", _fake_get_video_detail)
    downloader._download_aweme = _fake_download_aweme.__get__(
        downloader, VideoDownloader
    )

    result = await downloader.download({"aweme_id": "123"})

    assert result.total == 1
    assert result.success == 1
    assert reporter.item_totals == [(1, "单视频下载")]
    assert ("下载作品", "单视频资源下载中") in reporter.step_updates
    assert reporter.item_events == [("success", "123")]

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
                "url_list": [
                    "https://www.douyin.com/aweme/v1/play/?video_id=1&watermark=0"
                ]
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
async def test_should_download_skips_when_aweme_exists_locally(tmp_path):
    downloader, api_client = _build_downloader(tmp_path)
    aweme_id = "7600223638943468863"

    existing_file = tmp_path / f"2026-02-18_demo_{aweme_id}.mp4"
    existing_file.write_bytes(b"1")

    should_download = await downloader._should_download(aweme_id)
    assert should_download is False

    await api_client.close()


@pytest.mark.asyncio
async def test_download_aweme_assets_uses_publish_date_and_writes_manifest(
    tmp_path, monkeypatch
):
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(
        music=False, cover=False, avatar=False, json=False, folderstyle=True
    )

    async def _fake_get_session():
        return object()

    monkeypatch.setattr(api_client, "get_session", _fake_get_session)

    saved_paths = []

    async def _fake_download_with_retry(self, _url, save_path, _session, **_kwargs):
        saved_paths.append(save_path)
        return True

    downloader._download_with_retry = _fake_download_with_retry.__get__(
        downloader, VideoDownloader
    )

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
async def test_download_aweme_assets_keeps_success_when_transcript_skipped(
    tmp_path, monkeypatch
):
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

    downloader._download_with_retry = _fake_download_with_retry.__get__(
        downloader, VideoDownloader
    )

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
