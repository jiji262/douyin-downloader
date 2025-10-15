from typing import Any, Dict

from core.downloader_base import BaseDownloader, DownloadResult
from utils.logger import setup_logger

logger = setup_logger('VideoDownloader')


class VideoDownloader(BaseDownloader):
    async def download(self, parsed_url: Dict[str, Any]) -> DownloadResult:
        result = DownloadResult()

        aweme_id = parsed_url.get('aweme_id')
        if not aweme_id:
            logger.error("No aweme_id found in parsed URL")
            return result

        result.total = 1

        if not await self._should_download(aweme_id):
            logger.info(f"Video {aweme_id} already downloaded, skipping")
            result.skipped += 1
            return result

        await self.rate_limiter.acquire()

        aweme_data = await self.api_client.get_video_detail(aweme_id)
        if not aweme_data:
            logger.error(f"Failed to get video detail: {aweme_id}")
            result.failed += 1
            return result

        success = await self._download_aweme(aweme_data)
        if success:
            result.success += 1
        else:
            result.failed += 1

        return result

    async def _download_aweme(self, aweme_data: Dict[str, Any]) -> bool:
        author = aweme_data.get('author', {})
        author_name = author.get('nickname', 'unknown')
        return await self._download_aweme_assets(aweme_data, author_name)
