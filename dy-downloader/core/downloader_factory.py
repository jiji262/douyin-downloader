from typing import Dict, Any, Optional
from core.downloader_base import BaseDownloader
from core.video_downloader import VideoDownloader
from core.user_downloader import UserDownloader
from config import ConfigLoader
from storage import Database, FileManager
from auth import CookieManager
from control import QueueManager, RateLimiter, RetryHandler
from core.api_client import DouyinAPIClient
from utils.logger import setup_logger

logger = setup_logger('DownloaderFactory')


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
    ) -> Optional[BaseDownloader]:

        common_args = {
            'config': config,
            'api_client': api_client,
            'file_manager': file_manager,
            'cookie_manager': cookie_manager,
            'database': database,
            'rate_limiter': rate_limiter,
            'retry_handler': retry_handler,
            'queue_manager': queue_manager,
        }

        if url_type == 'video':
            return VideoDownloader(**common_args)
        elif url_type == 'user':
            return UserDownloader(**common_args)
        elif url_type == 'gallery':
            return VideoDownloader(**common_args)
        else:
            logger.error(f"Unsupported URL type: {url_type}")
            return None
