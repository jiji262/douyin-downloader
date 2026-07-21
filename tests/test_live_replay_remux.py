import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from auth import CookieManager
from config import ConfigLoader
from control import QueueManager, RateLimiter, RetryHandler
from core.api_client import DouyinAPIClient
from core.live_replay_downloader import LiveReplayDownloader
from storage import FileManager


class _FakeRemuxProcess:
    def __init__(self, *, block=False, error=None):
        self.block = block
        self.error = error
        self.returncode = None if block or error else 0
        self.pid = 123
        self.killed = False
        self.reaped = False

    async def communicate(self):
        if self.error:
            raise self.error
        if self.block:
            await asyncio.Event().wait()
        return b"", b""

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        self.reaped = True
        return self.returncode


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


@pytest.mark.asyncio
async def test_remux_prefers_bundled_ffmpeg_and_keeps_mp4_temp_suffix(monkeypatch, tmp_path: Path):
    downloader, api_client = _build_downloader(tmp_path)
    bundled = tmp_path / "ffmpeg.exe"
    bundled.write_bytes(b"binary")
    monkeypatch.setitem(
        sys.modules,
        "imageio_ffmpeg",
        SimpleNamespace(get_ffmpeg_exe=lambda: str(bundled)),
    )
    monkeypatch.setattr("core.ffmpeg.shutil.which", lambda _name: "PATH-ffmpeg")
    calls = []

    async def fake_create(*args, **_kwargs):
        calls.append(args)
        Path(args[-1]).write_bytes(b"merged")
        return _FakeRemuxProcess()

    monkeypatch.setattr("core.live_replay_downloader.asyncio.create_subprocess_exec", fake_create)
    output = tmp_path / "replay.mp4"

    assert await downloader._remux_tracks(tmp_path / "video.mp4", tmp_path / "audio.mp4", output)
    assert calls[0][0] == str(bundled)
    assert calls[0][-1].endswith(".tmp.mp4")
    assert output.read_bytes() == b"merged"
    await api_client.close()


@pytest.mark.asyncio
async def test_remux_falls_back_to_path_ffmpeg(monkeypatch, tmp_path: Path):
    downloader, api_client = _build_downloader(tmp_path)
    monkeypatch.setitem(
        sys.modules,
        "imageio_ffmpeg",
        SimpleNamespace(get_ffmpeg_exe=lambda: str(tmp_path / "missing-ffmpeg.exe")),
    )
    monkeypatch.setattr("core.ffmpeg.shutil.which", lambda _name: "PATH-ffmpeg")
    calls = []

    async def fake_create(*args, **_kwargs):
        calls.append(args)
        Path(args[-1]).write_bytes(b"merged")
        return _FakeRemuxProcess()

    monkeypatch.setattr("core.live_replay_downloader.asyncio.create_subprocess_exec", fake_create)

    assert await downloader._remux_tracks(
        tmp_path / "video.mp4", tmp_path / "audio.mp4", tmp_path / "replay.mp4"
    )
    assert calls[0][0] == "PATH-ffmpeg"
    await api_client.close()


@pytest.mark.asyncio
async def test_remux_timeout_kills_reaps_and_removes_partial_output(monkeypatch, tmp_path: Path):
    downloader, api_client = _build_downloader(tmp_path)
    process = _FakeRemuxProcess(block=True)
    monkeypatch.setattr("core.ffmpeg.shutil.which", lambda _name: "ffmpeg")
    monkeypatch.setattr("core.live_replay_downloader._REMUX_TIMEOUT_SECONDS", 0.01, raising=False)

    async def fake_create(*args, **_kwargs):
        Path(args[-1]).write_bytes(b"partial")
        return process

    monkeypatch.setattr("core.live_replay_downloader.asyncio.create_subprocess_exec", fake_create)

    result = await asyncio.wait_for(
        downloader._remux_tracks(
            tmp_path / "video.mp4", tmp_path / "audio.mp4", tmp_path / "replay.mp4"
        ),
        timeout=1.0,
    )

    assert result is False
    assert process.killed is True
    assert process.reaped is True
    assert not list(tmp_path.glob("*.tmp.mp4"))
    await api_client.close()


@pytest.mark.asyncio
async def test_remux_subprocess_error_kills_reaps_and_removes_partial_output(
    monkeypatch, tmp_path: Path
):
    downloader, api_client = _build_downloader(tmp_path)
    process = _FakeRemuxProcess(error=RuntimeError("pipe failed"))
    monkeypatch.setattr("core.ffmpeg.shutil.which", lambda _name: "ffmpeg")

    async def fake_create(*args, **_kwargs):
        Path(args[-1]).write_bytes(b"partial")
        return process

    monkeypatch.setattr("core.live_replay_downloader.asyncio.create_subprocess_exec", fake_create)

    result = await downloader._remux_tracks(
        tmp_path / "video.mp4", tmp_path / "audio.mp4", tmp_path / "replay.mp4"
    )

    assert result is False
    assert process.killed is True
    assert process.reaped is True
    assert not list(tmp_path.glob("*.tmp.mp4"))
    await api_client.close()


@pytest.mark.asyncio
async def test_remux_cancellation_kills_reaps_and_removes_partial_output(
    monkeypatch, tmp_path: Path
):
    downloader, api_client = _build_downloader(tmp_path)
    process = _FakeRemuxProcess(block=True)
    started = asyncio.Event()
    monkeypatch.setattr("core.ffmpeg.shutil.which", lambda _name: "ffmpeg")

    async def fake_create(*args, **_kwargs):
        Path(args[-1]).write_bytes(b"partial")
        started.set()
        return process

    monkeypatch.setattr("core.live_replay_downloader.asyncio.create_subprocess_exec", fake_create)
    task = asyncio.create_task(
        downloader._remux_tracks(
            tmp_path / "video.mp4", tmp_path / "audio.mp4", tmp_path / "replay.mp4"
        )
    )
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.killed is True
    assert process.reaped is True
    assert not list(tmp_path.glob("*.tmp.mp4"))
    await api_client.close()
