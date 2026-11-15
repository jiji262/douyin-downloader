"""一个手动录制会话：采集、后台封装、状态进度与断流恢复。"""

import asyncio
import contextlib
import csv
import os
import re
import shutil
import time
from pathlib import Path

import aiofiles

from core.ffmpeg import resolve_ffmpeg_path
from core.live_finalize import finalize_segment
from core.live_process import NO_WINDOW, SEGMENT_LIST, recording_command, stop_process
from core.live_streams import select_stream
from storage.live_recordings import run_io

POLL_SECONDS = 0.25
EMIT_SECONDS = 1.0
RECONNECT_DELAYS = (2, 5, 10, 30)
MIN_FREE_BYTES = 1024 * 1024 * 1024
RECONNECT_GRACE_SECONDS = 180
ROOM_LOOKUP_TIMEOUT = 10
DEFAULT_SEGMENT_SECONDS = 3600
INPUT_READ_TIMEOUT = 5
ROOM_ENDED_STATUS = "4"
SEGMENT_NAME = re.compile(r"part-(\d+)\.(ts|mp4)")


def recording_bytes(directory):
    sizes = {}
    for path in directory.glob("part-*.*"):
        if not SEGMENT_NAME.fullmatch(path.name):
            continue
        try:
            sizes[path.stem] = max(sizes.get(path.stem, 0), path.stat().st_size)
        except FileNotFoundError:
            continue
    return sum(sizes.values())


def room_identity(room):
    return str(room.get("id_str") or room.get("id") or "")


def _has_data(path):
    try:
        return path.stat().st_size > 0
    except FileNotFoundError:
        return False


def _next_number(directory, known):
    names = set(known)
    names.update(p.name for p in directory.glob("part-*.*") if SEGMENT_NAME.fullmatch(p.name))
    numbers = (int(match.group(1)) for match in map(SEGMENT_NAME.fullmatch, names) if match)
    return max(numbers, default=-1) + 1


def _clean_directory(directory):
    """会话结束：分段列表只服务于采集；没录到东西就连目录一起去掉。"""
    with contextlib.suppress(OSError):
        (directory / SEGMENT_LIST).unlink()
    for path in directory.glob("part-*.ts"):
        if not _has_data(path):
            with contextlib.suppress(OSError):
                path.unlink()
    with contextlib.suppress(OSError):
        directory.rmdir()


