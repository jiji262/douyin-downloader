"""单条目下载器（音乐 / 直播 / 直播回放）的跳过 / 失败原因。

这三类任务只有一个条目，原先失败时事件流里只有「失败 · 42」。这里钉住每个
已知出口结算时带上的人话原因。
"""

import asyncio
import errno

import aiohttp
import pytest

from auth import CookieManager
from config import ConfigLoader
from control import QueueManager, RateLimiter, RetryHandler
from core import item_reasons
from core.live_downloader import LiveDownloader
from core.live_replay_downloader import LiveReplayDownloader
from core.music_downloader import MusicDownloader
from storage import FileManager

MUSIC_ID = "7600224486650121999"
AWEME_ID = "7552196871758843145"
ROOM_ID = "42"
EPISODE_ID = "ep-1"


class _Reporter:
    def __init__(self):
        self.items = []

    def update_step(self, step, detail=""):
        pass

    def set_item_total(self, total, detail=""):
        pass

    def advance_item(self, status, detail="", reason=""):
        self.items.append((status, detail, reason))


class _FakeAPIClient:
    BASE_URL = "https://www.douyin.com"
    headers = {"User-Agent": "UnitTestAgent/1.0"}
    proxy = None

    def __init__(self, session=None):
        self._session = session

    async def get_session(self):
        return self._session


def _build(tmp_path, downloader_type, api_client):
    config = ConfigLoader()
    config.update(path=str(tmp_path), cover=False, json=False, music=False, avatar=False)
    retry_handler = RetryHandler(max_retries=1)
    retry_handler.retry_delays = [0]
    reporter = _Reporter()
    downloader = downloader_type(
        config,
        api_client,
        FileManager(str(tmp_path)),
        CookieManager(str(tmp_path / ".cookies.json")),
        database=None,
        rate_limiter=RateLimiter(max_per_second=100),
        retry_handler=retry_handler,
        queue_manager=QueueManager(max_workers=1),
        progress_reporter=reporter,
    )
    return downloader, reporter


async def _download_fails(*_args, **_kwargs):
    return False


# ---------------------------------------------------------------------------
# 音乐
# ---------------------------------------------------------------------------


class _MusicAPI(_FakeAPIClient):
    def __init__(self, *, detail=None, aweme=None):
        super().__init__()
        self._detail = detail
        self._aweme = aweme

    async def get_music_detail(self, _music_id):
        return self._detail

    async def get_music_aweme(self, _music_id, cursor=0, count=1):
        return {"items": [self._aweme] if self._aweme else []}


async def test_music_audio_download_failure_has_reason(tmp_path):
    api = _MusicAPI(detail={"title": "t", "play_url": {"url_list": ["https://cdn/m.mp3"]}})
    downloader, reporter = _build(tmp_path, MusicDownloader, api)
    downloader._download_with_retry = _download_fails

    result = await downloader.download({"music_id": MUSIC_ID})

    assert result.failed == 1
    assert reporter.items == [("failed", MUSIC_ID, item_reasons.FAIL_MUSIC_AUDIO)]
    assert downloader.item_reason_summary() == {
        "failed": [{"reason": item_reasons.FAIL_MUSIC_AUDIO, "count": 1}]
    }


async def test_music_without_audio_or_fallback_aweme_has_reason(tmp_path):
    downloader, reporter = _build(tmp_path, MusicDownloader, _MusicAPI(detail={"title": "t"}))

    result = await downloader.download({"music_id": MUSIC_ID})

    assert result.failed == 1
    assert reporter.items == [("failed", MUSIC_ID, item_reasons.FAIL_MUSIC_NO_SOURCE)]


async def test_music_fallback_aweme_skip_keeps_base_reason(tmp_path):
    api = _MusicAPI(detail={"title": "t"}, aweme={"aweme_id": AWEME_ID, "author": {}})
    downloader, reporter = _build(tmp_path, MusicDownloader, api)
    downloader._local_aweme_ids = {AWEME_ID}

    result = await downloader.download({"music_id": MUSIC_ID})

    assert result.skipped == 1
    assert reporter.items == [("skipped", AWEME_ID, item_reasons.SKIP_LOCAL_FILE_EXISTS)]


async def test_music_fallback_aweme_failure_keeps_base_reason(tmp_path):
    aweme = {"aweme_id": AWEME_ID, "author": {"nickname": "a"}, "video": {}}
    downloader, reporter = _build(tmp_path, MusicDownloader, _MusicAPI(aweme=aweme))

    result = await downloader.download({"music_id": MUSIC_ID})

    assert result.failed == 1
    assert reporter.items == [("failed", AWEME_ID, item_reasons.FAIL_NO_VIDEO_URL)]


# ---------------------------------------------------------------------------
# 直播
# ---------------------------------------------------------------------------


class _StreamResponse:
    def __init__(self, status, chunks, error):
        self.status = status
        self.content = self
        self._chunks = chunks
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def iter_chunked(self, _size):
        async def gen():
            for chunk in self._chunks:
                yield chunk
            if self._error is not None:
                raise self._error

        return gen()


class _StreamSession:
    def __init__(self, *, status=200, chunks=(), error=None):
        self._response = _StreamResponse(status, list(chunks), error)

    def get(self, _url, headers=None, timeout=None):
        return self._response


class _LiveAPI(_FakeAPIClient):
    def __init__(self, info, session=None):
        super().__init__(session)
        self._info = info

    async def get_live_room_info(self, _room_id, **_kwargs):
        return self._info


