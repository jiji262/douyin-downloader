import asyncio
import json
import re
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple
from urllib.parse import urlparse

import aiohttp

from auth import CookieManager
from config import ConfigLoader
from control import QueueManager, RateLimiter, RetryHandler
from core.api_client import DouyinAPIClient
from core.metadata import (
    build_author_home_url,
    extract_author_sec_uid,
    extract_video_cover_urls,
)
from core.transcript_manager import TranscriptManager
from storage import Database, FileManager, MetadataHandler
from storage.database import order_cover_mirrors
from utils.logger import setup_logger
from utils.naming import (
    DEFAULT_FILE_TEMPLATE,
    DEFAULT_FOLDER_TEMPLATE,
    build_aweme_context,
    render_template,
)
from utils.paid_content import (
    detect_mp4_encryption,
    is_paid_content,
    paid_content_warning,
)

logger = setup_logger("BaseDownloader")

# 进程级本地作品索引缓存（按下载根目录）。批量任务会为每个 job 新建
# downloader 实例，没有缓存时每个 job 首次下载前都要 rglob 全库一遍。
# 同一根目录的所有实例共享同一个集合对象，_mark_local_aweme_downloaded
# 的增量更新对后续 job 立即可见。缓存伴随进程存活：会话期间在应用外
# 手动删除的文件要到进程重启后才会被重新检测（与原先单 job 内的索引
# 是同一权衡，只是范围从单 job 扩大到进程）。
_LOCAL_AWEME_INDEX_CACHE: Dict[str, set[str]] = {}

# 单条视频的兜底总时限。单次请求的 total 超时（storage.file_manager 里的
# 300s）只约束一次尝试，而 _download_video_with_fallback 会「候选数 × 重试
# 轮数」地把它乘起来：4 轮 × 4 候选最坏能挂 80 分钟，整个队列跟着停摆。
# 15 分钟对正常大小的作品绰绰有余（真跑满说明这条已经没救了）。
_VIDEO_ITEM_DEADLINE_S = 900


class ProgressReporter(Protocol):
    def update_step(self, step: str, detail: str = "") -> None: ...

    def set_item_total(self, total: int, detail: str = "") -> None: ...

    def advance_item(self, status: str, detail: str = "") -> None: ...


class DownloadResult:
    def __init__(self):
        self.total = 0
        self.success = 0
        self.failed = 0
        self.skipped = 0

    def __str__(self):
        return f"Total: {self.total}, Success: {self.success}, Failed: {self.failed}, Skipped: {self.skipped}"


