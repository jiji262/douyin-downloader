import os
import re
import time
from pathlib import Path
from typing import Callable, Dict, Optional, Union

import aiofiles
import aiohttp
import httpx

from utils.logger import setup_logger
from utils.validators import sanitize_filename

logger = setup_logger("FileManager")

# sec_uid 是 [A-Za-z0-9_-] 的稳定 token，本身已是文件系统安全字符。仅替换真正
# 非法的路径字符，但【不】折叠连续下划线、不截断长度——legacy DouYin-Downloader
# 的 ``user_<sec_uid>`` 目录用的就是原始 sec_uid（含 ``__``），折叠后会对不上
# 用户已有的目录。详见 ``_AUTHOR_DIR_STYLES`` 的 ``user_sec_uid``。
_SEC_UID_ILLEGAL_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# 媒体下载的流式读取块大小。8KB 会让单流吞吐受限于 chunk 往返开销
# （视频动辄几十 MB），256KB 在内存占用与吞吐之间取平衡。
_DOWNLOAD_CHUNK_BYTES = 256 * 1024

# 媒体下载超时：总时长上限 300s 之外，连接（DNS/TCP/TLS）与读间隙必须
# 单独设限——/aweme/v1/play/ 302 后可能落到打不通的 PCDN 节点（*.qtaeixd.com
# 之类高位端口，SYN/握手黑洞），没有 connect 上限时握手会把 total 300s
# 全部耗光（线上日志实测两次重试间隔正好 300s），批量任务队尾因此停滞。
_DOWNLOAD_TOTAL_TIMEOUT_S = 300
_DOWNLOAD_CONNECT_TIMEOUT_S = 15
_DOWNLOAD_READ_STALL_TIMEOUT_S = 60

# 慢节点地板：冷存储 / PCDN 节点（*-mc-cold.douyinvod.com 之类）握手成功后
# 会以每秒几 KB 滴水式吐字节——一直有数据，sock_read 停滞检测永远不触发，
# 只能等满 total=300s 才失败切换（线上 job f25a60b4850f 队尾一条视频因此
# 静默 5 分钟，换下一个候选后 2 秒下完）。20 KB/s 远低于任何可用链路：
# 真按这个速度，6MB 的视频也要 300s+，本来就跨不过 total 超时，提前放弃
# 只会更早换到健康候选，不会牺牲原本能成功的下载。
_DOWNLOAD_MIN_SPEED_BPS = 20 * 1024
_DOWNLOAD_SLOW_WINDOW_S = 30

# 下载途中向调用方回报进度的最小间隔。批量任务的事件流按条渲染，
# 太密会把事件列表刷屏；2s 既能让 UI 明显「在动」，又不至于淹没日志。
_DOWNLOAD_PROGRESS_INTERVAL_S = 2.0

# 模块级时钟别名，便于测试注入假时钟（真实 sleep 会让用例慢到不可接受）。
_monotonic = time.monotonic


class SlowDownloadError(Exception):
    """节点持续低于最低吞吐——提前放弃，交给候选轮扫换下一个地址。"""


class _ThroughputGuard:
    """滚动窗口吞吐地板判定。

    按窗口而不是累计均值判定：累计均值会被「开头很快、后面卡成滴水」
    的节点骗过去，而这正是 PCDN 抽签失手后的典型形态。
    """

    def __init__(self, min_bps: int, window_s: float, clock=None):
        self._min_bps = min_bps
        self._window_s = window_s
        self._clock = clock or _monotonic
        self._window_start = self._clock()
        self._window_bytes = 0

    def feed(self, nbytes: int) -> None:
        """记入一块数据；本窗口持续低于地板时抛 :class:`SlowDownloadError`。"""
        self._window_bytes += nbytes
        elapsed = self._clock() - self._window_start
        if elapsed < self._window_s:
            return
        if self._window_bytes < self._min_bps * elapsed:
            raise SlowDownloadError(
                f"{self._window_bytes / elapsed / 1024:.1f} KB/s over {elapsed:.0f}s "
                f"(floor {self._min_bps / 1024:.0f} KB/s)"
            )
        self._window_start = self._clock()
        self._window_bytes = 0


