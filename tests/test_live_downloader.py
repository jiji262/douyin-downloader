"""LiveDownloader 测试。

用 asyncio 伪造一个 aiohttp-like session，校验流选择、跳过非直播状态、成功路径。
"""

import threading
from pathlib import Path

from auth import CookieManager
from config import ConfigLoader
from control import QueueManager, RateLimiter, RetryHandler
from core import item_reasons
from core.api_client import DouyinAPIClient
from core.live_downloader import LiveDownloader
from core.live_session import LiveSession
from storage import FileManager


class _FakeStreamResponse:
    """模拟 aiohttp 流式响应。"""

    def __init__(self, chunks, status: int = 200):
        self._chunks = list(chunks)
        self.status = status
        self.content = self
        self._consumed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def iter_chunked(self, size):
        async def gen():
            for c in self._chunks:
                yield c

        return gen()


class _FakeSession:
    def __init__(self, chunks, status: int = 200):
        self._chunks = chunks
        self._status = status

    def get(self, url, headers=None, timeout=None):
        return _FakeStreamResponse(self._chunks, self._status)


class _Reporter:
    def __init__(self):
        self.items, self.dirs, self.totals = [], [], []

    def set_item_total(self, total, detail=""):
        self.totals.append(total)

    def advance_item(self, status, detail="", reason=""):
        self.items.append((status, detail, reason))

    def on_output_dir(self, *, path):
        self.dirs.append(path)


def _build_downloader(tmp_path, reporter=None):
    config = ConfigLoader()
    config.update(path=str(tmp_path))

    file_manager = FileManager(str(tmp_path))
    cookie_manager = CookieManager(str(tmp_path / ".cookies.json"))
    api_client = DouyinAPIClient({})

    return LiveDownloader(
        config,
        api_client,
        file_manager,
        cookie_manager,
        database=None,
        rate_limiter=RateLimiter(max_per_second=5),
        retry_handler=RetryHandler(max_retries=1),
        queue_manager=QueueManager(max_workers=1),
        progress_reporter=reporter,
    ), api_client


def test_select_best_stream_url_prefers_flv_origin():
    room = {
        "stream_url": {
            "flv_pull_url": {
                "SD": "https://cdn/sd.flv",
                "FULL_HD1": "https://cdn/fhd.flv",
                "HD1": "https://cdn/hd.flv",
            },
            "hls_pull_url_map": {
                "HD1": "https://cdn/hd.m3u8",
            },
        }
    }
    url, quality = LiveDownloader._select_best_stream_url(room)
    assert url == "https://cdn/fhd.flv"
    assert quality == "FULL_HD1"


def test_select_best_falls_back_to_hls():
    room = {
        "stream_url": {
            "hls_pull_url_map": {"HD1": "https://cdn/hd.m3u8", "SD": "https://cdn/sd.m3u8"},
        }
    }
    url, quality = LiveDownloader._select_best_stream_url(room)
    assert url == "https://cdn/hd.m3u8"
    assert quality == "HD1"


def test_select_best_returns_none_if_no_stream():
    assert LiveDownloader._select_best_stream_url({}) == (None, "")
    assert LiveDownloader._select_best_stream_url({"stream_url": {}}) == (None, "")


async def test_offline_room_is_skipped(tmp_path):
    downloader, client = _build_downloader(tmp_path)
    async def room(*args, **kwargs):
        return {"room": {"status": 4}, "user": {}}
    client.get_live_room_info = room
    result = await downloader.download({"room_id": "42"})
    assert result.skipped == 1 and result.success == 0
    await client.close()


async def test_internal_room_id_metadata_is_forwarded(tmp_path):
    downloader, client = _build_downloader(tmp_path)
    captured = {}

    async def room(room_id, **kwargs):
        captured.update(room_id=room_id, **kwargs)
        return {"room": {"status": 4}, "user": {}}

    client.get_live_room_info = room
    result = await downloader.download(
        {"room_id": "7664563379964595007", "room_id_kind": "room_id", "sec_user_id": "sec-test"}
    )
    assert result.skipped == 1
    assert captured == {
        "room_id": "7664563379964595007",
        "room_id_kind": "room_id",
        "sec_user_id": "sec-test",
    }
    await client.close()