class LiveSession:
    def __init__(self, journal, *, config, database, reporter, stop_event, headers, proxy=""):
        self.journal, self.config, self.database = journal, config, database
        self.reporter, self.stop_event, self.headers = reporter, stop_event, headers
        self.proxy = proxy
        self.directory = Path(journal.data["directory"])
        self.queued = set()
        # 只放文件名，不设上限：停止前必须把所有完成段交给封装，不能因队列满漏掉。
        self.queue = asyncio.Queue()
        self.started = time.monotonic()
        self.seconds, self.bytes, self.gaps, self.attempts = 0.0, 0, 0, 0
        self.quality = self.room_id = self.phase = ""
        self.reason, self.ended, self.emitted_at = "ended", False, 0.0

    def stopped(self):
        limit = float(self.config.get("max_duration_seconds") or 0)
        return self.stop_event.is_set() or (limit > 0 and time.monotonic() - self.started >= limit)

    async def emit(self, phase, *, force=False):
        now = time.monotonic()
        if not force and phase == self.phase and now - self.emitted_at < EMIT_SECONDS:
            return
        self.phase, self.emitted_at = phase, now
        callback = getattr(self.reporter, "on_live_progress", None)
        if not callable(callback):
            return
        # 封装后 TS 换成体积略小的 MP4，显示值只增不减。
        self.bytes = max(self.bytes, await run_io(recording_bytes, self.directory))
        entries = self.journal.data["segments"].values()
        callback(
            recording_id=self.journal.data["recording_id"],
            phase=phase,
            recorded_seconds=round(self.seconds, 2),
            bytes=self.bytes,
            bitrate=round(self.bytes * 8 / max(self.seconds, 1)),
            quality=self.quality,
            segments=sum(e["state"] == "indexed" for e in entries),
            reconnects=self.gaps,
        )

    async def listed_segments(self):
        path = self.directory / SEGMENT_LIST
        if not path.exists():
            return []
        async with aiofiles.open(path) as handle:
            rows = csv.reader((await handle.read()).splitlines())
            return [row[0] for row in rows if row]

    async def harvest(self, *, tail=False):
        names = await self.listed_segments()
        if tail:
            paths = await run_io(lambda: sorted(self.directory.glob("part-*.ts")))
            names.extend(p.name for p in paths)
        for name in names:
            source = self.directory / Path(name).name
            if source.name in self.queued or not await run_io(_has_data, source):
                continue
            await self.journal.discover(source)
            self.queued.add(source.name)
            self.queue.put_nowait(source.name)

    async def finalize_worker(self):
        while True:
            name = await self.queue.get()
            try:
                if name is None:
                    return
                await finalize_segment(self.journal, name, database=self.database)
            finally:
                self.queue.task_done()

    async def read_progress(self, stream):
        offset = self.seconds
        while True:
            line = await stream.readline()
            if not line:
                return
            if line.startswith(b"out_time_us="):
                with contextlib.suppress(ValueError):
                    self.seconds = max(self.seconds, offset + int(line.split(b"=", 1)[1]) / 1000000)

    async def spawn(self, url):
        start = await run_io(_next_number, self.directory, list(self.journal.data["segments"]))
        # 进程以录制目录为 cwd 启动：相对路径的 FFmpeg（相对 PATH 项、IMAGEIO_FFMPEG_EXE）先固定成绝对路径。
        args = recording_command(
            os.path.abspath(resolve_ffmpeg_path()),
            url,
            headers=self.headers,
            interval=self.config.get("segment_seconds", DEFAULT_SEGMENT_SECONDS),
            start=start,
            timeout=min(float(self.config.get("idle_timeout_seconds") or 30), INPUT_READ_TIMEOUT),
            proxy=self.proxy,
        )
        return await asyncio.create_subprocess_exec(
            *args,
            cwd=str(self.directory),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=NO_WINDOW,
        )

    async def watch(self, process):
        while process.returncode is None and not self.stopped():
            free = await run_io(shutil.disk_usage, self.directory)
            if free.free < MIN_FREE_BYTES:
                raise OSError("磁盘剩余空间不足 1 GB，已停止录制")
            await self.harvest()
            await self.emit("recording")
            await asyncio.sleep(POLL_SECONDS)

    async def capture(self, url):
        process = await self.spawn(url)
        reader = asyncio.create_task(self.read_progress(process.stdout))
        try:
            await self.watch(process)
        finally:
            try:
                await stop_process(process)
            finally:
                await asyncio.gather(reader, return_exceptions=True)
            await self.harvest(tail=True)

    async def resolve_until_stopped(self, resolve):
        task = asyncio.create_task(resolve())
        deadline = time.monotonic() + ROOM_LOOKUP_TIMEOUT
        try:
            while not task.done() and not self.stopped() and time.monotonic() < deadline:
                await asyncio.wait({task}, timeout=POLL_SECONDS)
            return task.result() if task.done() else None
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def pause(self, seconds):
        for _ in range(int(seconds / POLL_SECONDS)):
            if self.stopped():
                return False
            await asyncio.sleep(POLL_SECONDS)
        return not self.stopped()

    async def lookup(self, resolve):
        try:
            info = await self.resolve_until_stopped(resolve)
        except Exception:
            info = None
        room = (info or {}).get("room") or {}
        identity = room_identity(room)
        if not identity:
            return None
        # 同一直播号重新开播会换内部房间：旧会话到此结束，避免和新房间的录制重复。
        if identity != self.room_id or str(room.get("status")) == ROOM_ENDED_STATUS:
            self.ended, self.reason = True, "ended"
            return None
        url, quality = select_stream(room)
        if url:
            self.quality = quality
            self.gaps += 1
        return url

    async def reconnect(self, resolve):
        deadline = time.monotonic() + RECONNECT_GRACE_SECONDS
        while time.monotonic() < deadline:
            delay = RECONNECT_DELAYS[min(self.attempts, len(RECONNECT_DELAYS) - 1)]
            self.attempts += 1
            if not await self.pause(delay):
                break
            await self.emit("reconnecting")
            url = await self.lookup(resolve)
            if url or self.ended:
                return url
        self.reason = "stopped" if self.stopped() else "interrupted"
        return None

    async def record(self, url, resolve):
        no_data_since = time.monotonic()
        while url and not self.stopped():
            before = self.seconds
            await self.capture(url)
            if self.stopped():
                break
            if self.seconds > before:
                # 只有真正收到新数据才清零退避，连上即断的流不会被 2 秒一次地反复重连。
                no_data_since, self.attempts = time.monotonic(), 0
            elif time.monotonic() - no_data_since > RECONNECT_GRACE_SECONDS:
                self.reason = "interrupted"
                return
            url = await self.reconnect(resolve)
        if self.stopped():
            self.reason = "stopped"

    async def finish(self, worker):
        try:
            await self.emit("finalizing", force=True)
            if worker is not None and not worker.done():
                self.queue.put_nowait(None)
                await worker
        finally:
            if worker is not None and not worker.done():
                # 收尾被取消：不留继续封装的孤儿任务，没封装完的段交给下次启动恢复。
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
            await self.journal.close(reason=self.reason)
            await run_io(_clean_directory, self.directory)

    async def run(self, info, resolve):
        worker = None
        try:
            if not resolve_ffmpeg_path():
                raise RuntimeError("找不到 FFmpeg，无法录制直播")
            await run_io(self.directory.mkdir, parents=True, exist_ok=True)
            self.room_id = room_identity(info["room"])
            url, self.quality = select_stream(info["room"])
            worker = asyncio.create_task(self.finalize_worker())
            await self.record(url, resolve)
        except BaseException as exc:
            self.reason = "interrupted"
            if isinstance(exc, asyncio.CancelledError) and worker is not None:
                # 被取消不再等封装排空：没封装完的段交给下次启动恢复。
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
            raise
        finally:
            await self.finish(worker)
        await self.emit(self.reason, force=True)
        return [s for s in self.journal.data["segments"].values() if s["state"] == "indexed"]