def _emit_progress(on_progress, bytes_read: int, bytes_total: int) -> None:
    """进度回调是尽力而为的旁路：reporter 抛错不能拖垮下载本身。"""
    if on_progress is None:
        return
    try:
        on_progress(bytes_read, bytes_total)
    except Exception as exc:
        logger.debug("Download progress callback failed: %s", exc)


class FileManager:
    _IMAGE_CONTENT_TYPE_SUFFIXES = {
        "image/gif": ".gif",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }

    # 作者目录层可选风格（与 DEFAULT_CONFIG["author_dir"]、REST SettingsPatch
    # 的 Literal、前端下拉三处保持一致）。
    _AUTHOR_DIR_STYLES = ("nickname", "sec_uid", "nickname_uid", "user_sec_uid")

    def __init__(self, base_path: str = "./Downloaded"):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def get_author_dir(
        self,
        author_name: str,
        *,
        author_sec_uid: Optional[str] = None,
        author_dir_style: str = "nickname",
    ) -> Path:
        """Return (and create) the configured author-level directory."""
        safe_author = self._compose_author_dir(
            author_name,
            author_sec_uid,
            author_dir_style,
        )
        author_dir = self.base_path / safe_author
        author_dir.mkdir(parents=True, exist_ok=True)
        return author_dir

    def get_save_path(
        self,
        author_name: str,
        mode: str = None,
        aweme_title: str = None,
        aweme_id: str = None,
        folderstyle: bool = True,
        download_date: str = "",
        folder_name: Optional[str] = None,
        *,
        author_sec_uid: Optional[str] = None,
        author_dir_style: str = "nickname",
        group_by_mode: bool = True,
        collection_dir: Optional[str] = None,
    ) -> Path:
        """Compute (and create) the destination directory for a download.

        ``folder_name`` is the pre-rendered, already-sanitized leaf directory
        name produced by ``utils.naming.render_template``. When provided, it
        overrides the legacy ``{date}_{title}_{id}`` layout. When omitted we
        fall back to the historical composition so external callers and the
        sibling CLI project keep working unchanged.

        ``author_dir_style`` controls how the author-level directory is
        composed (see :data:`_AUTHOR_DIR_STYLES`). Unknown values or missing
        ``author_sec_uid`` fall back to ``nickname`` with a ``WARNING`` so
        downloads never fail on a misconfiguration.

        ``group_by_mode`` controls whether the download mode (``post`` /
        ``like`` / ``mix`` …) gets its own sub-directory under the author. When
        ``False`` the mode layer is dropped entirely, so files land directly
        under the author directory (reproducing the legacy layout with no
        ``POST`` folder). It is independent of ``folderstyle`` (the per-aweme
        sub-folder).

        ``collection_dir`` inserts one more directory between the mode layer
        and the per-aweme leaf, so each 合集 (mix) lands in its own folder
        (``base/<author>/mix/<collection>/<leaf>``). It is sanitized here;
        empty / whitespace-only values insert nothing (legacy layout).
        """
        author_dir = self.get_author_dir(
            author_name,
            author_sec_uid=author_sec_uid,
            author_dir_style=author_dir_style,
        )

        if mode and group_by_mode:
            save_dir = author_dir / mode
        else:
            save_dir = author_dir

        # Only insert a collection layer for a genuinely non-empty name;
        # a blank/whitespace value must reproduce the legacy layout rather
        # than sanitize into an ``untitled`` folder.
        if collection_dir and str(collection_dir).strip():
            save_dir = save_dir / sanitize_filename(str(collection_dir).strip())

        if folderstyle:
            leaf = folder_name
            if leaf is None and aweme_title and aweme_id:
                safe_title = sanitize_filename(aweme_title)
                date_prefix = f"{download_date}_" if download_date else ""
                leaf = f"{date_prefix}{safe_title}_{aweme_id}"
            if leaf:
                save_dir = save_dir / leaf

        save_dir.mkdir(parents=True, exist_ok=True)
        return save_dir

    @classmethod
    def _compose_author_dir(
        cls,
        author_name: str,
        author_sec_uid: Optional[str],
        style: str,
    ) -> str:
        """Build the sanitized author-level directory name per ``style``.

        Behaviour matrix (kept in lock-step with the ``author_dir`` option
        surfaced in settings UI and ``DEFAULT_CONFIG``):

        - ``nickname``     → ``sanitize_filename(author_name)`` (legacy)
        - ``sec_uid``      → ``sanitize_filename(author_sec_uid)``;
          empty/None → fall back to nickname + ``logger.warning``.
        - ``nickname_uid`` → ``sanitize_filename(f"{author_name}_{author_sec_uid}")``;
          sec_uid missing → fall back to nickname + ``logger.warning``.
        - ``user_sec_uid`` → ``user_<raw sec_uid>`` to reproduce the legacy
          DouYin-Downloader layout. Uses the underscore-preserving
          :meth:`_sanitize_sec_uid_token` (NOT ``sanitize_filename``) so real
          sec_uids containing ``__`` keep their double underscore and match the
          user's existing ``user_MS4...__...`` directories. sec_uid missing →
          fall back to nickname + ``logger.warning``.
        - Unknown style    → fall back to nickname + ``logger.warning``.

        Never raises — a misconfiguration must degrade into a still-working
        download, not a hard failure.
        """
        nickname_dir = sanitize_filename(author_name)
        sec_uid = (author_sec_uid or "").strip()

        if style not in cls._AUTHOR_DIR_STYLES:
            logger.warning(
                "Unknown author_dir style %r, falling back to nickname (%s)",
                style,
                nickname_dir,
            )
            return nickname_dir

        if style == "nickname":
            return nickname_dir

        if style == "sec_uid":
            if not sec_uid:
                logger.warning(
                    "author_dir=sec_uid but sec_uid is missing for %r, falling back to nickname",
                    author_name,
                )
                return nickname_dir
            return sanitize_filename(sec_uid)

        if style == "user_sec_uid":
            if not sec_uid:
                logger.warning(
                    "author_dir=user_sec_uid but sec_uid is missing for %r, "
                    "falling back to nickname",
                    author_name,
                )
                return nickname_dir
            return f"user_{cls._sanitize_sec_uid_token(sec_uid)}"

        # style == "nickname_uid"
        if not sec_uid:
            logger.warning(
                "author_dir=nickname_uid but sec_uid is missing for %r, falling back to nickname",
                author_name,
            )
            return nickname_dir
        return sanitize_filename(f"{author_name}_{sec_uid}")

    @staticmethod
    def _sanitize_sec_uid_token(sec_uid: str) -> str:
        """Minimal sanitize for a sec_uid used as (part of) a directory name.

        Unlike :func:`utils.validators.sanitize_filename`, this preserves
        consecutive underscores and does not truncate — sec_uids are stable
        ``[A-Za-z0-9_-]`` tokens, and the legacy on-disk layout used them raw.
        Only genuinely illegal path characters are replaced, as defense in
        depth.
        """
        return _SEC_UID_ILLEGAL_RE.sub("_", sec_uid).strip("._- ")

    async def download_file(
        self,
        url: str,
        save_path: Path,
        session: aiohttp.ClientSession = None,
        headers: Optional[Dict[str, str]] = None,
        proxy: Optional[str] = None,
        *,
        prefer_response_content_type: bool = False,
        return_saved_path: bool = False,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> Union[bool, Path]:
        should_close = False
        if session is None:
            default_headers = headers or {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Referer": "https://www.douyin.com/",
                "Accept": "*/*",
            }
            session = aiohttp.ClientSession(headers=default_headers)
            should_close = True

        final_path = save_path
        tmp_path = save_path.with_suffix(save_path.suffix + ".tmp")
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(
                    total=_DOWNLOAD_TOTAL_TIMEOUT_S,
                    connect=_DOWNLOAD_CONNECT_TIMEOUT_S,
                    sock_read=_DOWNLOAD_READ_STALL_TIMEOUT_S,
                ),
                headers=headers,
                proxy=proxy or None,
            ) as response:
                if response.status == 200:
                    return await self._persist_stream(
                        response.content.iter_chunked(_DOWNLOAD_CHUNK_BYTES),
                        save_path,
                        response.content_length,
                        response.headers,
                        prefer_response_content_type=prefer_response_content_type,
                        return_saved_path=return_saved_path,
                        on_progress=on_progress,
                    )
                if response.status == 206:
                    expected_size = self._complete_content_range_size(response.headers)
                    if expected_size is None:
                        logger.warning(
                            "Rejected incomplete range response for %s: %s",
                            final_path.name,
                            response.headers.get("Content-Range") if response.headers else None,
                        )
                        return False
                    return await self._persist_stream(
                        response.content.iter_chunked(_DOWNLOAD_CHUNK_BYTES),
                        save_path,
                        expected_size,
                        response.headers,
                        prefer_response_content_type=prefer_response_content_type,
                        return_saved_path=return_saved_path,
                        on_progress=on_progress,
                    )
                status = response.status
                logger.debug("Download failed for %s, status=%s", final_path.name, status)
            # aiohttp connection released here. Douyin's image CDN 403s aiohttp's
            # TLS fingerprint for some assets (e.g. ``biz_tag=pcweb_cover`` covers)
            # while serving httpx/curl/requests fine, so retry those via httpx.
            if status == 403:
                return await self._download_via_httpx(
                    url,
                    save_path,
                    headers=headers,
                    proxy=proxy,
                    prefer_response_content_type=prefer_response_content_type,
                    return_saved_path=return_saved_path,
                    on_progress=on_progress,
                )
            return False
        except SlowDownloadError as e:
            # 明确区别于普通失败：这条走 WARNING，免得排查时又只看到
            # 一条空消息的 DEBUG（旧行为下 asyncio.TimeoutError 的 str 为空）。
            logger.warning("Abandoned slow node for %s: %s", final_path.name, e)
            tmp_path.unlink(missing_ok=True)
            return False
        except Exception as e:
            logger.debug("Download error for %s: %s", final_path.name, e)
            tmp_path.unlink(missing_ok=True)
            return False
        finally:
            if should_close:
                await session.close()

    @staticmethod
    def _complete_content_range_size(response_headers) -> Optional[int]:
        if not response_headers:
            return None
        content_range = response_headers.get("Content-Range")
        if not content_range:
            return None
        match = re.match(r"^bytes (\d+)-(\d+)/(\d+)$", content_range.strip())
        if not match:
            return None
        start, end, total = (int(part) for part in match.groups())
        if start != 0 or end + 1 != total:
            return None
        return total

    async def _persist_stream(
        self,
        chunk_iter,
        save_path: Path,
        expected_size: Optional[int],
        response_headers,
        *,
        prefer_response_content_type: bool = False,
        return_saved_path: bool = False,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> Union[bool, Path]:
        """Stream ``chunk_iter`` to a temp file and atomically rename it.

        Shared by the aiohttp and httpx download paths so the content-type
        resolution, size-mismatch guard, and atomic rename stay identical.
        """
        final_path = self._resolve_save_path_from_content_type(
            save_path,
            response_headers,
            prefer_response_content_type=prefer_response_content_type,
        )
        tmp_path = final_path.with_suffix(final_path.suffix + ".tmp")
        try:
            written = await self._stream_to_tmp(chunk_iter, tmp_path, expected_size, on_progress)
        except BaseException:
            # 任何中断——慢节点提前放弃、单条作品超时、用户取消任务——都不该
            # 把半截 .tmp 留在下载目录里。CancelledError 是 BaseException，
            # 单捕 Exception 会漏掉取消这条最常见的路径。
            tmp_path.unlink(missing_ok=True)
            raise
        if expected_size is not None and written != expected_size:
            logger.warning(
                "Size mismatch for %s: expected %d, got %d",
                final_path.name,
                expected_size,
                written,
            )
            tmp_path.unlink(missing_ok=True)
            return False
        os.replace(str(tmp_path), str(final_path))
        return final_path if return_saved_path else True

    @staticmethod
    async def _stream_to_tmp(chunk_iter, tmp_path: Path, expected_size, on_progress) -> int:
        """把响应体写进临时文件，途中做吞吐地板判定并按节流回报进度。"""
        guard = _ThroughputGuard(_DOWNLOAD_MIN_SPEED_BPS, _DOWNLOAD_SLOW_WINDOW_S)
        last_emit = _monotonic()
        emitted = False
        written = 0
        async with aiofiles.open(tmp_path, "wb") as f:
            async for chunk in chunk_iter:
                await f.write(chunk)
                written += len(chunk)
                # 已经收齐声明字节数时不再判定：数据都拿全了，「追不上」
                # 没有意义（小封面可能一整块下完但整体耗时越过判定窗口）。
                if expected_size is None or written < expected_size:
                    guard.feed(len(chunk))
                now = _monotonic()
                if on_progress is not None and now - last_emit >= _DOWNLOAD_PROGRESS_INTERVAL_S:
                    last_emit = now
                    emitted = True
                    _emit_progress(on_progress, written, expected_size or 0)
        # 收尾补一条 100%，但只补给「途中报过进度」的下载：秒下的小文件
        # 本来就没有假死问题，不该白白占掉事件流那 250 条的回放窗口。
        if emitted:
            _emit_progress(on_progress, written, expected_size or 0)
        return written

    async def _download_via_httpx(
        self,
        url: str,
        save_path: Path,
        *,
        headers: Optional[Dict[str, str]] = None,
        proxy: Optional[str] = None,
        prefer_response_content_type: bool = False,
        return_saved_path: bool = False,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> Union[bool, Path]:
        """Download an asset via httpx, whose TLS fingerprint the Douyin image
        CDN accepts when aiohttp's is rejected (403). Mirrors aiohttp's
        redirect-following and streaming-to-disk behaviour."""
        try:
            async with httpx.AsyncClient(
                # httpx 没有 aiohttp 那样的整体 wall-clock 上限：位置参数只作为
                # write/pool 的默认值，connect/read 为逐操作超时。
                timeout=httpx.Timeout(
                    _DOWNLOAD_TOTAL_TIMEOUT_S,
                    connect=_DOWNLOAD_CONNECT_TIMEOUT_S,
                    read=_DOWNLOAD_READ_STALL_TIMEOUT_S,
                ),
                proxy=proxy or None,
                follow_redirects=True,
            ) as client:
                async with client.stream("GET", url, headers=headers) as response:
                    if response.status_code != 200:
                        logger.debug(
                            "httpx fallback failed for %s, status=%s",
                            save_path.name,
                            response.status_code,
                        )
                        return False
                    # httpx auto-decompresses; Content-Length is the *compressed*
                    # size, so only trust it when the body isn't encoded.
                    expected_size: Optional[int] = None
                    if not response.headers.get("Content-Encoding"):
                        content_length = response.headers.get("Content-Length")
                        if content_length is not None and content_length.isdigit():
                            expected_size = int(content_length)
                    return await self._persist_stream(
                        response.aiter_bytes(),
                        save_path,
                        expected_size,
                        response.headers,
                        prefer_response_content_type=prefer_response_content_type,
                        return_saved_path=return_saved_path,
                        on_progress=on_progress,
                    )
        except SlowDownloadError:
            # 交给 download_file 统一按 WARNING 记账，别在这里降级成 DEBUG。
            raise
        except Exception as e:
            logger.debug("httpx fallback error for %s: %s", save_path.name, e)
            return False

    @classmethod
    def _resolve_save_path_from_content_type(
        cls,
        save_path: Path,
        response_headers,
        *,
        prefer_response_content_type: bool = False,
    ) -> Path:
        if not prefer_response_content_type:
            return save_path

        content_type = response_headers.get("Content-Type", "") if response_headers else ""
        normalized_type = content_type.split(";", 1)[0].strip().lower()
        suffix = cls._IMAGE_CONTENT_TYPE_SUFFIXES.get(normalized_type)
        if not suffix:
            return save_path
        return save_path.with_suffix(suffix)

    def file_exists(self, file_path: Path) -> bool:
        try:
            return file_path.exists() and file_path.stat().st_size > 0
        except OSError:
            return False

    def get_file_size(self, file_path: Path) -> int:
        try:
            return file_path.stat().st_size if self.file_exists(file_path) else 0
        except OSError:
            return 0
