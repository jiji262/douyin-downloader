"""音频抽取管线。在调用转录 API 之前把视频抽成低带宽 mp3。

ffmpeg 二进制通过 ``imageio-ffmpeg`` PyPI 包提供并由 PyInstaller 打入
sidecar onefile；运行时不依赖系统 ``PATH`` 上的 ffmpeg。

设计参考： ``.kiro/specs/transcript-audio-extract-and-ui/design.md``
"""
from __future__ import annotations

import asyncio
import collections
import os
import time
from pathlib import Path
from typing import Optional

from utils.logger import setup_logger

logger = setup_logger("AudioExtraction")


# ---------------------------------------------------------------------------
# Public exceptions
# ---------------------------------------------------------------------------


class AudioExtractError(Exception):
    """所有抽音失败的基类。

    ``str(exc)`` 总以 ``audio_extract_failed: <cause>`` 开头，与
    requirements R6.2 / R6.3 字面契约一致；上层
    :class:`core.transcript_manager.TranscriptManager` 不再二次包装。
    """

    cause: str = "unknown"

    def __init__(self, detail: str = "") -> None:
        prefix = f"audio_extract_failed: {self.cause}"
        super().__init__(f"{prefix}: {detail}" if detail else prefix)


class FfmpegNotAvailable(AudioExtractError):
    """``imageio_ffmpeg.get_ffmpeg_exe()`` 找不到二进制、或 ``ffmpeg
    -version`` 探测失败。"""

    cause = "ffmpeg_not_available"


class FfmpegTimeout(AudioExtractError):
    """ffmpeg 抽音子进程在 :data:`_FFMPEG_TIMEOUT_SECONDS` 内未结束。"""

    cause = "audio_extract_timeout"


class FfmpegNonZeroExit(AudioExtractError):
    """ffmpeg 抽音子进程以非零退出码结束。"""

    cause = "nonzero_exit_code"


class AudioExtractEmpty(AudioExtractError):
    """ffmpeg 退出码为 0，但写出的目标文件不存在或大小为 0 字节。"""

    cause = "audio_extract_empty"


class PlatformUnsupported(AudioExtractError):
    """``imageio-ffmpeg`` 在当前 OS / 架构上没有静态二进制（极少数
    边角平台，例如某些 Linux ARM 子架构）。本质是 :class:`FfmpegNotAvailable`
    的特例，但用独立 ``cause`` 让上层日志能区分。"""

    cause = "platform_unsupported"


# ---------------------------------------------------------------------------
# FfmpegLocator
# ---------------------------------------------------------------------------


_AVAILABILITY_TTL_SECONDS = 60.0
"""可用性缓存的 TTL（requirements R2.7）。"""

_VERSION_PROBE_TIMEOUT_SECONDS = 5.0
"""``ffmpeg -version`` 探测的硬超时（requirements R2.5）。"""


