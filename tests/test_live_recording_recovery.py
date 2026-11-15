"""录制日志必须覆盖安装与登记之间的中断。"""

from unittest.mock import AsyncMock

import pytest

from core.live_finalize import finalize_segment
from storage.live_recordings import RecordingJournal


async def test_installed_segment_indexes_once_without_remux(tmp_path, monkeypatch):
    output = tmp_path / "output"
    output.mkdir()
    journal = await RecordingJournal.create(
        tmp_path / "journals",
        directory=output,
        metadata={"base_path": str(tmp_path), "title": "live"},
    )
    source = output / "part-000001.ts"
    source.write_bytes(b"source")
    source.with_suffix(".mp4").write_bytes(b"installed")
    await journal.discover(source)
    await journal.update(source.name, state="installed")
    db = AsyncMock()
    first = await finalize_segment(journal, source.name, database=db)
    second = await finalize_segment(journal, source.name, database=db)
    assert first and second
    assert db.add_aweme.await_count == 1
    lines = (tmp_path / "download_manifest.jsonl").read_text().splitlines()
    assert len(lines) == 1
    assert journal.data["segments"][source.name]["state"] == "indexed"


async def test_failed_remux_preserves_recoverable_source(tmp_path, monkeypatch):
    journal = await RecordingJournal.create(
        tmp_path / "journals", directory=tmp_path, metadata={"base_path": str(tmp_path)}
    )
    source = tmp_path / "part-000001.ts"
    source.write_bytes(b"source")
    await journal.discover(source)
    monkeypatch.setattr("core.live_finalize.remux", AsyncMock(return_value=False))
    assert not await finalize_segment(journal, source.name, database=None)
    assert source.read_bytes() == b"source"
    assert journal.pending()[0]["state"] == "pending_finalize"


async def _crashed_session(tmp_path, *, age):
    import os
    import time

    output = tmp_path / "output"
    output.mkdir()
    journal = await RecordingJournal.create(
        tmp_path / "journals", directory=output, metadata={"base_path": str(tmp_path)}
    )
    source = output / "part-000000.ts"
    source.write_bytes(b"media")
    stamp = time.time() - age
    os.utime(source, (stamp, stamp))
    return journal, source


async def test_recovery_defers_recently_written_segment_then_retries(tmp_path, monkeypatch):
    from core import live_finalize

    journal, source = await _crashed_session(tmp_path, age=0)
    seen = []

    async def fake_finalize(loaded, name, *, database):
        seen.append(name)
        return True

    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        import os

        os.utime(source, (0, 0))

    monkeypatch.setattr(live_finalize, "finalize_segment", fake_finalize)
    monkeypatch.setattr(live_finalize.asyncio, "sleep", fake_sleep)
    await live_finalize.recover_recordings(journal.path.parent, database=None)
    # 第一遍跳过刚写过的段，静默期后第二遍才封装。
    assert sleeps == [live_finalize.RECOVERY_QUIET_SECONDS]
    assert seen == [source.name]


@pytest.mark.parametrize("fresh_name", ["part-000001.ts", "part-000000.pending.mp4"])
async def test_recovery_leaves_whole_journal_alone_while_directory_is_busy(
    tmp_path, monkeypatch, fresh_name
):
    from core import live_finalize

    journal, old = await _crashed_session(tmp_path, age=120)
    (old.parent / fresh_name).write_bytes(b"live")
    before = journal.path.read_bytes()
    finalized = []

    async def fake_finalize(loaded, name, *, database):
        finalized.append(name)
        return True

    monkeypatch.setattr(live_finalize, "finalize_segment", fake_finalize)
    await live_finalize.recover_recordings(journal.path.parent, database=None, retry_deferred=False)
    # 另一个进程可能还在录或在封装：已静默的旧段也不碰，日志一字不改。
    assert finalized == []
    assert journal.path.read_bytes() == before


async def test_recovery_removes_leftover_source_of_indexed_segment(tmp_path):
    from core.live_finalize import recover_recordings

    journal, source = await _crashed_session(tmp_path, age=120)
    await journal.discover(source)
    await journal.update(source.name, state="indexed")
    before = journal.path.stat().st_mtime_ns
    await recover_recordings(journal.path.parent, database=None)
    assert not source.exists()
    loaded = await RecordingJournal.load(journal.path)
    assert loaded.data["active"] is False
    # 已完成的日志再次恢复时不再重写。
    after = journal.path.stat().st_mtime_ns
    await recover_recordings(journal.path.parent, database=None)
    assert journal.path.stat().st_mtime_ns == after and after >= before


async def test_late_stale_journal_snapshot_cannot_overwrite_newer_state(tmp_path):
    import json

    journal = await RecordingJournal.create(
        tmp_path / "journals", directory=tmp_path, metadata={"base_path": str(tmp_path)}
    )
    journal._write_snapshot(10, json.dumps({"version": "new"}))
    journal._write_snapshot(9, json.dumps({"version": "stale"}))
    assert json.loads(journal.path.read_text())["version"] == "new"
    assert not list(journal.path.parent.glob("*.writing"))


async def test_manifest_append_repairs_partial_last_line(tmp_path):
    import json

    from core.live_finalize import _append_once

    manifest = tmp_path / "download_manifest.jsonl"
    manifest.write_text('{"aweme_id": "old"}\n{"aweme_id": "brok', encoding="utf-8")
    await _append_once(tmp_path, {"aweme_id": "live_1"})
    await _append_once(tmp_path, {"aweme_id": "live_1"})
    rows = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line)["aweme_id"])
        except ValueError:
            continue
    assert rows == ["old", "live_1"]