class BaseDownloader(ABC):
    def __init__(
        self,
        config: ConfigLoader,
        api_client: DouyinAPIClient,
        file_manager: FileManager,
        cookie_manager: CookieManager,
        database: Optional[Database] = None,
        rate_limiter: Optional[RateLimiter] = None,
        retry_handler: Optional[RetryHandler] = None,
        queue_manager: Optional[QueueManager] = None,
        progress_reporter: Optional[ProgressReporter] = None,
        job_id: Optional[str] = None,
    ):
        self.config = config
        self.api_client = api_client
        self.file_manager = file_manager
        self.cookie_manager = cookie_manager
        self.database = database
        self.rate_limiter = rate_limiter or RateLimiter()
        self.retry_handler = retry_handler or RetryHandler()
        thread_count = int(self.config.get("thread", 5) or 5)
        self.queue_manager = queue_manager or QueueManager(max_workers=thread_count)
        self.progress_reporter = progress_reporter
        # Server job that spawned this downloader (empty for CLI runs);
        # written onto each aweme row for the History job cross-filter.
        self.job_id = job_id
        self.metadata_handler = MetadataHandler()
        self.transcript_manager = TranscriptManager(self.config, self.file_manager, self.database)
        self._local_aweme_ids: Optional[set[str]] = None
        # 延迟到事件循环内创建（3.9 上 asyncio.Lock() 在无运行循环时创建
        # 会绑定错误的 loop）。
        self._local_index_build_lock: Optional[asyncio.Lock] = None
        self._aweme_id_pattern = re.compile(r"(?<!\d)(\d{15,20})(?!\d)")
        self._local_media_suffixes = {
            ".mp4",
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".gif",
            ".mp3",
            ".m4a",
        }
        # 控制终端错误日志量，避免进度条被大量日志打断后出现重复重绘。
        self._download_error_log_count = 0
        self._download_error_log_limit = 5
        # 本次任务已解析过的作者目录，键是 (昵称, sec_uid, 目录风格)。
        # 只为「打开输出文件夹」上报一次，避免每条作品都多敲一次 mkdir。
        self._author_dir_cache: Dict[Tuple[str, str, str], Path] = {}

    def _progress_update_step(self, step: str, detail: str = "") -> None:
        if not self.progress_reporter:
            return
        try:
            self.progress_reporter.update_step(step, detail)
        except Exception as exc:
            logger.debug("Progress update_step failed: %s", exc)

    def _progress_set_item_total(self, total: int, detail: str = "") -> None:
        if not self.progress_reporter:
            return
        try:
            self.progress_reporter.set_item_total(total, detail)
        except Exception as exc:
            logger.debug("Progress set_item_total failed: %s", exc)

    def _progress_advance_item(self, status: str, detail: str = "") -> None:
        if not self.progress_reporter:
            return
        try:
            self.progress_reporter.advance_item(status, detail)
        except Exception as exc:
            logger.debug("Progress advance_item failed: %s", exc)

    def _make_item_progress(self, aweme_id: Optional[str]):
        """构造单文件下载途中的字节进度回调（没有 reporter / id 时返回 None）。

        批量任务原先只在整条作品的全部资产下完后才 advance_item，单个大
        文件或慢节点期间事件流完全静止——线上 job f25a60b4850f 队尾一条
        视频静默 5 分钟，用户判定为卡死并连续取消了 4 个任务。
        """
        reporter = self.progress_reporter
        if not reporter or not aweme_id:
            return None
        emit = getattr(reporter, "on_item_progress", None)
        if not callable(emit):
            return None

        def _on_progress(bytes_read: int, bytes_total: int) -> None:
            try:
                emit(aweme_id=aweme_id, bytes_read=bytes_read, bytes_total=bytes_total)
            except Exception as exc:
                logger.debug("Progress on_item_progress failed: %s", exc)

        return _on_progress

    def _report_author_output_dir(
        self,
        author_name: str,
        author_sec_uid: Optional[str],
        author_dir_style: str,
    ) -> None:
        """把作品实际落盘的作者目录报给宿主任务。

        任务卡片的「打开输出文件夹」要落在当前博主目录，而不是设置里的下载
        根目录。跨作者任务（收藏夹）与去重由 reporter 侧聚合处理，见
        ``QueueProgressReporter.on_output_dir``；这里只负责在每个作者第一次
        出现时把目录算出来上报一次。
        """
        if not self.progress_reporter:
            return
        key = (author_name, author_sec_uid or "", author_dir_style)
        if key in self._author_dir_cache:
            return
        try:
            author_dir = self.file_manager.get_author_dir(
                author_name,
                author_sec_uid=author_sec_uid,
                author_dir_style=author_dir_style,
            )
        except Exception as exc:
            logger.debug("Resolve author dir for progress failed: %s", exc)
            return
        self._author_dir_cache[key] = author_dir
        try:
            fn = getattr(self.progress_reporter, "on_output_dir", None)
            if callable(fn):
                fn(path=str(author_dir))
        except Exception as exc:
            logger.debug("Progress on_output_dir failed: %s", exc)

    def _progress_report_author(
        self,
        nickname: Optional[str] = None,
        sec_uid: Optional[str] = None,
    ) -> None:
        """Surface author metadata to the reporter so the hosting job can
        cache it for retry and display.

        Downloaders call this as soon as author info is known (user_info
        lookup for batch jobs, aweme_data.author for single-video jobs).
        Safe to call with `None` values — the reporter drops empty payloads.
        """
        if not self.progress_reporter:
            return
        try:
            fn = getattr(self.progress_reporter, "on_author", None)
            if callable(fn):
                fn(nickname=nickname, sec_uid=sec_uid)
        except Exception as exc:  # pragma: no cover — defensive
            logger.debug("Progress on_author failed: %s", exc)

    def _log_download_error(self, log_fn, message: str) -> None:
        if self._download_error_log_count < self._download_error_log_limit:
            log_fn(message)
        elif self._download_error_log_count == self._download_error_log_limit:
            logger.error("Too many download errors, suppressing further per-file logs...")
        self._download_error_log_count += 1

    def _download_headers(self, user_agent: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "Referer": f"{self.api_client.BASE_URL}/",
            "Origin": self.api_client.BASE_URL,
            "Accept": "*/*",
        }

        headers["User-Agent"] = user_agent or self.api_client.headers.get("User-Agent", "")
        return headers

    @abstractmethod
    async def download(self, parsed_url: Dict[str, Any]) -> DownloadResult:
        pass

    async def _should_download(self, aweme_id: str) -> bool:
        await self._ensure_local_aweme_index()
        in_local = self._is_locally_downloaded(aweme_id)
        in_db = False
        if self.database:
            in_db = await self.database.is_downloaded(aweme_id)

        if in_db and in_local:
            return False

        if in_db and not in_local:
            logger.info(
                "Aweme %s exists in database but media file not found locally, retry download",
                aweme_id,
            )
            return True

        if in_local:
            logger.info("Aweme %s already exists locally, skipping", aweme_id)
            return False

        return True

    async def _ensure_local_aweme_index(self) -> None:
        """在工作线程中完成首次全库扫描建索引。

        扫描是同步 rglob + stat 重活；直接跑在事件循环里会把整个 HTTP
        服务冻住（大库/慢盘可达分钟级），桌面端看门狗会在连续两次
        /health 失联后强杀后台服务、丢掉运行中的 job。加锁避免并发
        worker 对同一根目录重复扫描。
        """
        if self._local_aweme_ids is not None:
            return
        lock = self._local_index_build_lock
        if lock is None:
            lock = self._local_index_build_lock = asyncio.Lock()
        async with lock:
            if self._local_aweme_ids is not None:
                return
            await asyncio.to_thread(self._build_local_aweme_index)

    def _is_locally_downloaded(self, aweme_id: str) -> bool:
        if not aweme_id:
            return False

        if self._local_aweme_ids is None:
            self._build_local_aweme_index()

        if self._local_aweme_ids is None:
            return False
        return aweme_id in self._local_aweme_ids

    def _build_local_aweme_index(self):
        base_path = self.file_manager.base_path
        cache_key = str(base_path.resolve())
        cached = _LOCAL_AWEME_INDEX_CACHE.get(cache_key)
        if cached is not None:
            self._local_aweme_ids = cached
            return

        aweme_ids: set[str] = set()

        if base_path.exists():
            for path in base_path.rglob("*"):
                if not path.is_file():
                    continue
                if path.suffix.lower() not in self._local_media_suffixes:
                    continue
                # Optional sidecars include the aweme id in their filename but
                # do not mean the primary video/gallery asset exists. Ignoring
                # them lets a later run with ``video: true`` fetch the actual
                # video after an earlier cover-only archive.
                if path.stem.lower().endswith(("_cover", "_avatar", "_music")):
                    continue
                try:
                    if path.stat().st_size <= 0:
                        continue
                except OSError:
                    continue
                for match in self._aweme_id_pattern.finditer(path.name):
                    aweme_ids.add(match.group(1))

        self._local_aweme_ids = aweme_ids
        _LOCAL_AWEME_INDEX_CACHE[cache_key] = aweme_ids

    def _mark_local_aweme_downloaded(self, aweme_id: str):
        if not aweme_id:
            return

        if self._local_aweme_ids is None:
            # 绑定共享缓存集合后再标记。retry_executor 直接调用
            # _download_aweme_assets（不经过 _should_download），此时索引
            # 还未建；若落入实例私有集合，id 进不了进程级缓存，同进程的
            # 后续 job 会把该作品当缺失重新下载。缓存命中时这里是纯字典
            # 查找，零额外成本。
            self._build_local_aweme_index()
        if self._local_aweme_ids is None:  # pragma: no cover — 防御
            self._local_aweme_ids = set()
        self._local_aweme_ids.add(aweme_id)

    def _filter_by_time(self, aweme_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        start_time = self.config.get("start_time")
        end_time = self.config.get("end_time")

        if not start_time and not end_time:
            return aweme_list

        start_ts = (
            int(datetime.strptime(start_time, "%Y-%m-%d").timestamp()) if start_time else None
        )
        end_ts = int(datetime.strptime(end_time, "%Y-%m-%d").timestamp()) if end_time else None

        filtered: List[Dict[str, Any]] = []
        for aweme in aweme_list:
            create_time = aweme.get("create_time", 0)
            if start_ts is not None and create_time < start_ts:
                continue
            if end_ts is not None and create_time > end_ts:
                continue
            filtered.append(aweme)

        return filtered

    def _limit_count(self, aweme_list: List[Dict[str, Any]], mode: str) -> List[Dict[str, Any]]:
        number_config = self.config.get("number", {})
        limit = number_config.get(mode, 0)

        if limit > 0:
            return aweme_list[:limit]
        return aweme_list

    def _comments_config(self) -> Optional[Dict[str, Any]]:
        comments_cfg = self.config.get("comments") or {}
        if isinstance(comments_cfg, dict) and comments_cfg.get("enabled"):
            return comments_cfg
        return None

    def _aweme_file_metadata(self, aweme_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        aweme_id = aweme_data.get("aweme_id")
        if not aweme_id:
            logger.error("Missing aweme_id in aweme data")
            return None

        desc = (aweme_data.get("desc", "no_title") or "").strip() or "no_title"
        publish_ts, publish_date = self._resolve_publish_time(aweme_data.get("create_time"))
        if not publish_date:
            publish_date = datetime.now().strftime("%Y-%m-%d")
            logger.warning(
                "Aweme %s missing/invalid create_time, fallback to current date %s",
                aweme_id,
                publish_date,
            )
        return {
            "aweme_id": aweme_id,
            "desc": desc,
            "publish_ts": publish_ts,
            "publish_date": publish_date,
            "media_type": self._detect_media_type(aweme_data),
        }

    def _render_aweme_file_names(
        self,
        aweme_data: Dict[str, Any],
        metadata: Dict[str, Any],
        author_name: str,
        mode: Optional[str],
        author_sec_uid: Optional[str] = None,
    ) -> Dict[str, str]:
        template_context = build_aweme_context(
            aweme_id=str(metadata["aweme_id"]),
            title=str(metadata["desc"]),
            author_name=author_name,
            author_sec_uid=author_sec_uid or extract_author_sec_uid(aweme_data),
            publish_date=str(metadata["publish_date"]),
            publish_ts=metadata["publish_ts"],
            media_type=str(metadata["media_type"]),
            mode=mode,
        )
        fallback = f"{metadata['publish_date']}_{metadata['aweme_id']}"
        return {
            "file_stem": render_template(
                self.config.get("filename_template") or DEFAULT_FILE_TEMPLATE,
                template_context,
                fallback=fallback,
            ),
            "folder_name": render_template(
                self.config.get("folder_template") or DEFAULT_FOLDER_TEMPLATE,
                template_context,
                fallback=fallback,
            ),
        }

    def _build_aweme_file_context(
        self,
        aweme_data: Dict[str, Any],
        author_name: str,
        mode: Optional[str] = None,
        *,
        author_sec_uid: Optional[str] = None,
        collection_dir: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        metadata = self._aweme_file_metadata(aweme_data)
        if metadata is None:
            return None

        effective_sec_uid = author_sec_uid or extract_author_sec_uid(aweme_data)
        names = self._render_aweme_file_names(
            aweme_data,
            metadata,
            author_name,
            mode,
            author_sec_uid=effective_sec_uid,
        )
        author_dir_style = self.config.get("author_dir") or "nickname"
        self._report_author_output_dir(author_name, effective_sec_uid, author_dir_style)
        save_dir = self.file_manager.get_save_path(
            author_name=author_name,
            mode=mode,
            aweme_title=metadata["desc"],
            aweme_id=metadata["aweme_id"],
            folderstyle=self.config.get("folderstyle", True),
            download_date=metadata["publish_date"],
            folder_name=names["folder_name"],
            author_sec_uid=effective_sec_uid,
            author_dir_style=author_dir_style,
            group_by_mode=self.config.get("group_by_mode", True),
            collection_dir=collection_dir,
        )
        return {**metadata, "file_stem": names["file_stem"], "save_dir": save_dir}

    async def _save_comments(
        self,
        aweme_id: str,
        comments_path: Path,
        comments_cfg: Dict[str, Any],
    ) -> bool:
        from core.comments_collector import CommentsCollector

        collector = CommentsCollector(
            self.api_client,
            self.metadata_handler,
            include_replies=bool(comments_cfg.get("include_replies", False)),
            max_comments=int(comments_cfg.get("max_comments", 0) or 0),
            page_size=int(comments_cfg.get("page_size", 20) or 20),
        )
        saved = await collector.collect_and_save(str(aweme_id), comments_path)
        return saved is not None

    async def _collect_comments_for_existing_aweme(
        self,
        aweme_data: Dict[str, Any],
        author_name: str,
        mode: Optional[str] = None,
        *,
        author_sec_uid: Optional[str] = None,
        collection_dir: Optional[str] = None,
    ) -> bool:
        comments_cfg = self._comments_config()
        if comments_cfg is None:
            return False

        file_context = self._build_aweme_file_context(
            aweme_data,
            author_name,
            mode,
            author_sec_uid=author_sec_uid,
            collection_dir=collection_dir,
        )
        if file_context is None:
            return False

        comments_path = file_context["save_dir"] / f"{file_context['file_stem']}_comments.json"
        if comments_path.exists() and comments_path.stat().st_size > 0:
            return False

        return await self._save_comments(
            str(file_context["aweme_id"]),
            comments_path,
            comments_cfg,
        )

    async def _download_aweme_assets(
        self,
        aweme_data: Dict[str, Any],
        author_name: str,
        mode: Optional[str] = None,
        *,
        db_batch: Optional[List[Dict[str, Any]]] = None,
        author_sec_uid: Optional[str] = None,
        collection_dir: Optional[str] = None,
    ) -> bool:
        # retry_executor 直接调本方法（不经过 _should_download），先在
        # 线程里把索引建好，避免成功后的 _mark_local_aweme_downloaded
        # 在事件循环里触发同步全库扫描。
        await self._ensure_local_aweme_index()
        file_context = self._build_aweme_file_context(
            aweme_data,
            author_name,
            mode,
            author_sec_uid=author_sec_uid,
            collection_dir=collection_dir,
        )
        if file_context is None:
            return False

        aweme_id = file_context["aweme_id"]
        desc = file_context["desc"]
        publish_ts = file_context["publish_ts"]
        publish_date = file_context["publish_date"]
        media_type = file_context["media_type"]
        file_stem = file_context["file_stem"]
        save_dir = file_context["save_dir"]
        downloaded_files: List[Path] = []

        session = await self.api_client.get_session()
        video_path: Optional[Path] = None
        primary_media_downloaded = False
        # 可选资产（封面/音乐/头像）相互独立且不影响主媒体成败：统一登记
        # (保存路径, 未 await 的协程) 后并行下载，避免串行等待每个附件
        # （尤其是慢镜像）时占住全局下载并发槽。视频关闭时这些附件仍可
        # 独立保存，因此封面归档不再强制要求先下载 mp4。
        optional_assets: List[Tuple[Path, Any]] = []

        if media_type == "video":
            paid_note = paid_content_warning(aweme_data)
            if paid_note:
                logger.warning("Aweme %s: %s", aweme_id, paid_note)
            if self.config.get("video", True):
                video_candidates = self._build_video_url_candidates(aweme_data)
                if not video_candidates:
                    logger.error("No playable video URL found for aweme %s", aweme_id)
                    return False
                video_candidates = await self._maybe_promote_original_candidate(
                    aweme_data, video_candidates, session
                )

                video_path = save_dir / f"{file_stem}.mp4"
                if not await self._download_video_with_fallback(
                    video_candidates, video_path, session, aweme_id=aweme_id
                ):
                    return False
                if not self._discard_if_encrypted(video_path, aweme_id):
                    return False
                downloaded_files.append(video_path)
                primary_media_downloaded = True

            if self.config.get("cover"):
                cover_urls = extract_video_cover_urls(aweme_data)
                if cover_urls:
                    cover_path = save_dir / f"{file_stem}_cover.jpg"
                    optional_assets.append(
                        (
                            cover_path,
                            self._download_first_available(
                                cover_urls,
                                cover_path,
                                session,
                                headers=self._download_headers(),
                                optional=True,
                            ),
                        )
                    )

            if self.config.get("music"):
                music_url = self._extract_first_url(aweme_data.get("music", {}).get("play_url"))
                if music_url:
                    music_path = save_dir / f"{file_stem}_music.mp3"
                    optional_assets.append(
                        (
                            music_path,
                            self._download_with_retry(
                                music_url,
                                music_path,
                                session,
                                headers=self._download_headers(),
                                optional=True,
                            ),
                        )
                    )

        elif media_type == "gallery":
            image_url_candidates = self._collect_image_url_candidates(aweme_data)
            image_live_urls = self._collect_image_live_urls(aweme_data)
            logger.info(
                "Gallery aweme %s: %d image(s), %d live photo(s)",
                aweme_id,
                len(image_url_candidates),
                len(image_live_urls),
            )
            if not image_url_candidates and not image_live_urls:
                logger.error(
                    "No gallery assets found for aweme %s (aweme_type=%s, "
                    "has image_post_info=%s, has images=%s)",
                    aweme_id,
                    aweme_data.get("aweme_type"),
                    "image_post_info" in aweme_data,
                    "images" in aweme_data,
                )
                return False

            for index, candidates in enumerate(image_url_candidates, start=1):
                download_result: bool | Path = False
                # 与 _download_first_available 同原则：多镜像时镜像列表本身
                # 就是重试机制（每镜像单次尝试），单镜像才保留退避重试——
                # 否则死镜像 × 每镜像 4 次退避嵌套，一张图最坏能拖数分钟。
                use_backoff = len(candidates) == 1
                for url_index, image_url in enumerate(candidates):
                    is_last = url_index == len(candidates) - 1
                    suffix = self._infer_image_extension(image_url)
                    image_path = save_dir / f"{file_stem}_{index}{suffix}"
                    download_result = await self._download_with_retry(
                        image_url,
                        image_path,
                        session,
                        headers=self._download_headers(),
                        prefer_response_content_type=True,
                        return_saved_path=True,
                        optional=not is_last,
                        retry=use_backoff,
                    )
                    if download_result:
                        downloaded_files.append(
                            download_result if isinstance(download_result, Path) else image_path
                        )
                        break
                if not download_result:
                    logger.error(f"Failed downloading image {index} for aweme {aweme_id}")
                    return False

            for index, live_url in enumerate(image_live_urls, start=1):
                suffix = Path(urlparse(live_url).path).suffix or ".mp4"
                live_path = save_dir / f"{file_stem}_live_{index}{suffix}"
                success = await self._download_with_retry(
                    live_url,
                    live_path,
                    session,
                    headers=self._download_headers(),
                )
                if not success:
                    logger.error(f"Failed downloading live image {index} for aweme {aweme_id}")
                    return False
                downloaded_files.append(live_path)
            primary_media_downloaded = True
        else:
            logger.error("Unsupported media type for aweme %s: %s", aweme_id, media_type)
            return False

        if self.config.get("avatar"):
            author = aweme_data.get("author", {})
            avatar_source = author.get("avatar_larger")
            if self._extract_urls(avatar_source):
                avatar_path = save_dir / f"{file_stem}_avatar.jpg"
                optional_assets.append(
                    (
                        avatar_path,
                        self._download_first_available(
                            avatar_source,
                            avatar_path,
                            session,
                            headers=self._download_headers(),
                            optional=True,
                        ),
                    )
                )

        if optional_assets:
            # 不变量：登记的协程（_download_first_available / _download_with_retry）
            # 内部捕获所有 Exception 并返回 False，不会让 gather 抛出——
            # 往这里登记新的协程时必须保持同样的不抛异常约定。
            outcomes = await asyncio.gather(*(coro for _, coro in optional_assets))
            for (asset_path, _), outcome in zip(optional_assets, outcomes):
                if outcome:
                    downloaded_files.append(outcome if isinstance(outcome, Path) else asset_path)

        if self.config.get("json"):
            json_path = save_dir / f"{file_stem}_data.json"
            if await self.metadata_handler.save_metadata(aweme_data, json_path):
                downloaded_files.append(json_path)

        comments_cfg = self._comments_config()
        if comments_cfg is not None:
            comments_path = save_dir / f"{file_stem}_comments.json"
            if await self._save_comments(str(aweme_id), comments_path, comments_cfg):
                downloaded_files.append(comments_path)

        if not downloaded_files:
            logger.error(
                "No assets were downloaded for aweme %s; enable video or another asset option",
                aweme_id,
            )
            return False

        author = aweme_data.get("author", {})
        if self.database:
            metadata_json = json.dumps(aweme_data, ensure_ascii=False)
            cover_list = extract_video_cover_urls(aweme_data)
            if not cover_list:
                # Image posts carry no video cover — fall back to the first
                # image's mirrors (same rule as the my-content projection).
                images = aweme_data.get("images")
                first_image = images[0] if isinstance(images, list) and images else None
                if isinstance(first_image, dict):
                    cover_list = first_image.get("url_list")
            record = {
                "aweme_id": aweme_id,
                "aweme_type": media_type,
                "title": desc,
                "author_id": author.get("uid"),
                "author_name": author.get("nickname", author_name),
                "create_time": aweme_data.get("create_time"),
                "file_path": str(save_dir),
                "metadata": metadata_json,
                # Attach sec_uid onto the payload so both the batched path
                # (add_aweme_batch iterates `record["author_sec_uid"]`) and
                # the single-write path (add_aweme reads the payload as
                # fallback when the kwarg is None) pick it up identically.
                "author_sec_uid": extract_author_sec_uid(aweme_data),
                "cover_urls": json.dumps(
                    order_cover_mirrors(cover_list if isinstance(cover_list, list) else [])
                ),
                "job_id": self.job_id or "",
            }
            # Caller may opt into batched DB writes by passing a list; we just
            # accumulate the record and let the caller commit them all at once.
            if db_batch is not None:
                db_batch.append(record)
            else:
                await self.database.add_aweme(record)

        # Same precedence as `_build_aweme_file_context`: a sec_uid the caller
        # resolved from the profile URL outranks the payload's author block,
        # which the upstream API sometimes trims.
        manifest_sec_uid = author_sec_uid or extract_author_sec_uid(aweme_data)
        manifest_record = {
            "date": publish_date,
            "aweme_id": aweme_id,
            "author_name": author.get("nickname", author_name),
            # Fixed schema — always present, "" when unknown — so manifest
            # consumers never branch on a missing key. `author_name` alone
            # cannot survive a nickname change or a collision.
            "author_sec_uid": manifest_sec_uid or "",
            "author_url": build_author_home_url(manifest_sec_uid) or "",
            "desc": desc,
            "media_type": media_type,
            "tags": self._extract_tags(aweme_data),
            "file_names": [path.name for path in downloaded_files],
            "file_paths": [self._to_manifest_path(path) for path in downloaded_files],
        }
        if publish_ts:
            manifest_record["publish_timestamp"] = publish_ts
        await self.metadata_handler.append_download_manifest(
            self.file_manager.base_path, manifest_record
        )

        if media_type == "video" and video_path is not None:
            transcript_result = await self.transcript_manager.process_video(
                video_path, aweme_id=aweme_id
            )
            transcript_status = transcript_result.get("status")
            if transcript_status == "skipped":
                logger.info(
                    "Transcript skipped for aweme %s: %s",
                    aweme_id,
                    transcript_result.get("reason", "unknown"),
                )
            elif transcript_status == "failed":
                logger.warning(
                    "Transcript failed for aweme %s: %s",
                    aweme_id,
                    transcript_result.get("error", "unknown"),
                )

        if primary_media_downloaded:
            self._mark_local_aweme_downloaded(aweme_id)
        logger.info("Downloaded selected assets for %s: %s (%s)", media_type, desc, aweme_id)
        return True

    async def _download_with_retry(
        self,
        url: str,
        save_path: Path,
        session,
        *,
        headers: Optional[Dict[str, str]] = None,
        optional: bool = False,
        prefer_response_content_type: bool = False,
        return_saved_path: bool = False,
        retry: bool = True,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> bool | Path:
        async def _task():
            download_result = await self.file_manager.download_file(
                url,
                save_path,
                session,
                headers=headers,
                proxy=getattr(self.api_client, "proxy", None),
                prefer_response_content_type=prefer_response_content_type,
                return_saved_path=return_saved_path,
                on_progress=on_progress,
            )
            if not download_result:
                raise RuntimeError(f"Download failed for {url}")
            return download_result

        try:
            if retry:
                return await self.retry_handler.execute_with_retry(_task)
            return await _task()
        except Exception as error:
            log_fn = logger.warning if optional else logger.error
            self._log_download_error(
                log_fn,
                f"Download error for {save_path.name}: {error}",
            )
            return False

    async def _download_video_with_fallback(
        self,
        candidates: List[Tuple[str, Dict[str, str]]],
        save_path: Path,
        session,
        *,
        aweme_id: Optional[str] = None,
    ) -> bool:
        """在候选地址间降级下载视频：每轮按序各尝试一次，整轮失败再退避重试。

        play 端点的失败多为 302 落点抽签不走运（PCDN 死节点），重试同一
        URL 有意义；直连地址失败（403/过期）则应换下一候选。按轮扫 +
        RetryHandler 退避兼顾两种失败模式，单候选时行为与旧退避重试一致。

        ``_VIDEO_ITEM_DEADLINE_S`` 是整条作品的兜底时限：候选数 × 重试轮数
        会把单次超时预算乘起来（旧行为最坏 4 轮 × 4 候选 × 300s ≈ 80 分钟），
        没有总时限时一条视频就能把整个队列拖死。
        """
        if not candidates:
            return False

        on_progress = self._make_item_progress(aweme_id)

        async def _attempt_round() -> bool:
            for url, headers in candidates:
                if await self._download_with_retry(
                    url,
                    save_path,
                    session,
                    headers=headers,
                    optional=True,
                    retry=False,
                    on_progress=on_progress,
                ):
                    return True
            raise RuntimeError(
                f"All {len(candidates)} video url candidate(s) failed for {save_path.name}"
            )

        return await self._run_within_item_deadline(
            self.retry_handler.execute_with_retry(_attempt_round), save_path
        )

    def _discard_if_encrypted(self, video_path: Path, aweme_id: Optional[str]) -> bool:
        """落盘的 mp4 若是 DRM 密文就删掉并判失败。

        付费作品的 ``download_addr`` 是 CENC 加密的全长正片，容器与 NAL
        分帧都正常（进度条能拖、时长正确），只有 slice 内容是密文，播放
        器一律花屏无声。密钥只由抖音授权接口下发，留着这份文件既占空间
        又会让人误以为下载成功。
        """
        scheme = detect_mp4_encryption(video_path)
        if not scheme:
            return True
        self._log_download_error(
            logger.error,
            f"Aweme {aweme_id}: 下载到的是 {scheme.upper()} 加密流（付费/会员内容），"
            f"本地无法播放，已删除 {video_path.name}",
        )
        video_path.unlink(missing_ok=True)
        return False

    async def _run_within_item_deadline(self, awaitable, save_path: Path) -> bool:
        """给单条作品的下载套上兜底总时限，超时/失败一律归为「这条没下成」。"""
        try:
            return await asyncio.wait_for(awaitable, timeout=_VIDEO_ITEM_DEADLINE_S)
        except asyncio.TimeoutError:
            self._log_download_error(
                logger.warning,
                f"Video download deadline ({_VIDEO_ITEM_DEADLINE_S}s) exceeded "
                f"for {save_path.name}",
            )
            return False
        except Exception:
            return False

    async def _download_first_available(
        self,
        source: Any,
        save_path: Path,
        session,
        *,
        headers: Optional[Dict[str, str]] = None,
        optional: bool = False,
        **kwargs,
    ) -> bool | Path:
        """Download an asset, falling back across every mirror in ``url_list``.

        Douyin serves images from multiple CDN mirrors (e.g. p3/p9). The first
        mirror occasionally returns 403 while a later one succeeds, so try each
        URL in turn and return on the first success instead of giving up after
        ``url_list[0]`` and silently dropping the asset.

        存在多个镜像时，镜像列表本身就是重试机制：每个镜像只尝试一次，
        避免在已知会持续 403 的死镜像上叠加多轮退避重试（单个封面最多
        可拖慢 20+ 秒并占用下载并发槽）。仅单一 URL 时保留退避重试。
        """
        urls = self._extract_urls(source)
        use_backoff = len(urls) == 1
        result: bool | Path = False
        for index, url in enumerate(urls):
            is_last = index == len(urls) - 1
            result = await self._download_with_retry(
                url,
                save_path,
                session,
                headers=headers,
                # Keep earlier-mirror failures quiet; only the final attempt
                # reflects the caller's chosen log level.
                optional=optional or not is_last,
                retry=use_backoff,
                **kwargs,
            )
            if result:
                return result
        return result

    # aweme_type codes that indicate image/note content
    _GALLERY_AWEME_TYPES = {2, 68, 150}
    _PLAY_ADDR_KEYS = (
        "play_addr_h264",
        "play_addr_265",
        "play_addr_256",
        "play_addr",
    )

    def _detect_media_type(self, aweme_data: Dict[str, Any]) -> str:
        if self._iter_gallery_items(aweme_data):
            return "gallery"
        aweme_type = aweme_data.get("aweme_type")
        if isinstance(aweme_type, int) and aweme_type in self._GALLERY_AWEME_TYPES:
            video = aweme_data.get("video") if isinstance(aweme_data.get("video"), dict) else {}
            if self._has_video_source(video):
                logger.info(
                    "Detected note video via aweme_type=%s for aweme %s",
                    aweme_type,
                    aweme_data.get("aweme_id"),
                )
                return "video"
            logger.info(
                "Detected gallery via aweme_type=%s for aweme %s",
                aweme_type,
                aweme_data.get("aweme_id"),
            )
            return "gallery"
        return "video"

    def _build_no_watermark_url(
        self, aweme_data: Dict[str, Any]
    ) -> Optional[Tuple[str, Dict[str, str]]]:
        """Best single video URL (kept for existing callers/tests);
        see :meth:`_build_video_url_candidates` for the preference order."""
        candidates = self._build_video_url_candidates(aweme_data)
        return candidates[0] if candidates else None

    def _build_video_url_candidates(
        self, aweme_data: Dict[str, Any]
    ) -> List[Tuple[str, Dict[str, str]]]:
        """按优先级返回可依次尝试的视频下载地址（url + 请求头）。

        直连 CDN（douyinvod.com 等）优先于 ``/aweme/v1/play/`` 签名端点：
        play 端点 302 后可能落到打不通的 PCDN 节点（``*.qtaeixd.com`` 高位
        端口），直连域名走标准 CDN（commit 099aae5 声明的意图，此前被循环
        内对 douyin.com 候选的提前 return 打破）。play 端点保留为降级候选，
        供直连失败时兜底。没有净版候选时按旧逻辑回退：uri 构造签名地址 →
        带水印地址。
        """
        video = aweme_data.get("video", {})
        quality = str(self.config.get("video_quality") or "highest")
        play_addr = self._pick_preferred_play_addr(video, quality) or {}
        url_candidates = [c for c in (play_addr.get("url_list") or []) if c]
        url_candidates.sort(key=lambda u: 0 if "watermark=0" in u else 1)

        direct_candidates, play_candidate, watermarked_candidate = self._partition_video_candidates(
            url_candidates
        )

        candidates: List[Tuple[str, Dict[str, str]]] = list(direct_candidates)
        if play_candidate:
            candidates.append(self._sign_play_candidate(play_candidate))
        if candidates:
            return candidates

        constructed = self._build_signed_play_url(
            video, play_addr, quality, paid=is_paid_content(aweme_data)
        )
        if constructed:
            return [constructed]
        if watermarked_candidate:
            return [watermarked_candidate]
        return []

    def _partition_video_candidates(
        self, url_candidates: List[str]
    ) -> Tuple[
        List[Tuple[str, Dict[str, str]]],
        Optional[str],
        Optional[Tuple[str, Dict[str, str]]],
    ]:
        """把 url_list 分拣为（全部净版直连镜像, 首个净版 play 端点, 首个带水印）。

        直连镜像常有 2-3 个不同主机（v3/v9 等），全部保留并维持 url_list
        原序——只取第一个会在镜像 1 挂掉时跳过健康的镜像 2、直接进 play
        端点的 PCDN 抽签。play 端点多条等价（同一端点），取首个即可，签名
        推迟到真正选用时（:meth:`_sign_play_candidate`）；带水印取首个作
        最后兜底。
        """
        direct: List[Tuple[str, Dict[str, str]]] = []
        play: Optional[str] = None
        watermarked: Optional[Tuple[str, Dict[str, str]]] = None

        for candidate in url_candidates:
            is_watermarked = self._is_watermarked_media_url(candidate)

            if urlparse(candidate).netloc.endswith("douyin.com"):
                if is_watermarked:
                    if watermarked is None:
                        watermarked = self._sign_play_candidate(candidate)
                    continue
                play = play or candidate
                continue

            if is_watermarked:
                watermarked = watermarked or (candidate, self._download_headers())
            else:
                direct.append((candidate, self._download_headers()))

        return direct, play, watermarked

    def _sign_play_candidate(self, candidate: str) -> Tuple[str, Dict[str, str]]:
        """Sign a douyin.com play URL when it lacks X-Bogus; pass through otherwise."""
        if "X-Bogus=" not in candidate:
            signed_url, ua = self.api_client.sign_url(candidate)
            return signed_url, self._download_headers(user_agent=ua)
        return candidate, self._download_headers()

    def _build_signed_play_url(
        self,
        video: Dict[str, Any],
        play_addr: Dict[str, Any],
        quality: str,
        *,
        paid: bool = False,
    ) -> Optional[Tuple[str, Dict[str, str]]]:
        uri = self._pick_source_uri(video, play_addr, paid=paid)
        if not uri:
            return None
        # Douyin /aweme/v1/play/ accepts a limited set of ratio strings.
        # Preserve the selected track's actual tier when its direct URLs
        # cannot be used; otherwise fall back to the configured preference.
        selected_entry = self._find_bit_rate_entry(video, play_addr)
        short_edge, _ = self._resolution_metrics(selected_entry, play_addr)
        selected_ratio = f"{short_edge}p"
        normalised_quality = quality.strip().lower()
        ratio_map = {
            # original 只是 highest 加一次原画探测；走到签名 play 端点这条
            # 备用链时两者的 ratio 完全一致。列出来是为了让意图可读——未知
            # 值本来就 fallback 到 1080p。
            "original": "1080p",
            "highest": "1080p",
            "lowest": "540p",
        }
        fallback_ratio = ratio_map.get(
            normalised_quality,
            normalised_quality if normalised_quality in self._QUALITY_TARGET_WIDTH else "1080p",
        )
        ratio = selected_ratio if selected_ratio in self._QUALITY_TARGET_WIDTH else fallback_ratio
        params = {
            "video_id": uri,
            "ratio": ratio,
            "line": "0",
            "is_play_url": "1",
            "watermark": "0",
            "source": "PackSourceEnum_PUBLISH",
        }
        signed_url, ua = self.api_client.build_signed_path("/aweme/v1/play/", params)
        return signed_url, self._download_headers(user_agent=ua)

    # /aweme/v1/play/ 的 ratio=default 会 302 到上传原片。web detail API 的
    # bit_rate 阶梯不含该档(实测最高档可比原画小 8 倍),原画只能走这里。
    _ORIGINAL_PROBE_TIMEOUT_SECONDS = 10

    async def _maybe_promote_original_candidate(
        self,
        aweme_data: Dict[str, Any],
        candidates: List[Tuple[str, Dict[str, str]]],
        session,
    ) -> List[Tuple[str, Dict[str, str]]]:
        """original 画质下探测原画,比所选档大时置顶为首选候选。

        置顶前必须比较真实大小:存在超分重编码档大于原画的反例(如
        7508597705644985612,档 47.4M > 原画 31.7M),盲选原画会降质。
        置顶的是探测时已跟随 302 的直连 CDN 地址,避免下载时重抽 play
        端点的 PCDN 落点;探测失败时保持原候选链,行为与旧版一致。
        """
        quality = str(self.config.get("video_quality") or "highest").strip().lower()
        # 探测有代价(原片可达转码档 8 倍体积,每条多一次超时上限 10s 的请求),
        # 所以它是 original 这一档的显式语义,而不是 highest 的隐含行为——
        # 藏在 highest 里用户既看不见也关不掉。默认值仍是 highest,即默认不探。
        if quality != "original":
            return candidates
        # 付费作品的 play_addr 就是试看渲染版本身，ratio=default 探到的
        # 是同一份资产（实测大小逐字节相等），探测只是白搭一次请求；真正
        # 的「原片」是要不起的 CENC 全长正片，置顶它只会下到一份密文。
        if is_paid_content(aweme_data):
            return candidates
        video = aweme_data.get("video") if isinstance(aweme_data.get("video"), dict) else {}
        play_addr = self._pick_preferred_play_addr(video, quality) or {}
        uri = self._pick_source_uri(video, play_addr, paid=is_paid_content(aweme_data))
        if not uri:
            return candidates
        probed = await self._probe_original_play_source(
            str(uri),
            session,
            need_set_cookie=bool(video.get("is_need_set_cookie")),
        )
        if not probed:
            return candidates
        original_url, original_size = probed
        try:
            selected_size = int(play_addr.get("data_size") or 0)
        except (TypeError, ValueError):
            selected_size = 0
        if selected_size and original_size <= selected_size:
            return candidates
        return [(original_url, self._download_headers())] + candidates

    async def _probe_original_play_source(
        self, uri: str, session, *, need_set_cookie: bool = False
    ) -> Optional[Tuple[str, int]]:
        """Range 取 1 字节探测原画地址,返回 (302 落点 URL, 文件总大小)。

        content-type 必须是 video/*:WAF 拦截页等 200+HTML 响应不得被
        误判为原画。任何异常都返回 None,由调用方退回原候选链。
        """
        params = {
            "video_id": uri,
            "ratio": "default",
            "line": "0",
            "is_play_url": "1",
            "watermark": "0",
            "source": "PackSourceEnum_PUBLISH",
        }
        if need_set_cookie:
            # 受限作品(video.is_need_set_cookie)官方 App 会附带此标记,
            # 缺失时 play 端点会拒绝。
            params["ss_is_p_v_ss"] = "1"
        signed_url, ua = self.api_client.build_signed_path("/aweme/v1/play/", params)
        headers = {**self._download_headers(user_agent=ua), "Range": "bytes=0-0"}
        try:
            async with session.get(
                signed_url,
                headers=headers,
                allow_redirects=True,
                proxy=getattr(self.api_client, "proxy", None) or None,
                timeout=aiohttp.ClientTimeout(total=self._ORIGINAL_PROBE_TIMEOUT_SECONDS),
            ) as resp:
                if resp.status not in (200, 206):
                    raise ValueError(f"unexpected status {resp.status}")
                if not str(resp.content_type or "").startswith("video/"):
                    raise ValueError(f"unexpected content-type {resp.content_type}")
                content_range = str(resp.headers.get("Content-Range") or "")
                raw_total = (
                    content_range.rsplit("/", 1)[-1]
                    if "/" in content_range
                    else resp.headers.get("Content-Length")
                )
                total = int(raw_total or 0)
                if total <= 0:
                    raise ValueError(f"missing total size (Content-Range={content_range!r})")
                return str(resp.url), total
        except Exception as error:
            # 探测是 best-effort:任何失败(网络、WAF 403 限速、签名、假
            # session)都必须退回原候选链,绝不能让主下载流程因此中断。但要
            # 留下可见线索:play 端点被限速时批量任务会整批降级到可小数倍
            # 的转码档,只有 debug 日志用户根本无从察觉。复用限量日志防刷屏。
            self._log_download_error(
                logger.warning,
                f"Original-quality probe failed for {uri}: {error}; "
                "falling back to transcoded tracks",
            )
            return None

    # 视频画质选择支持的规格名称。值保留为横屏长边尺寸，用于兼容只有
    # width 的旧响应；完整 width/height 响应会按短边匹配 1080p/720p 等档位。
    _QUALITY_TARGET_WIDTH: Dict[str, int] = {
        "1440p": 2560,
        "1080p": 1920,
        "720p": 1280,
        "540p": 960,
        "480p": 854,
        "360p": 640,
    }

    @staticmethod
    def _pick_source_uri(
        video: Dict[str, Any], play_addr: Dict[str, Any], *, paid: bool = False
    ) -> Optional[str]:
        """挑选构造播放地址用的 vid，付费内容跳过 ``download_addr``。

        付费作品的 ``play_addr`` 与 ``download_addr`` 是转码期生成的两份
        不同资产：前者明文（试看窗口外画面已被预渲染成模糊、音轨静音），
        后者是 CENC 加密的全长正片。回退到 download_addr 只会下到一份解
        不开的密文，还不如就用明文的试看版。
        """
        uri = play_addr.get("uri") or video.get("vid")
        if not uri and not paid:
            uri = (video.get("download_addr") or {}).get("uri")
        return uri or None

    @staticmethod
    def _find_bit_rate_entry(video: Dict[str, Any], play_addr: Dict[str, Any]) -> Dict[str, Any]:
        entries = video.get("bit_rate") if isinstance(video, dict) else None
        if not isinstance(entries, list):
            return {}
        for entry in entries:
            if isinstance(entry, dict) and entry.get("play_addr") is play_addr:
                return entry
        return {}

    @staticmethod
    def _resolution_metrics(entry: Dict[str, Any], play_addr: Dict[str, Any]) -> Tuple[int, int]:
        """Return ``(short_edge, pixels)`` for portrait and landscape tracks."""
        try:
            width = int(play_addr.get("width") or entry.get("width") or 0)
            height = int(play_addr.get("height") or entry.get("height") or 0)
        except (TypeError, ValueError):
            return 0, 0
        if width > 0 and height > 0:
            return min(width, height), width * height
        long_edge = max(width, height)
        if long_edge <= 0:
            return 0, 0
        nearest = min(
            BaseDownloader._QUALITY_TARGET_WIDTH.items(),
            key=lambda item: abs(item[1] - long_edge),
        )[0]
        short_edge = int(nearest[:-1])
        return short_edge, long_edge * short_edge

    @staticmethod
    def _pick_highest_quality_play_addr(video: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Shortcut for highest-quality selection (backward-compatible).

        Kept for older callers; new code should use
        :meth:`_pick_play_addr_by_quality` with an explicit quality string.
        """
        return BaseDownloader._pick_play_addr_by_quality(video, "highest")

    @staticmethod
    def _pick_play_addr_by_quality(
        video: Dict[str, Any], quality: str = "highest"
    ) -> Optional[Dict[str, Any]]:
        """从 video.bit_rate 多档率中按目标画质挑选 play_addr。

        抖音 API 的 ``video.bit_rate`` 是按质量排序的字典列表，每项含
        ``bit_rate``（码率）与 ``play_addr``（内含 URL、width 与 height）。

        - ``highest`` / 未知值 / 空：取分辨率最高的档；同分辨率按 bit_rate 决胜。
        - ``lowest``：取 bit_rate 最小的档；同码率按 width 小者优先。
        - ``<N>p``（如 ``1080p``）：按画面短边最接近目标值的档；完全没有 bit_rate
          数据时返回 ``None``，让调用方回退到 ``video.play_addr``。
        """
        bit_rates = video.get("bit_rate") if isinstance(video, dict) else None
        if not isinstance(bit_rates, list) or not bit_rates:
            return None

        entries: List[Tuple[int, int, int, Dict[str, Any]]] = []
        for entry in bit_rates:
            if not isinstance(entry, dict):
                continue
            play_addr = entry.get("play_addr")
            if not isinstance(play_addr, dict):
                continue
            try:
                br = int(entry.get("bit_rate") or 0)
            except (TypeError, ValueError):
                br = 0
            short_edge, pixels = BaseDownloader._resolution_metrics(entry, play_addr)
            entries.append((br, short_edge, pixels, play_addr))
        if not entries:
            return None

        normalised = (quality or "highest").strip().lower()

        if normalised == "lowest":
            entries.sort(key=lambda t: (t[0], t[2]))
            return entries[0][3]

        if normalised in BaseDownloader._QUALITY_TARGET_WIDTH:
            target_edge = int(normalised[:-1])
            # Closest short edge to target; tie-break by higher bit_rate so we
            # don't accidentally pick a re-encoded low-bitrate copy.
            entries.sort(key=lambda t: (abs(t[1] - target_edge), -t[0], -t[2]))
            return entries[0][3]

        # Default / "highest": highest resolution, tie-break by bit rate.
        entries.sort(key=lambda t: (-t[2], -t[0], -t[1]))
        return entries[0][3]

    @staticmethod
    def _pick_preferred_play_addr(
        video: Dict[str, Any], quality: str = "highest"
    ) -> Optional[Dict[str, Any]]:
        preferred = BaseDownloader._pick_play_addr_by_quality(video, quality)
        if preferred:
            return preferred
        if not isinstance(video, dict):
            return None
        primary = video.get("play_addr")
        if isinstance(primary, dict) and primary.get("uri"):
            return primary
        for key in BaseDownloader._PLAY_ADDR_KEYS:
            candidate = video.get(key)
            if isinstance(candidate, dict) and (
                BaseDownloader._extract_first_url(candidate) or candidate.get("uri")
            ):
                return candidate
        return None

    @staticmethod
    def _has_video_source(video: Dict[str, Any]) -> bool:
        if BaseDownloader._pick_preferred_play_addr(video):
            return True
        if not isinstance(video, dict):
            return False
        return bool(
            video.get("vid")
            or (isinstance(video.get("download_addr"), dict) and video["download_addr"].get("uri"))
        )

    def _collect_image_urls(self, aweme_data: Dict[str, Any]) -> List[str]:
        return [
            candidates[0]
            for candidates in self._collect_image_url_candidates(aweme_data)
            if candidates
        ]

    def _collect_image_url_candidates(self, aweme_data: Dict[str, Any]) -> List[List[str]]:
        image_urls = []
        gallery_items = self._iter_gallery_items(aweme_data)
        for item in gallery_items:
            if not isinstance(item, dict):
                continue
            candidates = self._collect_ranked_media_urls(
                (item.get("watermark_free_download_url_list"), item, 0),
                (item.get("origin_image"), item.get("origin_image"), 1),
                (item.get("display_image"), item.get("display_image"), 2),
                (item, item, 3),
                (item.get("download_url"), item.get("download_url"), 4),
                (item.get("download_addr"), item.get("download_addr"), 5),
                (item.get("download_url_list"), item, 6),
                (item.get("owner_watermark_image"), item.get("owner_watermark_image"), 7),
            )
            if candidates:
                image_urls.append(candidates)
        if not image_urls:
            logger.warning(
                "No image URLs extracted for aweme %s; gallery items count=%d",
                aweme_data.get("aweme_id"),
                len(gallery_items),
            )
        return image_urls

    def _collect_image_live_urls(self, aweme_data: Dict[str, Any]) -> List[str]:
        live_urls: List[str] = []
        quality = str(self.config.get("video_quality") or "highest")
        for item in self._iter_gallery_items(aweme_data):
            if not isinstance(item, dict):
                continue
            video = item.get("video") if isinstance(item.get("video"), dict) else {}
            # 实况图同样会有 bit_rate 多档，按配置的画质偏好选择对应档位。
            preferred_play_addr = self._pick_preferred_play_addr(video, quality)
            live_url = self._pick_first_media_url(
                preferred_play_addr,
                video.get("play_addr_h264"),
                video.get("play_addr_265"),
                video.get("play_addr_256"),
                video.get("play_addr"),
                video.get("download_addr"),
                item.get("video_play_addr"),
                item.get("video_download_addr"),
            )
            if live_url:
                live_urls.append(live_url)
        return self._deduplicate_urls(live_urls)

    @staticmethod
    def _iter_gallery_items(aweme_data: Dict[str, Any]) -> List[Any]:
        image_post = aweme_data.get("image_post_info")
        if isinstance(image_post, dict):
            for key in ("images", "image_list"):
                candidate = image_post.get(key)
                if isinstance(candidate, list) and candidate:
                    return candidate
        images = aweme_data.get("images") or aweme_data.get("image_list") or []
        if isinstance(images, list):
            return images
        return []

    @staticmethod
    def _deduplicate_urls(urls: List[str]) -> List[str]:
        deduped: List[str] = []
        seen: set[str] = set()
        for url in urls:
            if not url or url in seen:
                continue
            seen.add(url)
            deduped.append(url)
        return deduped

    @staticmethod
    def _pick_first_media_url(*sources: Any) -> Optional[str]:
        for source in sources:
            candidate = BaseDownloader._extract_first_url(source)
            if candidate:
                return candidate
        return None

    @staticmethod
    def _collect_ranked_media_urls(*sources: Tuple[Any, Any, int]) -> List[str]:
        entries: List[Tuple[Tuple[int, int, int, int], str]] = []
        for source, metadata, source_rank in sources:
            for candidate in BaseDownloader._extract_urls(source):
                entries.append(
                    (
                        BaseDownloader._gallery_image_sort_key(candidate, metadata, source_rank),
                        candidate,
                    )
                )

        urls: List[str] = []
        seen: set[str] = set()
        for _key, candidate in sorted(entries, key=lambda entry: entry[0]):
            if candidate in seen:
                continue
            seen.add(candidate)
            urls.append(candidate)
        return urls

    @staticmethod
    def _gallery_image_sort_key(
        url: str, metadata: Any, source_rank: int
    ) -> Tuple[int, int, int, int]:
        watermark_rank = (
            1 if source_rank >= 4 or BaseDownloader._is_watermarked_media_url(url) else 0
        )
        pixels = BaseDownloader._image_resolution_score(metadata)
        return (watermark_rank, -pixels, source_rank, BaseDownloader._image_format_rank(url))

    @staticmethod
    def _image_resolution_score(source: Any) -> int:
        if not isinstance(source, dict):
            return 0

        width = BaseDownloader._positive_int(source.get("width") or source.get("w"))
        height = BaseDownloader._positive_int(source.get("height") or source.get("h"))
        if width and height:
            return width * height
        # Some response variants expose only one side; use it as a weak hint
        # after exact pixel-area ranking, then fall back to source preference.
        return width or height or 0

    @staticmethod
    def _positive_int(value: Any) -> int:
        try:
            number = int(float(str(value).strip()))
        except (TypeError, ValueError):
            return 0
        return number if number > 0 else 0

    @staticmethod
    def _image_format_rank(url: str) -> int:
        path = (urlparse(url).path or "").lower()
        return 1 if ".webp" in path else 0

    @staticmethod
    def _is_watermarked_media_url(url: str) -> bool:
        normalized = url.lower()
        watermark_hints = (
            "tplv-dy-water",
            "dy-water",
            "owner_watermark",
            "watermark_image",
            "watermark=1",
            "playwm",
        )
        return any(hint in normalized for hint in watermark_hints)

    @staticmethod
    def _extract_first_url(source: Any) -> Optional[str]:
        urls = BaseDownloader._extract_urls(source)
        return urls[0] if urls else None

    @staticmethod
    def _extract_urls(source: Any) -> List[str]:
        if isinstance(source, dict):
            url_list = source.get("url_list") or source.get("urlList")
            if isinstance(url_list, list) and url_list:
                return [item for item in url_list if isinstance(item, str) and item]
        elif isinstance(source, list) and source:
            return [item for item in source if isinstance(item, str) and item]
        elif isinstance(source, str) and source:
            return [source]
        return []

    @staticmethod
    def _infer_image_extension(image_url: str) -> str:
        allowed_exts = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
        if not image_url:
            return ".jpg"

        image_path = (urlparse(image_url).path or "").lower()
        raw_suffix = Path(image_path).suffix.lower()
        if raw_suffix in allowed_exts:
            return raw_suffix

        matches = re.findall(r"\.(?:jpe?g|png|webp|gif)(?=[^a-z0-9]|$)", image_path)
        if matches:
            return matches[-1].lower()

        return ".jpg"

    @staticmethod
    def _resolve_publish_time(create_time: Any) -> Tuple[Optional[int], str]:
        if create_time in (None, ""):
            return None, ""

        try:
            publish_ts = int(create_time)
            if publish_ts <= 0:
                return None, ""
            return publish_ts, datetime.fromtimestamp(publish_ts).strftime("%Y-%m-%d")
        except (TypeError, ValueError, OSError, OverflowError):
            return None, ""

    @staticmethod
    def _extract_tags(aweme_data: Dict[str, Any]) -> List[str]:
        tags: List[str] = []

        def _append_tag(raw_tag: Any):
            if not raw_tag:
                return
            normalized_tag = str(raw_tag).strip().lstrip("#")
            if normalized_tag and normalized_tag not in tags:
                tags.append(normalized_tag)

        for item in aweme_data.get("text_extra") or []:
            if not isinstance(item, dict):
                continue
            _append_tag(item.get("hashtag_name"))
            _append_tag(item.get("tag_name"))

        for item in aweme_data.get("cha_list") or []:
            if not isinstance(item, dict):
                continue
            _append_tag(item.get("cha_name"))
            _append_tag(item.get("name"))

        desc = aweme_data.get("desc") or ""
        for hashtag in re.findall(r"#([^\s#]+)", desc):
            _append_tag(hashtag)

        return tags

    def _to_manifest_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.file_manager.base_path))
        except ValueError:
            return str(path)