def _live_info(status=2, stream_url=None):
    if stream_url is None:
        stream_url = {"flv_pull_url": {"ORIGIN": "https://cdn/live.flv"}}
    return {
        "room": {"status": status, "title": "t", "stream_url": stream_url},
        "user": {"nickname": "主播"},
    }


@pytest.mark.parametrize(
    ("info", "expected"),
    [
        (None, ("failed", ROOM_ID, item_reasons.FAIL_LIVE_ROOM_INFO)),
        (_live_info(status=4), ("skipped", ROOM_ID, item_reasons.SKIP_LIVE_NOT_STREAMING)),
        (_live_info(stream_url={}), ("failed", ROOM_ID, item_reasons.FAIL_LIVE_NO_STREAM)),
    ],
)
async def test_live_room_exits_have_reasons(tmp_path, info, expected):
    downloader, reporter = _build(tmp_path, LiveDownloader, _LiveAPI(info))

    await downloader.download({"room_id": ROOM_ID})

    assert reporter.items == [expected]


@pytest.mark.parametrize(
    ("session", "expected"),
    [
        (_StreamSession(status=403), item_reasons.FAIL_LIVE_STREAM_REJECTED),
        (_StreamSession(), item_reasons.FAIL_LIVE_NO_DATA),
        (
            _StreamSession(error=asyncio.TimeoutError("sock_read idle")),
            item_reasons.FAIL_LIVE_NO_DATA,
        ),
        (
            _StreamSession(error=aiohttp.ClientConnectionError("reset")),
            item_reasons.FAIL_UNEXPECTED,
        ),
        (
            _StreamSession(error=OSError(errno.ENOSPC, "No space left on device")),
            item_reasons.FAIL_WRITE_ERROR,
        ),
    ],
)
async def test_live_recording_failures_have_reasons(tmp_path, session, expected):
    downloader, reporter = _build(tmp_path, LiveDownloader, _LiveAPI(_live_info(), session))

    result = await downloader.download({"room_id": ROOM_ID})

    assert result.failed == 1
    assert reporter.items == [("failed", ROOM_ID, expected)]


async def test_live_recording_save_failure_has_reason(tmp_path, monkeypatch):
    session = _StreamSession(chunks=[b"data"])
    downloader, reporter = _build(tmp_path, LiveDownloader, _LiveAPI(_live_info(), session))

    def _replace_fails(*_args):
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr("core.live_downloader.os.replace", _replace_fails)

    result = await downloader.download({"room_id": ROOM_ID})

    assert result.failed == 1
    assert reporter.items == [("failed", ROOM_ID, item_reasons.FAIL_LIVE_SAVE)]


async def test_live_recording_partial_data_after_error_succeeds_without_reason(tmp_path):
    session = _StreamSession(chunks=[b"data"], error=aiohttp.ClientConnectionError("reset"))
    downloader, reporter = _build(tmp_path, LiveDownloader, _LiveAPI(_live_info(), session))

    result = await downloader.download({"room_id": ROOM_ID})

    assert result.success == 1
    assert reporter.items == [("success", ROOM_ID, "")]
    assert downloader.item_reason_summary() == {}


# ---------------------------------------------------------------------------
# 直播回放
# ---------------------------------------------------------------------------

_EPISODE = {"attach_room_id_str": "room-1", "owner": {"nickname": "主播甲"}}
_VIDEO_TRACK = {"height": 720, "main": "https://cdn/video.mp4"}
_AUDIO_TRACK = {"height": 0, "main": "https://cdn/audio.mp4"}


def _replay(*tracks):
    return {"title": "回放", "video_info": {"unfold_play_info": {"play_urls": list(tracks)}}}


class _ReplayAPI(_FakeAPIClient):
    def __init__(self, episode, replay):
        super().__init__()
        self._episode = episode
        self._replay = replay

    async def get_live_replay_episode(self, _episode_id):
        return self._episode

    async def get_live_replay_info(self, _episode_id, _room_id, replay_id=None):
        return self._replay


@pytest.mark.parametrize(
    ("episode", "replay", "expected"),
    [
        (None, None, item_reasons.FAIL_REPLAY_NOT_FOUND),
        ({"owner": {}}, None, item_reasons.FAIL_REPLAY_NO_ROOM),
        (_EPISODE, None, item_reasons.FAIL_REPLAY_NO_PLAYBACK),
        (_EPISODE, _replay(_AUDIO_TRACK), item_reasons.FAIL_REPLAY_NO_VIDEO_TRACK),
        (_EPISODE, _replay(_VIDEO_TRACK, _AUDIO_TRACK), item_reasons.FAIL_REPLAY_VIDEO_TRACK),
    ],
)
async def test_live_replay_failures_have_reasons(tmp_path, episode, replay, expected):
    downloader, reporter = _build(tmp_path, LiveReplayDownloader, _ReplayAPI(episode, replay))
    downloader._download_with_retry = _download_fails

    result = await downloader.download({"episode_id": EPISODE_ID})

    assert result.failed == 1
    assert reporter.items == [("failed", EPISODE_ID, expected)]


async def test_live_replay_missing_episode_id_keeps_detail_and_has_reason(tmp_path):
    downloader, reporter = _build(tmp_path, LiveReplayDownloader, _ReplayAPI(None, None))

    result = await downloader.download({})

    assert result.failed == 1
    assert reporter.items == [("failed", "missing episode_id", item_reasons.FAIL_REPLAY_MISSING_ID)]
