"""手动直播录制：持续 TS 分段、后台封装和独立停止。"""

import asyncio
import threading
import time
import uuid
import weakref
from datetime import datetime
from pathlib import Path

from core import item_reasons
from core.downloader_base import BaseDownloader, DownloadResult
from core.live_session import LiveSession
from core.live_streams import select_stream
from core.output_naming import plan_output_paths
from storage.live_recordings import RecordingJournal, journal_root
from utils.naming import build_live_context

_ACTIVE_ROOMS = weakref.WeakKeyDictionary()
_SESSION_ID_LENGTH = 12
# 会话目录 = stem + "_" + 随机串；目录里最长的文件是封装中的 part-NNNNNN.pending.mp4。
_LONGEST_LEAF = "_" + "0" * _SESSION_ID_LENGTH + "/part-000000.pending.mp4"


class LiveDownloader(BaseDownloader):
    _select_best_stream_url = staticmethod(select_stream)

    async def download(self, parsed_url):
        result = DownloadResult()
        room_id = str(parsed_url.get("room_id") or "")
        if not room_id:
            return result
        result.total = 1
        self._progress_set_item_total(1, "直播录制")
        if self._stop_event().is_set():
            # 排队期间就点了停止：不再拉房间信息，也不建空的录制目录。
            result.skipped = 1
            self._progress_advance_item("skipped", room_id, item_reasons.SKIP_LIVE_STOPPED)
            return result
        info = await self._room_info(parsed_url)
        reason = self._unavailable_reason(info)
        if reason:
            status = "skipped" if reason == item_reasons.SKIP_LIVE_NOT_STREAMING else "failed"
            setattr(result, status, 1)
            self._progress_advance_item(status, room_id, reason)
            return result
        room = info["room"]
        identity = str(room.get("id_str") or room.get("id") or "")
        if not identity:
            raise RuntimeError("直播间缺少稳定身份，无法安全录制")
        active = _ACTIVE_ROOMS.setdefault(asyncio.get_running_loop(), set())
        if identity in active:
            raise RuntimeError("该直播间已在录制中")
        active.add(identity)
        try:
            return await self._record(parsed_url, info, identity)
        finally:
            active.discard(identity)

    async def _room_info(self, parsed):
        kwargs = {}
        if parsed.get("room_id_kind") == "room_id":
            kwargs = {
                "room_id_kind": "room_id",
                "sec_user_id": str(parsed.get("sec_user_id") or ""),
            }
        await self.rate_limiter.acquire()
        return await self.api_client.get_live_room_info(str(parsed["room_id"]), **kwargs)

    @staticmethod
    def _unavailable_reason(info):
        if not info:
            return item_reasons.FAIL_LIVE_ROOM_INFO
        room = info.get("room") or {}
        if str(room.get("status")) != "2":
            return item_reasons.SKIP_LIVE_NOT_STREAMING
        if not select_stream(room)[0]:
            return item_reasons.FAIL_LIVE_NO_STREAM
        return None

    async def _record(self, parsed, info, identity):
        owner = info.get("user") or info["room"].get("owner") or {}
        author = str(owner.get("nickname") or "unknown")
        title = str(info["room"].get("title") or "直播")
        directory, stem = self._plan_output_paths(author, title, identity)
        # 日志里存绝对路径：换个工作目录启动恢复时，分段和清单仍指向原来的位置。
        directory = (directory / (stem + "_" + uuid.uuid4().hex[:_SESSION_ID_LENGTH])).absolute()
        self._report_recording_dir(directory)
        metadata = {
            "base_path": str(Path(self.file_manager.base_path).absolute()),
            "title": title,
            "author_name": author,
            "author_sec_uid": owner.get("sec_uid"),
            "room_id": identity,
            "job_id": self.job_id or "",
            "create_time": int(time.time()),
        }
        journal = await RecordingJournal.create(
            journal_root(self.database, self.file_manager.base_path),
            directory=directory,
            metadata=metadata,
        )
        headers = {
            "User-Agent": self.api_client.headers.get("User-Agent", "Mozilla/5.0"),
            "Referer": "https://live.douyin.com/",
            "Origin": "https://live.douyin.com",
        }
        session = LiveSession(
            journal,
            config=self._live_config(),
            database=self.database,
            reporter=self.progress_reporter,
            stop_event=self._stop_event(),
            headers=headers,
            proxy=getattr(self.api_client, "proxy", ""),
        )
        await session.run(info, lambda: self._room_info(parsed))
        return self._summarize(session, list(journal.data["segments"].values()), identity)

    def _summarize(self, session, segments, identity):
        result = DownloadResult()
        result.total = 1
        if not segments:
            # 没录到内容时会话目录已被清掉，「打开输出文件夹」改落到上级目录。
            self._report_recording_dir(session.directory.parent)
            stopped = session.reason == "stopped"
            status = "skipped" if stopped else "failed"
            setattr(result, status, 1)
            reason = item_reasons.SKIP_LIVE_STOPPED if stopped else item_reasons.FAIL_LIVE_NO_DATA
            self._progress_advance_item(status, identity, reason)
            return result
        result.total = len(segments)
        self._progress_set_item_total(result.total, "直播录制")
        for entry in segments:
            if entry["state"] == "indexed":
                result.success += 1
                self._progress_advance_item("success", entry["segment_id"])
            else:
                result.failed += 1
                self._progress_advance_item(
                    "failed", entry["segment_id"], item_reasons.FAIL_LIVE_SAVE
                )
        if session.reason == "interrupted" or result.failed:
            result.incomplete_reason = "录制中断或部分分段待恢复，已保存的文件保留"
        return result

    def _stop_event(self):
        event = getattr(self, "stop_event", None)
        if event is None:
            event = self.stop_event = threading.Event()
        return event

    def _report_recording_dir(self, directory):
        # 任务卡片「打开输出文件夹」直接落到本次录制的分段目录。
        callback = getattr(self.progress_reporter, "on_output_dir", None)
        if callable(callback):
            callback(path=str(directory))

    def _live_config(self):
        value = self.config.get("live") or {}
        return value if isinstance(value, dict) else {}

    def _plan_output_paths(self, author_name, title, room_id):
        context = build_live_context(
            room_id=room_id, title=title, author_name=author_name, started_at=datetime.now()
        )
        return plan_output_paths(
            file_manager=self.file_manager,
            config=self.config,
            context=context,
            mode="live",
            suffixes=(_LONGEST_LEAF,),
        )
