"""视频下载的「活着」信号与单条作品总时限。

线上 job f25a60b4850f 的队尾一条视频静默 5 分钟：批量任务原先只在整条
作品全部资产下完后才 ``advance_item``，单个文件下载途中不发任何事件，
UI 停在 97% 不动。同时 ``_download_video_with_fallback`` 的超时预算会
相乘（4 轮 × N 候选 × 每次 300s），单条视频最坏可挂到一小时以上。
"""

import asyncio

import pytest

from auth import CookieManager
from config import ConfigLoader
from control import QueueManager, RateLimiter, RetryHandler
from core import downloader_base
from core.api_client import DouyinAPIClient
from core.video_downloader import VideoDownloader
from storage import FileManager


class _RecordingReporter:
    """只记录事件的 reporter，字段与 control.progress_reporter 协议对齐。"""

    def __init__(self):
        self.events = []

    def update_step(self, step, detail=""):
        self.events.append(("update_step", step, detail))

    def set_item_total(self, total, detail=""):
        self.events.append(("set_item_total", total, detail))

    def advance_item(self, status, detail=""):
        self.events.append(("advance_item", status, detail))

    def on_item_progress(self, *, aweme_id, bytes_read, bytes_total):
        self.events.append(("on_item_progress", aweme_id, bytes_read, bytes_total))

    def progress_events(self):
        return [e for e in self.events if e[0] == "on_item_progress"]


def _build_downloader(tmp_path, *, reporter=None, max_retries: int = 3):
    config = ConfigLoader()
    config.update(path=str(tmp_path))

    retry_handler = RetryHandler(max_retries=max_retries)
    retry_handler.retry_delays = [0]

    return VideoDownloader(
        config,
        DouyinAPIClient({}),
        FileManager(str(tmp_path)),
        CookieManager(str(tmp_path / ".cookies.json")),
        database=None,
        rate_limiter=RateLimiter(max_per_second=100),
        retry_handler=retry_handler,
        queue_manager=QueueManager(max_workers=1),
        progress_reporter=reporter,
    )


@pytest.fixture(autouse=True)
def _clear_local_index_cache():
    downloader_base._LOCAL_AWEME_INDEX_CACHE.clear()
    yield
    downloader_base._LOCAL_AWEME_INDEX_CACHE.clear()


# ---------------------------------------------------------------------------
# 下载中进度事件
# ---------------------------------------------------------------------------


async def test_video_download_streams_progress_to_reporter(tmp_path, monkeypatch):
    reporter = _RecordingReporter()
    downloader = _build_downloader(tmp_path, reporter=reporter)

    async def _fake_download_file(_url, _save_path, _session, **kwargs):
        on_progress = kwargs.get("on_progress")
        assert on_progress is not None, "视频下载必须把进度回调透传给 FileManager"
        on_progress(1024, 4096)
        on_progress(4096, 4096)
        return True

    monkeypatch.setattr(downloader.file_manager, "download_file", _fake_download_file)

    ok = await downloader._download_video_with_fallback(
        [("https://v3.douyinvod.com/v.mp4", {})],
        tmp_path / "v.mp4",
        session=None,
        aweme_id="7670780733078248626",
    )

    assert ok is True
    assert reporter.progress_events() == [
        ("on_item_progress", "7670780733078248626", 1024, 4096),
        ("on_item_progress", "7670780733078248626", 4096, 4096),
    ]


async def test_video_download_without_aweme_id_skips_progress(tmp_path, monkeypatch):
    """没有 aweme_id 时不发进度事件——renderer 用 id 匹配当前项。"""
    reporter = _RecordingReporter()
    downloader = _build_downloader(tmp_path, reporter=reporter)

    async def _fake_download_file(_url, _save_path, _session, **kwargs):
        assert kwargs.get("on_progress") is None
        return True

    monkeypatch.setattr(downloader.file_manager, "download_file", _fake_download_file)

    ok = await downloader._download_video_with_fallback(
        [("https://v3.douyinvod.com/v.mp4", {})], tmp_path / "v.mp4", session=None
    )

    assert ok is True
    assert reporter.progress_events() == []


async def test_aweme_assets_pass_aweme_id_into_video_download(tmp_path, monkeypatch):
    """端到端接线：_download_aweme_assets 必须把 aweme_id 传下去，
    否则进度事件永远发不出来。"""
    downloader = _build_downloader(tmp_path)
    seen = {}

    async def _fake_fallback(_candidates, save_path, _session, *, aweme_id=None):
        seen["aweme_id"] = aweme_id
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_bytes(b"x")
        return True

    monkeypatch.setattr(downloader, "_download_video_with_fallback", _fake_fallback)
    monkeypatch.setattr(downloader.api_client, "get_session", _noop_session)

    aweme = {
        "aweme_id": "7670780733078248626",
        "desc": "认知觉醒的本质。",
        "create_time": 1786000000,
        "author": {"nickname": "悦润禾", "uid": "1"},
        "video": {"play_addr": {"url_list": ["https://v3.douyinvod.com/v.mp4?watermark=0"]}},
    }
    await downloader._download_aweme_assets(aweme, "悦润禾", mode="post")

    assert seen["aweme_id"] == "7670780733078248626"


async def _noop_session():
    return None


# ---------------------------------------------------------------------------
# 单条作品总时限
# ---------------------------------------------------------------------------


async def test_video_download_gives_up_at_item_deadline(tmp_path, monkeypatch):
    """候选 × 重试轮的超时预算相乘时，单条作品仍必须在总时限内收敛。"""
    downloader = _build_downloader(tmp_path, max_retries=3)
    monkeypatch.setattr(downloader_base, "_VIDEO_ITEM_DEADLINE_S", 0.2)

    async def _hang(*_args, **_kwargs):
        await asyncio.sleep(30)
        return True

    monkeypatch.setattr(downloader.file_manager, "download_file", _hang)

    ok = await asyncio.wait_for(
        downloader._download_video_with_fallback(
            [("https://a.douyinvod.com/v.mp4", {}), ("https://b.douyinvod.com/v.mp4", {})],
            tmp_path / "v.mp4",
            session=None,
        ),
        timeout=5,
    )

    assert ok is False


def test_item_deadline_is_bounded():
    """时限要留足大文件余量，但必须远小于旧的最坏情况（4 轮 × 4 候选 × 300s）。"""
    assert 300 <= downloader_base._VIDEO_ITEM_DEADLINE_S <= 1800
