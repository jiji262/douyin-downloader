import asyncio
import threading

from core import live_session
from core.live_session import LiveSession
from storage.live_recordings import RecordingJournal


async def make_session(tmp_path, reporter=None):
    journal = await RecordingJournal.create(
        tmp_path / "journals", directory=tmp_path, metadata={"base_path": str(tmp_path)}
    )
    return LiveSession(
        journal,
        config={},
        database=None,
        reporter=reporter,
        stop_event=threading.Event(),
        headers={},
    )


def room(identity="room-1", status=2):
    return {
        "room": {
            "id_str": identity,
            "status": status,
            "stream_url": {"flv_pull_url": {"HD1": "https://cdn.invalid/%s.flv" % identity}},
        }
    }


async def test_every_closed_segment_is_finalized_before_worker_exits(tmp_path, monkeypatch):
    finalized = []

    async def fake_finalize(journal, name, *, database):
        await asyncio.sleep(0)
        finalized.append(name)
        await journal.update(name, state="indexed")
        return True

    monkeypatch.setattr(live_session, "finalize_segment", fake_finalize)
    session = await make_session(tmp_path)
    for number in range(40):
        (tmp_path / ("part-%06d.ts" % number)).write_bytes(b"media")
    worker = asyncio.create_task(session.finalize_worker())
    await session.harvest(tail=True)
    await session.finish(worker)
    assert len(finalized) == 40
    assert not session.journal.pending()


async def test_stopping_cancels_and_reaps_pending_room_lookup(tmp_path):
    session = await make_session(tmp_path)
    started, reaped = asyncio.Event(), asyncio.Event()

    async def resolve():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            reaped.set()

    task = asyncio.create_task(session.resolve_until_stopped(resolve))
    await started.wait()
    session.stop_event.set()
    assert await asyncio.wait_for(task, 1) is None
    assert reaped.is_set()


async def test_backoff_survives_reconnects_until_new_data(tmp_path, monkeypatch):
    session = await make_session(tmp_path)
    session.room_id = "room-1"
    waits = []

    async def pause(seconds):
        waits.append(seconds)
        return True

    async def resolve():
        return room()

    monkeypatch.setattr(session, "pause", pause)
    for _ in range(3):
        assert await session.reconnect(resolve)
    assert waits == [2, 5, 10]


async def test_reconnect_ends_session_when_room_identity_changes(tmp_path, monkeypatch):
    session = await make_session(tmp_path)
    session.room_id = "room-1"

    async def pause(seconds):
        return True

    async def resolve():
        return room("room-2")

    monkeypatch.setattr(session, "pause", pause)
    assert await session.reconnect(resolve) is None
    assert session.reason == "ended" and session.gaps == 0


async def test_reconnect_treats_missing_identity_as_failed_lookup(tmp_path, monkeypatch):
    session = await make_session(tmp_path)
    session.room_id = "room-1"
    answers = [{"room": {"status": 2}}, room()]

    async def pause(seconds):
        return True

    async def resolve():
        return answers.pop(0)

    monkeypatch.setattr(session, "pause", pause)
    assert await session.reconnect(resolve) == "https://cdn.invalid/room-1.flv"
    assert session.gaps == 1


async def test_progress_is_throttled_but_phase_changes_are_immediate(tmp_path):
    events = []

    class Reporter:
        def on_live_progress(self, **data):
            events.append(data["phase"])

    session = await make_session(tmp_path, Reporter())
    for _ in range(5):
        await session.emit("recording")
    await session.emit("reconnecting")
    await session.emit("reconnecting", force=True)
    assert events == ["recording", "reconnecting", "reconnecting"]


async def test_stop_before_any_data_removes_empty_journal_and_directory(tmp_path):
    output = tmp_path / "output"
    journal = await RecordingJournal.create(
        tmp_path / "journals", directory=output, metadata={"base_path": str(tmp_path)}
    )
    session = LiveSession(
        journal,
        config={},
        database=None,
        reporter=None,
        stop_event=threading.Event(),
        headers={},
    )
    session.stop_event.set()
    assert await session.run(room(), lambda: None) == []
    assert session.reason == "stopped"
    assert not journal.path.exists()
    assert not output.exists()


class _StuckOutput:
    def __init__(self, done):
        self.done = done

    async def readline(self):
        await self.done.wait()
        return b""


class _StuckFFmpeg:
    """流停发时的 FFmpeg：不响应 q，只有 kill 才退出。"""

    def __init__(self):
        self.killed = asyncio.Event()
        self.returncode = None
        self.stdin = self
        self.stdout = _StuckOutput(self.killed)

    def write(self, data):
        pass

    async def drain(self):
        pass

    async def wait(self):
        await self.killed.wait()
        self.returncode = -9
        return -9

    def kill(self):
        self.killed.set()


async def test_cancel_while_stopping_keeps_unlisted_tail_segment_recoverable(tmp_path, monkeypatch):
    import json

    import pytest

    output = tmp_path / "out"
    journal = await RecordingJournal.create(
        tmp_path / "journals", directory=output, metadata={"base_path": str(tmp_path)}
    )
    session = LiveSession(
        journal,
        config={"segment_seconds": 3600},
        database=None,
        reporter=None,
        stop_event=threading.Event(),
        headers={},
    )
    child, spawned = _StuckFFmpeg(), asyncio.Event()

    async def spawn(url):
        (output / "part-000000.ts").write_bytes(b"media")
        spawned.set()
        return child

    monkeypatch.setattr(live_session, "resolve_ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(session, "spawn", spawn)
    task = asyncio.create_task(session.run(room(), lambda: None))
    await spawned.wait()
    session.stop_event.set()
    await asyncio.sleep(0.6)
    # 停止收尾还在等 FFmpeg 退出时退出应用：首段还没写进分段列表。
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert child.killed.is_set()
    saved = json.loads(journal.path.read_text())
    assert saved["segments"]["part-000000.ts"]["state"] == "pending_finalize"
    assert (output / "part-000000.ts").exists()


async def test_cancel_during_finalizing_report_still_closes_worker_and_journal(tmp_path, monkeypatch):
    import pytest

    session = await make_session(tmp_path)
    worker = asyncio.create_task(session.finalize_worker())
    entered = asyncio.Event()

    async def slow_emit(phase, *, force=False):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(session, "emit", slow_emit)
    task = asyncio.create_task(session.finish(worker))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert worker.done()
    assert session.journal.data["active"] is False


async def test_stop_after_long_silence_is_recorded_as_stopped(tmp_path, monkeypatch):
    session = await make_session(tmp_path)
    monkeypatch.setattr(live_session, "RECONNECT_GRACE_SECONDS", 0)

    async def silent_capture(url):
        # 断流很久后用户点停止：这一轮没有新数据。
        session.stop_event.set()

    monkeypatch.setattr(session, "capture", silent_capture)
    await session.record("https://cdn.invalid/live.flv", lambda: None)
    assert session.reason == "stopped"


async def test_relative_ffmpeg_path_is_pinned_before_changing_directory(tmp_path, monkeypatch):
    import os

    session = await make_session(tmp_path)
    seen = {}

    async def fake_exec(*args, **kwargs):
        seen["binary"], seen["cwd"] = args[0], kwargs["cwd"]
        return object()

    monkeypatch.setattr(live_session, "resolve_ffmpeg_path", lambda: "bin/ffmpeg")
    monkeypatch.setattr(live_session.asyncio, "create_subprocess_exec", fake_exec)
    await session.spawn("https://cdn.invalid/live.flv")
    assert seen["binary"] == os.path.abspath("bin/ffmpeg")
    assert seen["cwd"] == str(tmp_path)
