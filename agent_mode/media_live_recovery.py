"""`live` 的录制日志读取与分段恢复：启动时恢复上次中断的分段、被中断时限时收尾。

日志在 `<state>/.live-recordings/<recording_id>.json`，由 core 的录制流程写入。
日志本身不记录是哪个进程在写，所以 agent 的 `live` 录制期间对旁边同名的 `.lock` 持有
排他 flock：恢复时跳过被别人锁着的日志，免得去碰另一个 `live` 进程正在录的分段。

- flock 随持有进程退出（含被 SIGKILL）由内核释放，不受 pid 复用影响。
- 锁文件先在随机名上建好并锁住，再改名成正式名字：别人看到它时它一定已经被锁着，
  不会在「已建未锁」时被恢复方当成过期锁删掉。录制 id 是 uuid，不会有两个进程抢同一把锁。
- 恢复方用只读 fd 加共享锁试探（NFS 上只读 fd 加不了排他锁），非阻塞打开且只认普通文件，
  预先放好的 FIFO 或符号链接卡不住它；判断不了的日志跳过不恢复。
"""

import asyncio
import contextlib
import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent_mode.envelope import add_warning

try:
    import fcntl
except ImportError:  # Windows：退回 core 的「目录静默 30 秒」判断
    fcntl = None

# 被中断后给「把已录的 TS 封装成 MP4」留的时间；超过就留给下次启动恢复
INTERRUPT_FINALIZE_TIMEOUT_S = 60
SEGMENT_INDEXED = "indexed"
JOURNAL_SUFFIX = ".json"
LOCK_SUFFIX = ".lock"
LOCK_FILE_MODE = 0o600
LOCK_TOKEN_BYTES = 8
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_NEW_LOCK_FLAGS = os.O_RDWR | os.O_CREAT | os.O_EXCL | _NOFOLLOW
_PROBE_FLAGS = os.O_RDONLY | _NOFOLLOW | _NONBLOCK


def journal_path(database: Any, config: Any, recording_id: str) -> Path:
    from storage.live_recordings import journal_root

    return journal_root(database, config.get("path")) / (recording_id + JOURNAL_SUFFIX)


class RecordingLock:
    """录制期间对日志旁的 `.lock` 持有排他 flock。拿不到锁不影响录制，只是少一层保护。"""

    def __init__(self) -> None:
        self._fd: Optional[int] = None
        self._path: Optional[Path] = None

    def claim(self, journal: Path) -> None:
        if fcntl is None or self._fd is not None:
            return
        path = journal.with_suffix(LOCK_SUFFIX)
        staging = path.with_name(".%s.%s" % (path.name, secrets.token_hex(LOCK_TOKEN_BYTES)))
        try:
            journal.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(staging), _NEW_LOCK_FLAGS, LOCK_FILE_MODE)
        except OSError:
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.replace(str(staging), str(path))
        except OSError:
            with contextlib.suppress(OSError):
                staging.unlink()
            os.close(fd)
            return
        self._fd, self._path = fd, path

    def release(self) -> None:
        """先删锁文件再关 fd（关 fd 即释放 flock）。"""
        if self._fd is None:
            return
        with contextlib.suppress(OSError):
            self._path.unlink()
        os.close(self._fd)
        self._fd = self._path = None


def _unknown_lock() -> bool:
    add_warning("could not check a live recording lock; skipped recovering that recording")
    return True


def _held_by_running_process(lock: Path) -> bool:
    """锁是否被另一个还在跑的进程持有；持有者已退出的锁顺手删掉，判断不了的当作持有。"""
    try:
        fd = os.open(str(lock), _PROBE_FLAGS)
    except FileNotFoundError:
        return False  # 刚被删掉：那次录制已经结束
    except OSError:
        return _unknown_lock()  # 比如是符号链接
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return _unknown_lock()
        # 共享锁同样与录制方的排他锁冲突，且只读 fd 在 NFS 上也加得上
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        return _unknown_lock()
    finally:
        os.close(fd)
    with contextlib.suppress(OSError):
        lock.unlink()
    return False


def _recoverable_journals(root: Path) -> List[Path]:
    journals = sorted(root.glob("*" + JOURNAL_SUFFIX))
    if fcntl is None:
        return journals
    held = {lock.stem for lock in root.glob("*" + LOCK_SUFFIX) if _held_by_running_process(lock)}
    return [path for path in journals if path.stem not in held]


def read_journal(path: Path) -> Dict[str, Any]:
    """没录到任何分段时日志会被删掉，读不到就当作空。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def recorded_files(journal: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """返回 (已封装好的 MP4, 还没封装的 TS)，都是绝对路径。"""
    finished, pending = [], []
    for entry in (journal.get("segments") or {}).values():
        if entry.get("state") == SEGMENT_INDEXED:
            finished.append(str(Path(entry["target"]).absolute()))
        elif Path(entry.get("source") or "").is_file():
            pending.append(str(Path(entry["source"]).absolute()))
    return finished, pending


async def recover_interrupted(database: Any, config: Any) -> None:
    """与人用 CLI 启动时等价：先把上次中断留下的分段转封装并补登记。"""
    from core.live_finalize import recover_recordings
    from storage.live_recordings import journal_root

    try:
        root = journal_root(database, config.get("path"))
        paths = _recoverable_journals(root)
        if paths:
            await recover_recordings(root, database=database, paths=paths, retry_deferred=False)
    except Exception as exc:  # noqa: BLE001 — 恢复失败不该挡住这次录制
        add_warning("recovering earlier interrupted recordings failed (%s)" % type(exc).__name__)


async def _finalize_pending(path: Path, database: Any) -> None:
    from core.live_finalize import finalize_segment
    from storage.live_recordings import RecordingJournal

    journal = await RecordingJournal.load(path)
    for name, entry in list(journal.data["segments"].items()):
        if entry["state"] != SEGMENT_INDEXED:
            await finalize_segment(journal, name, database=database)


async def finalize_after_interrupt(path: Path, database: Any) -> None:
    """被中断时采集进程已停：限时把已录的分段封装掉。再次被打断或失败都直接放弃。"""
    if not path.is_file():
        return
    try:
        await asyncio.wait_for(_finalize_pending(path, database), INTERRUPT_FINALIZE_TIMEOUT_S)
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        return
