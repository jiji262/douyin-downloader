import json

import pytest

from auth import CookieManager
from config import ConfigLoader
from control import QueueManager, RateLimiter, RetryHandler
from core.api_client import DouyinAPIClient
from core.live_replay_downloader import LiveReplayDownloader
from storage import FileManager


def _build_downloader(tmp_path):
    config = ConfigLoader()
    config.update(path=str(tmp_path))

    file_manager = FileManager(str(tmp_path))
    cookie_manager = CookieManager(str(tmp_path / ".cookies.json"))
    api_client = DouyinAPIClient({})

    return LiveReplayDownloader(
        config,
        api_client,
        file_manager,
        cookie_manager,
        database=None,
        rate_limiter=RateLimiter(max_per_second=5),
        retry_handler=RetryHandler(max_retries=1),
        queue_manager=QueueManager(max_workers=1),
    ), api_client


def _manifest_records(tmp_path):
    return [
        json.loads(line)
        for line in (tmp_path / "download_manifest.jsonl").read_text(encoding="utf-8").splitlines()
    ]


def test_select_playback_tracks_prefers_highest_video_and_audio():
    video, audio = LiveReplayDownloader._select_playback_tracks(
        [
            {"height": 480, "width": 854, "main": "https://cdn/480-video.mp4"},
            {"height": 0, "width": 0, "main": "https://cdn/audio.mp4"},
            {"height": 1080, "width": 1920, "main": "https://cdn/1080-video.mp4"},
            {"height": 720, "width": 1280, "backup": "https://cdn/720-video.mp4"},
        ]
    )

    assert video == "https://cdn/1080-video.mp4"
    assert audio == "https://cdn/audio.mp4"


@pytest.mark.asyncio
async def test_live_replay_downloader_downloads_tracks_and_remuxes(tmp_path):
    downloader, api_client = _build_downloader(tmp_path)

    async def fake_episode(episode_id):
        return {
            "episode_id_str": episode_id,
            "attach_room_id_str": "room-1",
            "owner": {"nickname": "主播甲"},
            "episode_extra_basic_info": {"room_start_time": 1706960719},
        }

    async def fake_replay_info(episode_id, room_id, replay_id=None):
        assert episode_id == "ep-1"
        assert room_id == "room-1"
        return {
            "episode_id_str": episode_id,
            "title": "直播回放标题",
            "video_info": {
                "duration": 9980,
                "unfold_play_info": {
                    "play_urls": [
                        {"height": 720, "main": "https://cdn/video.mp4"},
                        {"height": 0, "main": "https://cdn/audio.mp4"},
                    ]
                },
            },
        }

    downloads = []

    async def fake_download_track(url, target_path):
        downloads.append((url, target_path.name))
        target_path.write_bytes(url.encode("utf-8"))
        return True

    async def fake_remux(video_path, audio_path, output_path):
        output_path.write_bytes(video_path.read_bytes() + b"\n" + audio_path.read_bytes())
        return True

    api_client.get_live_replay_episode = fake_episode
    api_client.get_live_replay_info = fake_replay_info
    downloader._download_track = fake_download_track
    downloader._remux_tracks = fake_remux

    result = await downloader.download({"episode_id": "ep-1"})

    assert result.total == 1
    assert result.success == 1
    assert result.failed == 0
    assert downloads == [
        ("https://cdn/video.mp4", "2024-02-03_1945_直播回放标题_ep-1.video.mp4"),
        ("https://cdn/audio.mp4", "2024-02-03_1945_直播回放标题_ep-1.audio.mp4"),
    ]
    outputs = [p for p in tmp_path.rglob("*.mp4") if not p.name.endswith((".video.mp4", ".audio.mp4"))]
    assert len(outputs) == 1
    assert outputs[0].read_bytes() == b"https://cdn/video.mp4\nhttps://cdn/audio.mp4"

    await api_client.close()


@pytest.mark.asyncio
async def test_live_replay_downloader_fails_when_episode_missing(tmp_path):
    downloader, api_client = _build_downloader(tmp_path)

    async def fake_episode(episode_id):
        return None

    api_client.get_live_replay_episode = fake_episode

    result = await downloader.download({"episode_id": "missing"})

    assert result.total == 1
    assert result.failed == 1
    assert result.success == 0

    await api_client.close()


