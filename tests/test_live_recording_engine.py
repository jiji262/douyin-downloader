"""录制进程与分段收尾的行为契约。"""

import asyncio
import json
import threading

import pytest

from core import live_process
from core.live_process import recording_command, stop_process
from core.live_streams import select_stream
from storage.live_recordings import RecordingJournal


def test_sdk_origin_beats_legacy_flv_and_rejects_non_http():
    sdk = {"data": {"origin": {"main": {"flv": "https://cdn/origin.flv"}}}}
    room = {
        "stream_url": {
            "flv_pull_url": {"FULL_HD1": "https://cdn/hd.flv"},
            "live_core_sdk_data": {"pull_data": {"stream_data": json.dumps(sdk)}},
        }
    }
    assert select_stream(room) == ("https://cdn/origin.flv", "origin")
    room["stream_url"]["live_core_sdk_data"]["pull_data"]["stream_data"] = "invalid"
    assert select_stream(room) == ("https://cdn/hd.flv", "FULL_HD1")
    assert select_stream({"stream_url": {"flv_pull_url": {"ORIGIN": "file:///secret"}}}) == (
        None,
        "",
    )


def test_segment_command_preserves_input_and_flushes_inner_muxer():
    args = recording_command(
        "ffmpeg",
        "https://cdn/live.flv",
        headers={"User-Agent": "test"},
        interval=7,
        start=4,
        timeout=3,
    )
    # 相对名 + cwd：目录里的 % 不会被 segment 当成模板。
    assert args[-1] == "part-%06d.ts"
    assert args[args.index("-segment_list") + 1] == "segments.csv"
    assert "-http_proxy" not in args
    assert args.count("-i") == 1
    assert args[args.index("-c") + 1] == "copy"
    assert args[args.index("-segment_format") + 1] == "mpegts"
    assert args[args.index("-segment_format_options") + 1] == "flush_packets=1"
    assert args[args.index("-segment_start_number") + 1] == "4"
    assert args[args.index("-rw_timeout") + 1] == "3000000"
    with pytest.raises(ValueError):
        recording_command(
            "ffmpeg",
            "file:///secret",
            headers={},
            interval=7,
            start=0,
            timeout=3,
        )


async def test_journal_recovers_installed_but_unindexed_segment(tmp_path):
    journal = await RecordingJournal.create(
        tmp_path / "journals", directory=tmp_path / "output", metadata={"title": "live"}
    )
    source = tmp_path / "output" / "part-000001.ts"
    source.parent.mkdir(exist_ok=True)
    source.write_bytes(b"transport")
    await journal.discover(source)
    await journal.update(source.name, state="installed")
    loaded = await RecordingJournal.load(journal.path)
    assert loaded.pending()[0]["state"] == "installed"
    assert loaded.pending()[0]["segment_id"] != loaded.data["recording_id"]
    await loaded.update(source.name, state="indexed")
    assert not loaded.pending()


async def test_stop_has_hard_deadline_and_reaps_child():
    class Input:
        def write(self, value):
            assert value == b"q\n"

        async def drain(self):
            pass

    class Child:
        stdin = Input()
        returncode = None
        killed = threading.Event()

        async def wait(self):
            while not self.killed.is_set():
                await asyncio.sleep(0.001)
            self.returncode = -9
            return -9

        def kill(self):
            self.killed.set()

    child = Child()
    await stop_process(child, timeout=0.01)
    assert child.killed.is_set() and child.returncode == -9


@pytest.mark.parametrize(
    ("proxy", "expected"),
    [
        ("http://127.0.0.1:7890", ["-http_proxy", "http://127.0.0.1:7890"]),
        ("socks5://127.0.0.1:7891", []),
        ("", []),
    ],
)
def test_only_http_proxies_are_passed_to_ffmpeg(proxy, expected):
    args = recording_command(
        "ffmpeg", "https://cdn/live.flv", headers={}, interval=7, start=0, timeout=3, proxy=proxy
    )
    index = args.index("-i")
    assert args[index - len(expected) : index] == expected or not expected
    assert ("-http_proxy" in args) == bool(expected)
    assert "httpproxy" in args[args.index("-protocol_whitelist") + 1].split(",")


class HungChild:
    """不响应 q 的 FFmpeg：只有 kill 才会退出。"""

    def __init__(self):
        self.stdin = self
        self.returncode = None
        self.killed = asyncio.Event()

    def write(self, value):
        pass

    async def drain(self):
        pass

    async def wait(self):
        await self.killed.wait()
        self.returncode = -9
        return -9

    def kill(self):
        self.killed.set()


async def test_cancelled_stop_still_kills_and_reaps_child():
    child = HungChild()
    task = asyncio.create_task(stop_process(child, timeout=30))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert child.killed.is_set() and child.returncode == -9


async def test_remux_watchdog_kills_only_when_output_stops_growing(tmp_path, monkeypatch):
    monkeypatch.setattr(live_process, "REMUX_STALL_SECONDS", 0.3)
    monkeypatch.setattr(live_process, "REMUX_POLL_SECONDS", 0.05)
    target = tmp_path / "part-000001.mp4"
    pending = target.with_suffix(".pending.mp4")
    child = HungChild()

    async def fake_exec(*args, **kwargs):
        return child

    monkeypatch.setattr(live_process.asyncio, "create_subprocess_exec", fake_exec)

    async def grow():
        for _ in range(10):
            with pending.open("ab") as handle:
                handle.write(b"x")
            await asyncio.sleep(0.05)

    writer = asyncio.create_task(grow())
    started = asyncio.get_running_loop().time()
    assert await live_process.remux("ffmpeg", tmp_path / "part-000001.ts", target) is False
    await writer
    # 持续增长的 0.5 秒内不判卡死；停止增长后约 0.3 秒才强杀。
    assert asyncio.get_running_loop().time() - started >= 0.7
    assert child.killed.is_set()
