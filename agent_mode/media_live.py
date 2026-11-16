"""`live` 的实现：经 DownloaderFactory 走现有的直播录制路径，录到 `--max-duration` 为止。

除 `--out` 目录和状态目录（history.db、.live-recordings/）外不写任何文件。
输出里只有本地文件路径和投影后的房间信息，没有任何流地址。
"""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Callable, Dict, List

from agent_mode import state
from agent_mode.capabilities import UNSUPPORTED, url_type_level
from agent_mode.envelope import (
    NAME_MAX_CHARS,
    AgentError,
    add_warning,
    clean_text,
    install_action,
    retry_action,
)
from agent_mode.events import make_reporter
from agent_mode.media_live_recovery import (
    RecordingLock,
    finalize_after_interrupt,
    journal_path,
    read_journal,
    recorded_files,
    recover_interrupted,
)

LIVE_URL_TYPE = "live"
RETRY_AFTER_S = 60
DURATION_PRECISION = 1
FFMPEG_PACKAGE = "imageio-ffmpeg==0.6.0"
STOPPED_MAX_DURATION = "max_duration"
STOPPED_ENDED = "ended"
STOPPED_STREAM_LOST = "stream_lost"
STOPPED_INTERRUPTED = "interrupted"
# core.live_session 的结束原因 -> 对外的 stopped_reason。agent 模式从不置 stop_event，
# 所以 "stopped" 只可能是到了 --max-duration。
_SESSION_REASONS = {
    "stopped": STOPPED_MAX_DURATION,
    "ended": STOPPED_ENDED,
    "interrupted": STOPPED_STREAM_LOST,
}


class LiveCollector:
    """交给下载器的 reporter：记下录制目录、进度和失败原因，再转发给事件输出。"""

    def __init__(self, events: Any, *, on_recording: Callable[[str], None]):
        self.events = events
        # 第一次得知录制 id 时回调一次，用来给录制日志加 flock 锁
        self.on_recording = on_recording
        self.recording_id = ""
        self.phase = ""
        self.recorded_s = 0.0
        self.item_reason = ""

    def update_step(self, step: str, detail: str = "") -> None:
        self.events.update_step(step, detail)

    def set_item_total(self, total: int, detail: str = "") -> None:
        self.events.set_item_total(total, detail)

    def advance_item(self, status: str, detail: str = "", reason: str = "") -> None:
        if reason:
            self.item_reason = reason
        self.events.advance_item(status, detail, reason=reason)

    def on_live_progress(self, **fields: Any) -> None:
        recording_id = str(fields.get("recording_id") or "")
        if recording_id and recording_id != self.recording_id:
            self.recording_id = recording_id
            self.on_recording(recording_id)
        self.phase = str(fields.get("phase") or self.phase)
        self.recorded_s = max(self.recorded_s, float(fields.get("recorded_seconds") or 0))
        self.events.on_live_progress(**fields)


def _require_ffmpeg() -> None:
    from core.ffmpeg import resolve_ffmpeg_path

    if not resolve_ffmpeg_path():
        raise AgentError(
            "DEPENDENCY_MISSING",
            "FFmpeg was not found; `live` needs it to record.",
            action=install_action(FFMPEG_PACKAGE),
        )


async def _parse_live_url(raw: str) -> Dict[str, Any]:
    from agent_mode.inspect_cmd import resolve_input_url
    from core.url_parser import URLParser

    # 短链不交给带登录 cookie 的录制 client 去解析
    url = await resolve_input_url(raw)
    if url is None:
        raise AgentError(
            "UPSTREAM_EMPTY",
            "Could not resolve the short link (network error, expired link, or rate limit).",
            action=retry_action(RETRY_AFTER_S),
        )
    parsed = URLParser.parse(url) or {}
    url_type = parsed.get("type")
    if url_type == LIVE_URL_TYPE and parsed.get("room_id"):
        return parsed
    code = "UNSUPPORTED" if url_type_level(url_type) == UNSUPPORTED else "USAGE"
    raise AgentError(
        code,
        "`live` only records live-room URLs (https://live.douyin.com/<room>); this one is "
        "'%s'. Run `douyin-dl agent inspect <url>` to see what it is." % (url_type or "unknown"),
    )


def _watch_room(client: Any, room: Dict[str, str]) -> None:
    """包一层房间信息查询，顺手记下标题和主播昵称（只取这两个字段）。"""
    lookup = client.get_live_room_info

    async def remembering_lookup(*args: Any, **kwargs: Any) -> Any:
        info = await lookup(*args, **kwargs)
        details = (info or {}).get("room") or {}
        owner = (info or {}).get("user") or details.get("owner") or {}
        for key, value in (("title", details.get("title")), ("nickname", owner.get("nickname"))):
            if value:
                room[key] = clean_text(value, NAME_MAX_CHARS)
        return info

    client.get_live_room_info = remembering_lookup


