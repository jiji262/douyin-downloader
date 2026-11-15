"""分段封装与幂等登记；失败保留 TS，供重启恢复。"""

import asyncio
import errno
import json
import os
import time
import weakref
from pathlib import Path

from core.ffmpeg import resolve_ffmpeg_path
from core.live_process import remux
from storage.live_recordings import RecordingJournal, run_io, sync_directory
from utils.logger import setup_logger

logger = setup_logger("LiveFinalize")
# 恢复只碰静默超过该时长的 TS：残留的 FFmpeg 可能还在写最后一段。
RECOVERY_QUIET_SECONDS = 30.0
_LINK_UNSUPPORTED_ERRNOS = frozenset(
    code
    for code in (
        errno.EXDEV,
        errno.EPERM,
        errno.EACCES,
        errno.ENOTSUP,
        errno.ENOSYS,
        getattr(errno, "EOPNOTSUPP", None),
    )
    if code is not None
)
# Windows 上 FAT32 / exFAT 等卷不支持硬链接：ERROR_INVALID_FUNCTION / ERROR_NOT_SUPPORTED。
_WINDOWS_LINK_UNSUPPORTED = (1, 50)
_MANIFEST_LOCKS = weakref.WeakKeyDictionary()


def _link_unsupported(exc):
    winerror = getattr(exc, "winerror", None)
    return exc.errno in _LINK_UNSUPPORTED_ERRNOS or winerror in _WINDOWS_LINK_UNSUPPORTED


def _install(temporary, target):
    with temporary.open("r+b") as handle:
        os.fsync(handle.fileno())
    # 成品只安装一次，不覆盖已存在的文件。
    try:
        os.link(str(temporary), str(target))
        temporary.unlink()
    except OSError as exc:
        if not _link_unsupported(exc):
            raise
        # 不支持硬链接时先独占占名，再原子替换自己的空占位。
        descriptor = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        os.replace(str(temporary), str(target))
    sync_directory(target.parent)


def _manifest_lock():
    # 锁按事件循环分开：模块级 Lock 在 3.9 会绑定导入时的循环，CLI 的 asyncio.run 里争用就会报错。
    loop = asyncio.get_running_loop()
    lock = _MANIFEST_LOCKS.get(loop)
    if lock is None:
        lock = _MANIFEST_LOCKS[loop] = asyncio.Lock()
    return lock


def _scan_manifest(path, aweme_id):
    """返回 (已登记, 末尾是不完整半行)。坏行跳过。"""
    if not path.exists():
        return False, False
    data = path.read_bytes()
    for line in data.splitlines():
        try:
            if json.loads(line).get("aweme_id") == aweme_id:
                return True, False
        except (ValueError, AttributeError):
            continue
    return False, bool(data) and not data.endswith(b"\n")


def _append_record(path, record):
    found, partial_tail = _scan_manifest(path, record["aweme_id"])
    if found:
        return
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        # 上次崩溃留下的半行先断开，否则新记录会和残片拼成一行坏 JSON。
        handle.write(("\n" if partial_tail else "") + line)
        handle.flush()
        os.fsync(handle.fileno())
    sync_directory(path.parent)


async def _append_once(base, record):
    async with _manifest_lock():
        await run_io(_append_record, Path(base) / "download_manifest.jsonl", record)


async def _index(journal, entry, database):
    meta = journal.data["metadata"]
    target = Path(entry["target"])
    record = {
        "aweme_id": entry["segment_id"],
        "aweme_type": "live",
        "media_type": "live",
        "title": meta.get("title", "直播"),
        "desc": meta.get("title", "直播"),
        "author_name": meta.get("author_name", "unknown"),
        "author_sec_uid": meta.get("author_sec_uid"),
        "room_id": meta.get("room_id"),
        "create_time": meta.get("create_time"),
        "job_id": meta.get("job_id", ""),
        "file_path": str(target),
        "file_paths": [str(target)],
        "file_names": [target.name],
        "mode": "live",
        "recording_id": journal.data["recording_id"],
    }
    if database:
        await database.add_aweme({**record, "metadata": json.dumps(record, ensure_ascii=False)})
    await _append_once(meta["base_path"], record)


async def _ensure_installed(journal, name, source, target):
    entry = journal.data["segments"][name]
    if entry["state"] == "installing" and target.exists() and target.stat().st_size == 0:
        await run_io(target.unlink)
    if entry["state"] == "installed" and target.exists():
        return True
    if not target.exists():
        binary = resolve_ffmpeg_path()
        if not binary or not await remux(binary, source, target):
            return False
        await journal.update(name, state="installing")
        await run_io(_install, target.with_suffix(".pending.mp4"), target)
    await journal.update(name, state="installed")
    # 硬链接后、删临时名前崩溃会留下同内容的 .pending.mp4。
    await run_io(target.with_suffix(".pending.mp4").unlink, missing_ok=True)
    return True


async def finalize_segment(journal, name, *, database):
    entry = journal.data["segments"][name]
    if entry["state"] == "indexed":
        return True
    source, target = Path(entry["source"]), Path(entry["target"])
    try:
        if not await _ensure_installed(journal, name, source, target):
            return False
        if not target.is_file() or target.stat().st_size == 0:
            return False
        await _index(journal, entry, database)
        await journal.update(name, state="indexed")
        await run_io(source.unlink, missing_ok=True)
        return True
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("直播分段收尾失败，保留产物供恢复")
        return False


def _settled(source):
    try:
        return time.time() - source.stat().st_mtime >= RECOVERY_QUIET_SECONDS
    except FileNotFoundError:
        # 源 TS 已删：要么已安装待登记，要么确实丢了，交给 finalize 判定。
        return True


def _scan_sources(directory):
    """返回 (待恢复的 TS, 目录是否已静默)。封装中的 .pending.mp4 也算正在写。"""
    sources = sorted(directory.glob("part-*.ts"))
    busy = list(directory.glob("part-*.pending.mp4"))
    return sources, all(_settled(path) for path in sources + busy)


async def _recover_journal(path, database):
    """恢复一个会话；返回 True 表示目录里还有刚写过的文件，整份暂缓。"""
    journal = await RecordingJournal.load(path)
    sources, settled = await run_io(_scan_sources, Path(journal.data["directory"]))
    if not settled:
        # 可能还有进程在录或在封装：整份日志不碰，免得抢着封装同一段、互相覆盖日志。
        return True
    for source in sources:
        await journal.discover(source)
    for name, entry in list(journal.data["segments"].items()):
        if entry["state"] == "indexed":
            # 登记后、删 TS 前崩溃留下的中间文件。
            await run_io(Path(entry["source"]).unlink, missing_ok=True)
        else:
            await finalize_segment(journal, name, database=database)
    if journal.data.get("active"):
        await journal.update_meta(active=False)
    return False


async def _recover_all(paths, database):
    deferred = []
    for path in paths:
        try:
            if await _recover_journal(path, database):
                deferred.append(path)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("直播日志恢复失败，保留日志")
    return deferred


async def recover_recordings(root, *, database, paths=None, retry_deferred=True):
    """恢复崩溃 / 中断留下的分段。retry_deferred=False 时刚写过的段留到下次启动（CLI 不为此多等）。"""
    if paths is None:
        paths = await run_io(lambda: sorted(Path(root).glob("*.json")))
    deferred = await _recover_all(paths, database)
    if deferred and retry_deferred:
        await asyncio.sleep(RECOVERY_QUIET_SECONDS)
        await _recover_all(deferred, database)