async def test_stop_requested_while_queued_skips_without_room_lookup(tmp_path):
    reporter = _Reporter()
    downloader, client = _build_downloader(tmp_path, reporter)
    downloader.stop_event = threading.Event()
    downloader.stop_event.set()

    async def room(*args, **kwargs):
        raise AssertionError("排队时已停止，不应再查直播间")

    client.get_live_room_info = room
    result = await downloader.download({"room_id": "42"})
    assert result.skipped == 1 and result.success == 0
    assert reporter.items == [("skipped", "42", item_reasons.SKIP_LIVE_STOPPED)]
    await client.close()


async def test_segments_left_unsaved_report_save_failure_each(tmp_path, monkeypatch):
    reporter = _Reporter()
    downloader, client = _build_downloader(tmp_path, reporter)

    async def room(*args, **kwargs):
        return {
            "room": {
                "id_str": "room-1",
                "status": 2,
                "stream_url": {"flv_pull_url": {"HD1": "https://cdn.invalid/live.flv"}},
            },
            "user": {"nickname": "主播甲"},
        }

    directories = []

    async def fake_run(session, info, resolve):
        directories.append(str(session.directory))
        session.journal.data["segments"] = {
            "part-000000.ts": {"segment_id": "seg-0", "state": "indexed"},
            "part-000001.ts": {"segment_id": "seg-1", "state": "pending_finalize"},
        }
        session.reason = "stopped"
        return []

    client.get_live_room_info = room
    monkeypatch.setattr(LiveSession, "run", fake_run)
    result = await downloader.download({"room_id": "42"})
    assert (result.total, result.success, result.failed) == (2, 1, 1)
    assert result.incomplete_reason
    assert reporter.items == [
        ("success", "seg-0", ""),
        ("failed", "seg-1", item_reasons.FAIL_LIVE_SAVE),
    ]
    # 「打开输出文件夹」落到本次录制的分段目录，而不是作者目录。
    assert reporter.dirs == directories
    await client.close()


async def test_relative_output_root_is_persisted_as_absolute_paths(tmp_path, monkeypatch):
    from storage.live_recordings import RecordingJournal

    monkeypatch.chdir(tmp_path)
    reporter = _Reporter()
    config = ConfigLoader()
    config.update(path="downloads")
    client = DouyinAPIClient({})
    downloader = LiveDownloader(
        config, client, FileManager("downloads"), CookieManager(str(tmp_path / ".c.json")),
        database=None, progress_reporter=reporter,
    )
    created = []
    original = RecordingJournal.create.__func__

    async def spy(cls, root, *, directory, metadata):
        journal = await original(cls, root, directory=directory, metadata=metadata)
        created.append(journal)
        return journal

    async def room(*args, **kwargs):
        return {
            "room": {"id_str": "room-1", "status": 2,
                     "stream_url": {"flv_pull_url": {"HD1": "https://cdn.invalid/live.flv"}}},
            "user": {"nickname": "主播甲"},
        }

    async def no_data(session, info, resolve):
        session.reason = "stopped"
        return []

    monkeypatch.setattr(RecordingJournal, "create", classmethod(spy))
    monkeypatch.setattr(LiveSession, "run", no_data)
    client.get_live_room_info = room
    result = await downloader.download({"room_id": "42"})
    data = created[0].data
    assert data["directory"].startswith(str(tmp_path)) and data["metadata"]["base_path"].startswith(str(tmp_path))
    assert result.skipped == 1
    assert reporter.items == [("skipped", "room-1", item_reasons.SKIP_LIVE_STOPPED)]
    # 没录到内容时目录会被清掉：输出目录改报上级。
    assert reporter.dirs == [data["directory"], str(Path(data["directory"]).parent)]
    await client.close()
