import json
import re
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Tuple
from urllib.parse import urlparse

from auth import CookieManager
from config import ConfigLoader
from control import QueueManager, RateLimiter, RetryHandler
from core.api_client import DouyinAPIClient
from core.transcript_manager import TranscriptManager
from storage import Database, FileManager, MetadataHandler
from utils.logger import setup_logger
from utils.validators import sanitize_filename

logger = setup_logger("BaseDownloader")


class ProgressReporter(Protocol):
    def update_step(self, step: str, detail: str = "") -> None:
        ...

    def set_item_total(self, total: int, detail: str = "") -> None:
        ...

    def advance_item(self, status: str, detail: str = "") -> None:
        ...


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
        self.metadata_handler = MetadataHandler()
        self.transcript_manager = TranscriptManager(
            self.config, self.file_manager, self.database
        )
        self._local_aweme_ids: Optional[set[str]] = None
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

    def _log_download_error(self, log_fn, message: str) -> None:
        if self._download_error_log_count < self._download_error_log_limit:
            log_fn(message)
        elif self._download_error_log_count == self._download_error_log_limit:
            logger.error(
                "Too many download errors, suppressing further per-file logs..."
            )
        self._download_error_log_count += 1

    def _download_headers(self, user_agent: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "Referer": f"{self.api_client.BASE_URL}/",
            "Origin": self.api_client.BASE_URL,
            "Accept": "*/*",
        }

        headers["User-Agent"] = user_agent or self.api_client.headers.get(
            "User-Agent", ""
        )
        return headers

    @abstractmethod
    async def download(self, parsed_url: Dict[str, Any]) -> DownloadResult:
        pass

    async def _should_download(self, aweme_id: str) -> bool:
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
            logger.info(f"Aweme {aweme_id} already exists locally, skipping")
            return False

        return True

    def _is_locally_downloaded(self, aweme_id: str) -> bool:
        if not aweme_id:
            return False

        if self._local_aweme_ids is None:
            self._build_local_aweme_index()

        assert self._local_aweme_ids is not None
        return aweme_id in self._local_aweme_ids

    def _build_local_aweme_index(self):
        base_path = self.file_manager.base_path
        aweme_ids: set[str] = set()

        if base_path.exists():
            for path in base_path.rglob("*"):
                if not path.is_file():
                    continue
                if path.suffix.lower() not in self._local_media_suffixes:
                    continue
                try:
                    if path.stat().st_size <= 0:
                        continue
                except OSError:
                    continue
                for match in self._aweme_id_pattern.finditer(path.name):
                    aweme_ids.add(match.group(1))

        self._local_aweme_ids = aweme_ids

    def _mark_local_aweme_downloaded(self, aweme_id: str):
        if not aweme_id:
            return

        if self._local_aweme_ids is None:
            self._local_aweme_ids = set()
        self._local_aweme_ids.add(aweme_id)

    def _filter_by_time(self, aweme_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        start_time = self.config.get("start_time")
        end_time = self.config.get("end_time")

        if not start_time and not end_time:
            return aweme_list

        filtered: List[Dict[str, Any]] = []
        for aweme in aweme_list:
            create_time = aweme.get("create_time", 0)

            if start_time:
                start_ts = int(datetime.strptime(start_time, "%Y-%m-%d").timestamp())
                if create_time < start_ts:
                    continue

            if end_time:
                end_ts = int(datetime.strptime(end_time, "%Y-%m-%d").timestamp())
                if create_time > end_ts:
                    continue

            filtered.append(aweme)

        return filtered

    def _limit_count(
        self, aweme_list: List[Dict[str, Any]], mode: str
    ) -> List[Dict[str, Any]]:
        number_config = self.config.get("number", {})
        limit = number_config.get(mode, 0)

        if limit > 0:
            return aweme_list[:limit]
        return aweme_list

    async def _download_aweme_assets(
        self,
        aweme_data: Dict[str, Any],
        author_name: str,
        mode: Optional[str] = None,
    ) -> bool:
        aweme_id = aweme_data.get("aweme_id")
        if not aweme_id:
            logger.error("Missing aweme_id in aweme data")
            return False

        desc = (aweme_data.get("desc", "no_title") or "").strip() or "no_title"
        publish_ts, publish_date = self._resolve_publish_time(
            aweme_data.get("create_time")
        )
        if not publish_date:
            publish_date = datetime.now().strftime("%Y-%m-%d")
            logger.warning(
                "Aweme %s missing/invalid create_time, fallback to current date %s",
                aweme_id,
                publish_date,
            )
        file_stem = sanitize_filename(f"{publish_date}_{desc}_{aweme_id}")

        save_dir = self.file_manager.get_save_path(
            author_name=author_name,
            mode=mode,
            aweme_title=desc,
            aweme_id=aweme_id,
            folderstyle=self.config.get("folderstyle", True),
            download_date=publish_date,
        )
        downloaded_files: List[Path] = []

        session = await self.api_client.get_session()
        video_path: Optional[Path] = None

        media_type = self._detect_media_type(aweme_data)
        if media_type == "video":
            video_info = self._build_no_watermark_url(aweme_data)
            if not video_info:
                logger.error(f"No playable video URL found for aweme {aweme_id}")
                return False

            video_url, video_headers = video_info
            video_path = save_dir / f"{file_stem}.mp4"
            if not await self._download_with_retry(
                video_url, video_path, session, headers=video_headers
            ):
                return False
            downloaded_files.append(video_path)

            if self.config.get("cover"):
                cover_url = self._extract_first_url(
                    aweme_data.get("video", {}).get("cover")
                )
                if cover_url:
                    cover_path = save_dir / f"{file_stem}_cover.jpg"
                    if await self._download_with_retry(
                        cover_url,
                        cover_path,
                        session,
                        headers=self._download_headers(),
                        optional=True,
                    ):
                        downloaded_files.append(cover_path)

            if self.config.get("music"):
                music_url = self._extract_first_url(
                    aweme_data.get("music", {}).get("play_url")
                )
                if music_url:
                    music_path = save_dir / f"{file_stem}_music.mp3"
                    if await self._download_with_retry(
                        music_url,
                        music_path,
                        session,
                        headers=self._download_headers(),
                        optional=True,
                    ):
                        downloaded_files.append(music_path)

        elif media_type == "gallery":
            image_urls = self._collect_image_urls(aweme_data)
            if not image_urls:
                logger.error(f"No images found for aweme {aweme_id}")
                return False

            for index, image_url in enumerate(image_urls, start=1):
                suffix = Path(urlparse(image_url).path).suffix or ".jpg"
                image_path = save_dir / f"{file_stem}_{index}{suffix}"
                success = await self._download_with_retry(
                    image_url,
                    image_path,
                    session,
                    headers=self._download_headers(),
                )
                if not success:
                    logger.error(
                        f"Failed downloading image {index} for aweme {aweme_id}"
                    )
                    return False
                downloaded_files.append(image_path)
        else:
            logger.error(f"Unsupported media type for aweme {aweme_id}: {media_type}")
            return False

        if self.config.get("avatar"):
            author = aweme_data.get("author", {})
            avatar_url = self._extract_first_url(author.get("avatar_larger"))
            if avatar_url:
                avatar_path = save_dir / f"{file_stem}_avatar.jpg"
                if await self._download_with_retry(
                    avatar_url,
                    avatar_path,
                    session,
                    headers=self._download_headers(),
                    optional=True,
                ):
                    downloaded_files.append(avatar_path)

        if self.config.get("json"):
            json_path = save_dir / f"{file_stem}_data.json"
            if await self.metadata_handler.save_metadata(aweme_data, json_path):
                downloaded_files.append(json_path)

        author = aweme_data.get("author", {})
        if self.database:
            metadata_json = json.dumps(aweme_data, ensure_ascii=False)
            await self.database.add_aweme(
                {
                    "aweme_id": aweme_id,
                    "aweme_type": media_type,
                    "title": desc,
                    "author_id": author.get("uid"),
                    "author_name": author.get("nickname", author_name),
                    "create_time": aweme_data.get("create_time"),
                    "file_path": str(save_dir),
                    "metadata": metadata_json,
                }
            )

        manifest_record = {
            "date": publish_date,
            "aweme_id": aweme_id,
            "author_name": author.get("nickname", author_name),
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

        self._mark_local_aweme_downloaded(aweme_id)
        logger.info(f"Downloaded {media_type}: {desc} ({aweme_id})")
        return True

    async def _download_with_retry(
        self,
        url: str,
        save_path: Path,
        session,
        *,
        headers: Optional[Dict[str, str]] = None,
        optional: bool = False,
    ) -> bool:
        async def _task():
            success = await self.file_manager.download_file(
                url, save_path, session, headers=headers
            )
            if not success:
                raise RuntimeError(f"Download failed for {url}")
            return True

        try:
            await self.retry_handler.execute_with_retry(_task)
            return True
        except Exception as error:
            log_fn = logger.warning if optional else logger.error
            self._log_download_error(
                log_fn,
                f"Download error for {save_path.name}: {error}",
            )
            return False

    def _detect_media_type(self, aweme_data: Dict[str, Any]) -> str:
        if aweme_data.get("image_post_info") or aweme_data.get("images"):
            return "gallery"
        return "video"

    def _build_no_watermark_url(
        self, aweme_data: Dict[str, Any]
    ) -> Optional[Tuple[str, Dict[str, str]]]:
        video = aweme_data.get("video", {})
        play_addr = video.get("play_addr", {})
        url_candidates = [c for c in (play_addr.get("url_list") or []) if c]
        url_candidates.sort(key=lambda u: 0 if "watermark=0" in u else 1)

        fallback_candidate: Optional[Tuple[str, Dict[str, str]]] = None

        for candidate in url_candidates:
            parsed = urlparse(candidate)
            headers = self._download_headers()

            if parsed.netloc.endswith("douyin.com"):
                if "X-Bogus=" not in candidate:
                    signed_url, ua = self.api_client.sign_url(candidate)
                    headers = self._download_headers(user_agent=ua)
                    return signed_url, headers
                return candidate, headers

            fallback_candidate = (candidate, headers)

        uri = (
            play_addr.get("uri")
            or video.get("vid")
            or video.get("download_addr", {}).get("uri")
        )
        if uri:
            params = {
                "video_id": uri,
                "ratio": "1080p",
                "line": "0",
                "is_play_url": "1",
                "watermark": "0",
                "source": "PackSourceEnum_PUBLISH",
            }
            signed_url, ua = self.api_client.build_signed_path(
                "/aweme/v1/play/", params
            )
            return signed_url, self._download_headers(user_agent=ua)

        if fallback_candidate:
            return fallback_candidate

        return None

    def _collect_image_urls(self, aweme_data: Dict[str, Any]) -> List[str]:
        image_urls: List[str] = []
        image_post = aweme_data.get("image_post_info", {})
        images = image_post.get("images") or aweme_data.get("images") or []
        for item in images:
            url_list = item.get("url_list") if isinstance(item, dict) else None
            if url_list:
                image_urls.append(url_list[0])
        return image_urls

    @staticmethod
    def _extract_first_url(source: Any) -> Optional[str]:
        if isinstance(source, dict):
            url_list = source.get("url_list")
            if isinstance(url_list, list) and url_list:
                return url_list[0]
        elif isinstance(source, list) and source:
            return source[0]
        elif isinstance(source, str):
            return source
        return None

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
