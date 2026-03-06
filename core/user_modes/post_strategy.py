from __future__ import annotations

from typing import Any, Dict, List

from core.user_modes.base_strategy import BaseUserModeStrategy
from utils.logger import setup_logger

logger = setup_logger("PostUserModeStrategy")


class PostUserModeStrategy(BaseUserModeStrategy):
    mode_name = "post"
    api_method_name = "get_user_post"

    async def collect_items(
        self, sec_uid: str, user_info: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        fetcher = getattr(self.downloader.api_client, self.api_method_name, None)
        if not callable(fetcher):
            logger.error("API client missing get_user_post")
            return []

        aweme_list: List[Dict[str, Any]] = []
        max_cursor = 0
        has_more = True
        pagination_restricted = False

        number_limit = int(
            self.downloader.config.get("number", {}).get(self.mode_name, 0) or 0
        )
        increase_enabled = bool(
            self.downloader.config.get("increase", {}).get(self.mode_name, False)
        )
        latest_time = None
        if increase_enabled and self.downloader.database:
            latest_time = await self.downloader.database.get_latest_aweme_time(
                user_info.get("uid")
            )

        self.downloader._progress_update_step("拉取作品列表", "分页抓取中")

        while has_more:
            await self.downloader.rate_limiter.acquire()
            request_cursor = max_cursor
            page_data = await fetcher(sec_uid, request_cursor, 20)
            page = self._normalize_page_data(page_data)
            page_items = self.select_items(page)

            if not page_items:
                if request_cursor and page.get("status_code") == 0:
                    pagination_restricted = True
                    logger.warning(
                        "User post pagination likely blocked at cursor=%s, switching to browser fallback",
                        request_cursor,
                    )
                break

            if increase_enabled and latest_time:
                new_items = [
                    a for a in page_items if a.get("create_time", 0) > latest_time
                ]
                aweme_list.extend(new_items)
                if len(new_items) < len(page_items):
                    break
            else:
                aweme_list.extend(page_items)

            self.downloader._progress_update_step(
                "拉取作品列表", f"已抓取 {len(aweme_list)} 条"
            )

            has_more = bool(page.get("has_more", False))
            max_cursor = int(page.get("max_cursor", 0) or 0)
            if has_more and max_cursor == request_cursor:
                logger.warning(
                    "max_cursor did not advance (%s), stop paging to avoid loop",
                    max_cursor,
                )
                pagination_restricted = True
                break

            if number_limit > 0 and len(aweme_list) >= number_limit:
                aweme_list = aweme_list[:number_limit]
                break

        if pagination_restricted:
            self.downloader._progress_update_step("拉取作品列表", "分页受限，尝试浏览器回补")
            await self.downloader._recover_user_post_with_browser(
                sec_uid, user_info, aweme_list
            )

        return aweme_list