@pytest.mark.asyncio
async def test_live_replay_downloader_preserves_tracks_when_remux_fails(tmp_path):
    downloader, api_client = _build_downloader(tmp_path)

    async def fake_episode(episode_id):
        return {
            "episode_id_str": episode_id,
            "attach_room_id_str": "room-1",
            "owner": {"nickname": "主播甲"},
            "episode_extra_basic_info": {"room_start_time": 1706960719},
        }

    async def fake_replay_info(episode_id, room_id, replay_id=None):
        return {
            "episode_id_str": episode_id,
            "title": "直播回放标题",
            "video_info": {
                "unfold_play_info": {
                    "play_urls": [
                        {"height": 720, "main": "https://cdn/video.mp4"},
                        {"height": 0, "main": "https://cdn/audio.mp4"},
                    ]
                },
            },
        }

    async def fake_download_track(url, target_path):
        target_path.write_bytes(url.encode("utf-8"))
        return True

    async def fake_remux(video_path, audio_path, output_path):
        return False

    api_client.get_live_replay_episode = fake_episode
    api_client.get_live_replay_info = fake_replay_info
    downloader._download_track = fake_download_track
    downloader._remux_tracks = fake_remux

    result = await downloader.download({"episode_id": "ep-1"})

    assert result.success == 1
    assert result.failed == 0
    assert [p.read_bytes() for p in tmp_path.rglob("*.video.mp4")] == [b"https://cdn/video.mp4"]
    assert [p.read_bytes() for p in tmp_path.rglob("*.audio.mp4")] == [b"https://cdn/audio.mp4"]
    assert len(list(tmp_path.rglob("*.mp4"))) == 2
    assert _manifest_records(tmp_path)[-1]["remux_status"] == "remux_failed"

    await api_client.close()


@pytest.mark.asyncio
async def test_live_replay_downloader_fails_when_room_id_missing(tmp_path):
    downloader, api_client = _build_downloader(tmp_path)

    async def fake_episode(episode_id):
        return {"episode_id_str": episode_id}

    api_client.get_live_replay_episode = fake_episode

    result = await downloader.download({"episode_id": "ep-1"})

    assert result.failed == 1
    assert result.success == 0

    await api_client.close()


@pytest.mark.asyncio
async def test_live_replay_downloader_fails_when_no_playable_video(tmp_path):
    downloader, api_client = _build_downloader(tmp_path)

    async def fake_episode(episode_id):
        return {"episode_id_str": episode_id, "attach_room_id_str": "room-1"}

    async def fake_replay_info(episode_id, room_id, replay_id=None):
        return {"episode_id_str": episode_id, "video_info": {"unfold_play_info": {"play_urls": []}}}

    api_client.get_live_replay_episode = fake_episode
    api_client.get_live_replay_info = fake_replay_info

    result = await downloader.download({"episode_id": "ep-1"})

    assert result.failed == 1
    assert result.success == 0

    await api_client.close()


@pytest.mark.asyncio
async def test_live_replay_downloader_preserves_video_when_audio_download_fails(tmp_path):
    downloader, api_client = _build_downloader(tmp_path)

    async def fake_episode(episode_id):
        return {
            "episode_id_str": episode_id,
            "attach_room_id_str": "room-1",
            "owner": {"nickname": "主播甲"},
            "episode_extra_basic_info": {"room_start_time": 1706960719},
        }

    async def fake_replay_info(episode_id, room_id, replay_id=None):
        return {
            "episode_id_str": episode_id,
            "title": "直播回放标题",
            "video_info": {
                "unfold_play_info": {
                    "play_urls": [
                        {"height": 720, "main": "https://cdn/video.mp4"},
                        {"height": 0, "main": "https://cdn/audio.mp4"},
                    ]
                },
            },
        }

    async def fake_download_track(url, target_path):
        if "audio" in url:
            return False
        target_path.write_bytes(url.encode("utf-8"))
        return True

    api_client.get_live_replay_episode = fake_episode
    api_client.get_live_replay_info = fake_replay_info
    downloader._download_track = fake_download_track

    result = await downloader.download({"episode_id": "ep-1"})

    assert result.success == 1
    assert result.failed == 0
    assert [p.read_bytes() for p in tmp_path.rglob("*.video.mp4")] == [b"https://cdn/video.mp4"]
    assert list(tmp_path.rglob("*.audio.mp4")) == []
    assert _manifest_records(tmp_path)[-1]["remux_status"] == "audio_download_failed"

    await api_client.close()
