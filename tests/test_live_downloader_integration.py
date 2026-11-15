"""用实际 FFmpeg 和本机 HTTP 流验收分段、历史与清单。"""

import asyncio
import json
from pathlib import Path

import pytest
from aiohttp import web

from auth import CookieManager
from config import ConfigLoader
from core.api_client import DouyinAPIClient
from core.ffmpeg import resolve_ffmpeg_path
from core.live_downloader import LiveDownloader
from storage import Database, FileManager


async def make_source(tmp_path):
    binary = resolve_ffmpeg_path()
    if not binary:
        pytest.skip("FFmpeg 不可用")
    path = tmp_path / "fixture.flv"
    child = await asyncio.create_subprocess_exec(
        binary,
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=160x90:rate=10",
        "-f",
        "lavfi",
        "-i",
        "sine=sample_rate=48000",
        "-t",
        "3",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-g",
        "10",
        "-c:a",
        "aac",
        "-f",
        "flv",
        str(path),
    )
    assert await child.wait() == 0
    return path.read_bytes()


@pytest.mark.parametrize("folder", ["plain", "100% live"])
async def test_real_segments_registered_and_playable(tmp_path, folder):
    tmp_path = tmp_path / folder
    tmp_path.mkdir()
    payload = await make_source(tmp_path)
    requests = []

    async def stream(request):
        requests.append(dict(request.headers))
        return web.Response(body=payload, content_type="video/x-flv")

    app = web.Application()
    app.router.add_get("/live.flv", stream)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    config = ConfigLoader()
    config.update(path=str(tmp_path), live={"segment_seconds": 1})
    db = Database(str(tmp_path / "history.db"))
    await db.initialize()
    async with DouyinAPIClient({}) as client:
        calls = 0

        async def room(*args, **kwargs):
            nonlocal calls
            calls += 1
            return {
                "room": {
                    "id_str": "room-1",
                    "status": 2 if calls == 1 else 4,
                    "stream_url": {"flv_pull_url": {"HD1": "http://127.0.0.1:%s/live.flv" % port}},
                },
                "user": {"nickname": "Test"},
            }

        client.get_live_room_info = room
        downloader = LiveDownloader(
            config, client, FileManager(str(tmp_path)), CookieManager(), database=db
        )
        try:
            result = await asyncio.wait_for(downloader.download({"room_id": "42"}), 15)
        finally:
            await runner.cleanup()
            await db.close()
    assert result.success == 3 and result.failed == 0
    assert len(requests) == 1 and requests[0]["Origin"] == "https://live.douyin.com"
    await assert_saved_media(tmp_path)


async def assert_saved_media(tmp_path):
    rows = [
        json.loads(line) for line in (tmp_path / "download_manifest.jsonl").read_text().splitlines()
    ]
    assert len({row["aweme_id"] for row in rows}) == 3
    for row in rows:
        path = Path(row["file_path"])
        assert path.suffix == ".mp4" and path.stat().st_size > 0
        proc = await asyncio.create_subprocess_exec(
            resolve_ffmpeg_path(), "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"
        )
        assert await proc.wait() == 0