class FfmpegLocator:
    """单例：缓存 ffmpeg 路径与可用性，避免每次抽音都重探。

    第一次调用 :meth:`locate` 触发 ``imageio_ffmpeg.get_ffmpeg_exe()``
    取路径并跑一次 ``<ffmpeg> -version``。后续 60 秒内复用缓存。
    """

    _instance: Optional["FfmpegLocator"] = None

    def __init__(self) -> None:
        self._path: Optional[str] = None
        self._version: Optional[str] = None
        self._available: Optional[bool] = None
        self._cached_at: float = 0.0
        self._last_error: Optional[str] = None
        self._lock = asyncio.Lock()

    # -- public API ---------------------------------------------------------

    @classmethod
    def instance(cls) -> "FfmpegLocator":
        """模块级单例。在测试里通过 :meth:`reset_for_tests` 清掉。"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_for_tests(cls) -> None:
        """仅供测试使用：清空单例与缓存。"""
        cls._instance = None

    async def locate(self) -> str:
        """返回可执行 ffmpeg 路径。

        Raises:
            FfmpegNotAvailable: 缓存中没有可用 ffmpeg 时（路径找不到、
                ``-version`` 探测失败、平台不支持等）。
        """
        async with self._lock:
            await self._refresh_if_needed()
            if not self._available:
                raise FfmpegNotAvailable(self._last_error or "unknown")
            assert self._path is not None  # for mypy
            return self._path

    async def diagnostic(self) -> dict:
        """返回 ``GET /api/v1/transcript/diagnostic`` 的字段三元组。"""
        async with self._lock:
            await self._refresh_if_needed()
        return {
            "ffmpeg_available": bool(self._available),
            "ffmpeg_path": self._path or "",
            "ffmpeg_version": self._version,
        }

    # -- internals ----------------------------------------------------------

    async def _refresh_if_needed(self) -> None:
        now = time.monotonic()
        if (
            self._available is not None
            and (now - self._cached_at) < _AVAILABILITY_TTL_SECONDS
        ):
            return
        await self._probe()
        self._cached_at = time.monotonic()

    async def _probe(self) -> None:
        # Step 1: resolve binary path
        try:
            import imageio_ffmpeg  # local import — keeps sidecar bootable
            # if the optional dep is missing in a dev env
            path = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as exc:  # imageio_ffmpeg raises RuntimeError on
            # platforms without a bundled binary AND no system ffmpeg.
            self._path = None
            self._version = None
            self._available = False
            self._last_error = f"get_ffmpeg_exe failed: {exc!r}"
            logger.warning(
                "imageio_ffmpeg.get_ffmpeg_exe() failed: %r", exc
            )
            return

        if not path or not os.path.exists(path):
            self._path = path or None
            self._version = None
            self._available = False
            self._last_error = f"ffmpeg path missing: {path!r}"
            return

        # Step 2: probe `<ffmpeg> -version`
        proc: Optional[asyncio.subprocess.Process] = None
        try:
            proc = await asyncio.create_subprocess_exec(
                path,
                "-version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_VERSION_PROBE_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            if proc is not None:
                await _kill_and_reap(proc)
            self._path = path
            self._version = None
            self._available = False
            self._last_error = "ffmpeg -version timed out"
            logger.warning("ffmpeg -version timed out at %s", path)
            return
        except Exception as exc:
            if proc is not None:
                await _kill_and_reap(proc)
            self._path = path
            self._version = None
            self._available = False
            self._last_error = f"subprocess error: {exc!r}"
            logger.warning("ffmpeg -version subprocess error: %r", exc)
            return

        if proc.returncode != 0 or b"ffmpeg version" not in stdout.lower():
            self._path = path
            self._version = None
            self._available = False
            self._last_error = (
                f"ffmpeg -version exit={proc.returncode}, "
                f"stdout_head={stdout[:64]!r}"
            )
            return

        first_line = (
            stdout.split(b"\n", 1)[0]
            .decode("utf-8", errors="replace")
            .strip()
        )
        self._path = path
        self._version = first_line
        self._available = True
        self._last_error = None


# ---------------------------------------------------------------------------
# Public extraction API
# ---------------------------------------------------------------------------


_FFMPEG_TIMEOUT_SECONDS = 600.0
"""ffmpeg 抽音子进程硬超时（requirements R1.10 / R1.11）。"""

_STDERR_RING_LIMIT_BYTES = 1 * 1024 * 1024
"""stderr 环形缓冲上限（requirements R1.12）。"""

_STDERR_TAIL_BYTES = 4096
"""非零退出时返回给上层的 stderr 末尾字节数（requirements R1.13）。"""

_FFMPEG_EXTRACT_ARGS = (
    "-vn",
    "-ac",
    "1",
    "-ar",
    "16000",
    "-b:a",
    "32k",
    "-f",
    "mp3",
)
"""固定参数列表（requirements R1.2）。tuple 而非 list 以防意外变更。"""


async def extract_audio(
    video_path: Path,
    output_dir: Path,
    *,
    locator: Optional[FfmpegLocator] = None,
) -> Path:
    """把 ``video_path`` 抽成 ``<stem>.mp3``，写到 ``output_dir`` 并返回路径。

    成功条件：
    - ffmpeg 在 :data:`_FFMPEG_TIMEOUT_SECONDS` 内退出
    - 退出码 0
    - 输出文件大小严格大于 0 字节

    Raises:
        FfmpegNotAvailable: ffmpeg 二进制不可用（``locate()`` 抛出）。
        FfmpegTimeout: 子进程 600 秒未结束。
        FfmpegNonZeroExit: 子进程非零退出。
        AudioExtractEmpty: 子进程退出码 0，但输出文件不存在或为 0 字节。
    """
    locator = locator or FfmpegLocator.instance()
    ffmpeg_path = await locator.locate()  # may raise FfmpegNotAvailable

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{video_path.stem}.mp3"

    args = (
        ffmpeg_path,
        "-y",  # overwrite output if it somehow already exists
        "-i",
        str(video_path),
        *_FFMPEG_EXTRACT_ARGS,
        str(output_path),
    )

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )

    # Bounded stderr capture — bytes from the tail of the stream
    # (requirements R1.12). ``maxlen`` on a deque of int (bytes) costs O(1)
    # per append.
    stderr_ring: collections.deque = collections.deque(
        maxlen=_STDERR_RING_LIMIT_BYTES
    )

    async def _drain_stderr() -> None:
        assert proc.stderr is not None  # we set stderr=PIPE above
        while True:
            chunk = await proc.stderr.read(8192)
            if not chunk:
                break
            stderr_ring.extend(chunk)

    try:
        await asyncio.wait_for(
            asyncio.gather(_drain_stderr(), proc.wait()),
            timeout=_FFMPEG_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        await _kill_and_reap(proc)
        _safe_unlink(output_path)
        raise FfmpegTimeout(f"timeout after {int(_FFMPEG_TIMEOUT_SECONDS)}s")
    except BaseException:
        # Anything else (cancellation, drain_stderr crash, etc.) — leave
        # the proc reaped so we don't orphan an ffmpeg process. Re-raise
        # so the caller still sees the original failure.
        await _kill_and_reap(proc)
        _safe_unlink(output_path)
        raise

    if proc.returncode != 0:
        tail_bytes = bytes(stderr_ring)[-_STDERR_TAIL_BYTES:]
        tail = tail_bytes.decode("utf-8", errors="replace")
        _safe_unlink(output_path)
        raise FfmpegNonZeroExit(
            f"exit={proc.returncode}; stderr_tail={tail!r}"
        )

    try:
        size = output_path.stat().st_size if output_path.exists() else 0
    except OSError:
        size = 0
    if size <= 0:
        _safe_unlink(output_path)
        raise AudioExtractEmpty(
            f"output missing or empty at {output_path}"
        )

    return output_path


async def _kill_and_reap(proc: asyncio.subprocess.Process) -> None:
    """Best-effort: kill ``proc`` and wait up to 5 s for it to exit so the
    caller doesn't leave an orphaned ffmpeg process behind. Used by both
    the timeout path and the catch-all on ``extract_audio``'s gather.
    """
    try:
        proc.kill()
    except ProcessLookupError:
        # Already exited — nothing to do.
        return
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("failed to kill ffmpeg pid=%s: %r", proc.pid, exc)
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except asyncio.TimeoutError:  # pragma: no cover - 5s should be plenty
        logger.warning(
            "ffmpeg pid=%s did not exit within 5s after kill", proc.pid
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("ffmpeg pid=%s reap failed: %r", proc.pid, exc)


def _safe_unlink(path: Path) -> None:
    """Delete ``path`` ignoring ``FileNotFoundError`` and other OS errors.
    Used on failure paths where we want to leave no half-written file
    behind but a cleanup failure must not eclipse the original error."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:  # pragma: no cover - defensive
        logger.warning("failed to unlink %s: %r", path, exc)


__all__ = [
    "AudioExtractEmpty",
    "AudioExtractError",
    "FfmpegLocator",
    "FfmpegNonZeroExit",
    "FfmpegNotAvailable",
    "FfmpegTimeout",
    "PlatformUnsupported",
    "extract_audio",
]