def _live_data(journal: Dict[str, Any], collector: LiveCollector, room: Dict[str, str]) -> Dict:
    finished, pending = recorded_files(journal)
    if pending:
        add_warning(
            "%d segment(s) are still raw .ts files; the next `live` run converts them to .mp4"
            % len(pending)
        )
    # 没开始录（未开播、取不到房间）时没有结束原因，报 null
    reason = str(journal.get("reason") or collector.phase)
    files = finished + pending
    return {
        "room": dict(room),
        "files": files,
        "duration_s": round(collector.recorded_s, DURATION_PRECISION),
        "segments": len(files),
        "stopped_reason": _SESSION_REASONS.get(reason),
    }


def _empty_error(collector: LiveCollector, data: Dict[str, Any]) -> AgentError:
    from core import item_reasons

    if collector.item_reason == item_reasons.SKIP_LIVE_NOT_STREAMING:
        return AgentError(
            "UPSTREAM_EMPTY", "The room is not live right now.", retryable=False, data=data
        )
    if collector.item_reason == item_reasons.FAIL_LIVE_ROOM_INFO:
        message = "Could not fetch the live room (wrong URL, rate limit, or anti-bot)."
    else:
        message = "The live stream returned no data (it may have just ended)."
    return AgentError("UPSTREAM_EMPTY", message, action=retry_action(RETRY_AFTER_S), data=data)


def _outcome(result: Any, data: Dict[str, Any], collector: LiveCollector) -> Dict[str, Any]:
    if not data["files"]:
        raise _empty_error(collector, data)
    if getattr(result, "failed", 0):
        raise AgentError(
            "PARTIAL",
            "Recording stopped with %d segment(s) not finalized; `files` lists what was saved."
            % result.failed,
            data=data,
        )
    return data


async def _add_history(database: Any, config: Any, *, url: str, result: Any) -> None:
    """与人用 CLI 一致：每次运行在历史库里记一行。config 里本来就没有 cookie。"""
    await database.add_history(
        {
            "url": url,
            "url_type": LIVE_URL_TYPE,
            "total_count": result.total,
            "success_count": result.success,
            "config": json.dumps(
                {k: v for k, v in config.config.items() if k != "transcript"}, ensure_ascii=False
            ),
        }
    )


async def _download(args: argparse.Namespace, config: Any, database: Any) -> Dict[str, Any]:
    from auth import CookieManager
    from core.api_client import DouyinAPIClient, LoginRequiredError
    from core.downloader_factory import DownloaderFactory
    from storage import FileManager

    def journal(recording_id: str) -> Path:
        return journal_path(database, config, recording_id)

    lock = RecordingLock()
    collector = LiveCollector(
        make_reporter(args.events), on_recording=lambda rid: lock.claim(journal(rid))
    )
    room: Dict[str, str] = {}
    # 指定 cookie 文件：无参的 CookieManager() 会读写 cwd 下的 .cookies.json
    cookie_manager = CookieManager(cookie_file=str(state.cookies_path()))

    def snapshot() -> Dict[str, Any]:
        return _live_data(read_journal(journal(collector.recording_id)), collector, room)

    parsed = await _parse_live_url(args.url)
    async with DouyinAPIClient(cookie_manager.get_cookies(), proxy=config.get("proxy")) as client:
        _watch_room(client, room)
        downloader = DownloaderFactory.create(
            LIVE_URL_TYPE,
            config,
            client,
            file_manager=FileManager(config.get("path")),
            cookie_manager=cookie_manager,
            database=database,
            progress_reporter=collector,
        )
        try:
            result = await downloader.download(parsed)
        except asyncio.CancelledError:
            await finalize_after_interrupt(journal(collector.recording_id), database)
            data = dict(snapshot(), stopped_reason=STOPPED_INTERRUPTED)
            raise AgentError("INTERRUPTED", "Recording was interrupted.", data=data) from None
        except LoginRequiredError:
            raise AgentError("LOGIN_REQUIRED", "Douyin asked for a login.") from None
        finally:
            lock.release()
    await _add_history(database, config, url=parsed["original_url"], result=result)
    return _outcome(result, snapshot(), collector)


def _missing_dirs(path: Path) -> List[Path]:
    """运行前还不存在的输出目录及其上级，由深到浅。"""
    missing = []
    while not path.exists() and path != path.parent:
        missing.append(path)
        path = path.parent
    return missing


def _remove_if_empty(created: List[Path]) -> None:
    """FileManager 一构造就建目录；没录到文件时把本次新建的空目录收掉。"""
    for path in created:
        try:
            path.rmdir()
        except OSError:
            # 目录非空（录到了文件）或根本没建出来
            return


async def record_live(args: argparse.Namespace) -> Dict[str, Any]:
    from storage import Database

    _require_ffmpeg()
    out_dir = state.resolve_path(args.out) if args.out else state.default_output_dir()
    created_dirs = _missing_dirs(out_dir)
    config = state.build_config(download_path=out_dir)
    # update 对 dict 值是合并，其余直播参数保持默认
    config.update(live={"max_duration_seconds": args.max_duration})
    state.ensure_state_dir()
    database = Database(db_path=str(state.history_db_path()))
    await database.initialize()
    try:
        await recover_interrupted(database, config)
        return await _download(args, config, database)
    finally:
        await database.close()
        _remove_if_empty(created_dirs)
