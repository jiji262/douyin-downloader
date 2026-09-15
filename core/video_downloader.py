from typing import Any, Dict

from core.downloader_base import BaseDownloader, DownloadResult
from utils.logger import setup_logger

logger = setup_logger("VideoDownloader")

# get_video_detail 在「请求被拒 / 空响应」与「作品已删除 / 不可见」时都只回 None，
# 这里分不清是哪一种，所以两种可能都写上。
DETAIL_UNAVAILABLE_REASON = (
    "抖音未返回作品详情（可能触发了风控验证，或作品已删除 / 不可见），"
    "请确认作品能在抖音打开后重试，或重新登录"
)


class VideoDownloader(BaseDownloader):
    async def download(self, parsed_url: Dict[str, Any]) -> DownloadResult:
        result = DownloadResult()

        aweme_id = parsed_url.get("aweme_id")
        if not aweme_id:
            logger.error("No aweme_id found in parsed URL")
            return result

        result.total = 1
        self._progress_set_item_total(1, "单作品下载")
        self._progress_update_step("下载作品", "单作品资源下载中")

        should_download = await self._should_download(aweme_id)
        if not should_download and not self._comments_config():
            logger.info("Video %s already downloaded, skipping", aweme_id)
            result.skipped += 1
            self._progress_advance_item("skipped", str(aweme_id))
            return result

        await self.rate_limiter.acquire()

        aweme_data = await self.api_client.get_video_detail(aweme_id)
        if not aweme_data:
            logger.error("Failed to get video detail: %s", aweme_id)
            result.failed += 1
            result.failure_reason = DETAIL_UNAVAILABLE_REASON
            self._progress_advance_item("failed", str(aweme_id))
            return result

        if not should_download:
            if await self._collect_comments_for_existing_video(aweme_data):
                logger.info("Collected comments for already-downloaded video %s", aweme_id)
                result.success += 1
                self._progress_advance_item("success", str(aweme_id))
            else:
                logger.info("Video %s already downloaded, skipping", aweme_id)
                result.skipped += 1
                self._progress_advance_item("skipped", str(aweme_id))
            return result

        success = await self._download_aweme(aweme_data)
        if success:
            result.success += 1
            self._progress_advance_item("success", str(aweme_id))
        else:
            result.failed += 1
            self._progress_advance_item("failed", str(aweme_id))

        return result

    async def _collect_comments_for_existing_video(self, aweme_data: Dict[str, Any]) -> bool:
        author = aweme_data.get("author", {}) or {}
        author_name = author.get("nickname", "unknown")
        return await self._collect_comments_for_existing_aweme(aweme_data, author_name)

    async def _download_aweme(self, aweme_data: Dict[str, Any]) -> bool:
        author = aweme_data.get("author", {}) or {}
        author_name = author.get("nickname", "unknown")
        # Cache author on the hosting job so JobRow can display the nickname
        # and `retry_failed_awemes` doesn't need to re-fetch user info.
        self._progress_report_author(
            nickname=author_name if author_name != "unknown" else None,
            sec_uid=author.get("sec_uid"),
        )
        return await self._download_aweme_assets(aweme_data, author_name)
