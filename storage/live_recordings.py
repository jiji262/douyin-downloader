"""按会话保存录制产物日志；状态更新原子落盘，恢复不依赖扩展名猜测。"""

import asyncio
import contextlib
import functools
import json
import os
import threading
import uuid
from pathlib import Path


async def run_io(function, *args, **kwargs):
    call = functools.partial(function, *args, **kwargs)
    return await asyncio.get_running_loop().run_in_executor(None, call)


def sync_directory(path):
    if os.name == "nt":
        return
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("%s.%s.writing" % (path.name, uuid.uuid4().hex))
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
    except BaseException:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise
    sync_directory(path.parent)


def _recordable_sources(directory):
    sources = []
    for path in sorted(directory.glob("part-*.ts")):
        try:
            if path.stat().st_size > 0:
                sources.append(path)
        except OSError:
            continue
    return sources


class RecordingJournal:
    def __init__(self, path, data):
        self.path = Path(path)
        self.data = data
        self.lock = asyncio.Lock()
        # 保存在线程里执行，协程被取消后线程仍会写完：按版本串行落盘，晚到的旧快照直接丢弃。
        self._write_lock = threading.Lock()
        self._version = 0
        self._written = 0
        self._discarded = False

    @classmethod
    async def create(cls, root, *, directory, metadata):
        recording_id = uuid.uuid4().hex
        data = {
            "recording_id": recording_id,
            "directory": str(directory),
            "metadata": metadata,
            "segments": {},
            "active": True,
        }
        journal = cls(Path(root) / (recording_id + ".json"), data)
        await journal.save()
        return journal

    @classmethod
    async def load(cls, path):
        data = await run_io(lambda: json.loads(Path(path).read_text(encoding="utf-8")))
        return cls(path, data)

    def _write_snapshot(self, version, payload):
        with self._write_lock:
            if self._discarded or version <= self._written:
                return
            _write(self.path, payload)
            self._written = version

    def _discard_file(self):
        with self._write_lock:
            self._discarded = True
            with contextlib.suppress(FileNotFoundError):
                self.path.unlink()

    async def save(self):
        self._version += 1
        payload = json.dumps(self.data, ensure_ascii=False)
        await run_io(self._write_snapshot, self._version, payload)

    def _add(self, source):
        if source.name in self.data["segments"]:
            return False
        self.data["segments"][source.name] = {
            "segment_id": "live_" + self.data["recording_id"] + "_" + source.stem,
            "source": str(source),
            "target": str(source.with_suffix(".mp4")),
            "state": "pending_finalize",
        }
        return True

    async def discover(self, source):
        async with self.lock:
            if self._add(source):
                await self.save()

    async def update(self, name, **values):
        async with self.lock:
            self.data["segments"][name].update(values)
            await self.save()

    async def update_meta(self, **values):
        async with self.lock:
            self.data.update(values)
            await self.save()

    async def close(self, *, reason):
        """会话结束：先把目录里还没登记的非空 TS 补进日志（收尾被取消时尾段来不及登记），
        再落盘最终状态；一个可恢复的分段都没有，日志才删除。"""
        sources = await run_io(_recordable_sources, Path(self.data["directory"]))
        async with self.lock:
            for source in sources:
                self._add(source)
            self.data.update(active=False, reason=reason)
            if self.data["segments"]:
                await self.save()
            else:
                await run_io(self._discard_file)

    def pending(self):
        return [dict(s) for s in self.data["segments"].values() if s["state"] != "indexed"]


def journal_root(database, base_path):
    parent = Path(database.db_path).resolve().parent if database else Path(base_path)
    return parent / ".live-recordings"
