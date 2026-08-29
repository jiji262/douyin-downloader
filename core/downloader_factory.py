from typing import Any, Optional

from auth import CookieManager
from config import ConfigLoader
from control import QueueManager, RateLimiter, RetryHandler
from core.api_client import DouyinAPIClient
from core.downloader_base import BaseDownloader
from core.live_downloader import LiveDownloader
from core.live_replay_downloader import LiveReplayDownloader
from core.mix_downloader import MixDownloader
from core.music_downloader import MusicDownloader
from core.user_downloader import UserDownloader
from core.video_downloader import VideoDownloader
from storage import Database, FileManager
from utils.logger import setup_logger

logger = setup_logger("DownloaderFactory")

# 已识别但永远不会有下载器的 url_type -> 给用户看的原因。
# 与 TikTok 的 "暂不支持（将于后续版本支持）" 不同：这里是能力上不可能，
# 不是排期问题，文案必须说清楚，否则用户会一直等一个不会来的版本。
UNSUPPORTED_URL_TYPE_DETAIL = {
    "lvdetail": "抖音放映厅影视内容不支持下载：版权影视采用 DRM 加密，无法获取可播放的成片",
}


class DownloaderFactory:
    @staticmethod
    def create(
        url_type: str,
        config: ConfigLoader,
        api_client: DouyinAPIClient,
        file_manager: FileManager,
        cookie_manager: CookieManager,
        database: Optional[Database] = None,
        rate_limiter: Optional[RateLimiter] = None,
        retry_handler: Optional[RetryHandler] = None,
        queue_manager: Optional[QueueManager] = None,
        progress_reporter: Optional[Any] = None,
        job_id: Optional[str] = None,
    ) -> Optional[BaseDownloader]:

        common_args = {
            "config": config,
            "api_client": api_client,
            "file_manager": file_manager,
            "cookie_manager": cookie_manager,
            "database": database,
            "rate_limiter": rate_limiter,
            "retry_handler": retry_handler,
            "queue_manager": queue_manager,
            "progress_reporter": progress_reporter,
            "job_id": job_id,
        }

        if url_type == "video":
            return VideoDownloader(**common_args)
        elif url_type == "user":
            return UserDownloader(**common_args)
        elif url_type == "gallery":
            return VideoDownloader(**common_args)
        elif url_type == "collection":
            return MixDownloader(**common_args)
        elif url_type == "music":
            return MusicDownloader(**common_args)
        elif url_type == "live":
            return LiveDownloader(**common_args)
        elif url_type == "live_replay":
            return LiveReplayDownloader(**common_args)
        elif url_type == "short":
            logger.error(
                "Short URL was not resolved before dispatching. "
                "Please call api_client.resolve_short_url() first."
            )
            return None
        elif url_type in UNSUPPORTED_URL_TYPE_DETAIL:
            logger.error(
                "Capability-gated URL type: %s (%s)",
                url_type,
                UNSUPPORTED_URL_TYPE_DETAIL[url_type],
            )
            return None
        else:
            logger.error("Unsupported URL type: %s", url_type)
            return None
