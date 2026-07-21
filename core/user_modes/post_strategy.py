from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

from core.user_modes.base_strategy import BaseUserModeStrategy
from utils.logger import setup_logger

logger = setup_logger("PostUserModeStrategy")

_POST_PAGE_TIMEOUT_SECONDS = 45.0
_POST_PAGE_SIZE = 20
_PostPageResult = Tuple[List[Dict[str, Any]], bool]


class PostUserModeStrategy(BaseUserModeStrategy):
    mode_name = "post"
    api_method_name = "get_user_post"

    async def collect_items(self, sec_uid: str, user_info: Dict[str, Any]) -> List[Dict[str, Any]]:
        fetcher = getattr(self.downloader.api_client, self.api_method_name, None)
        if not callable(fetcher):
            logger.error("API client missing get_user_post")
            return []

        aweme_list, pagination_restricted = await self._collect_api_items(sec_uid, user_info)
        if not pagination_restricted:
            return aweme_list

        self.downloader._progress_update_step("拉取作品列表", "分页受限，尝试浏览器回补")
        if self._media_type_filter_enabled():
            await self.downloader._recover_user_post_with_browser(
                sec_uid,
                user_info,
                aweme_list,
                item_filter=self._filter_by_media_type,
            )
        else:
            await self.downloader._recover_user_post_with_browser(sec_uid, user_info, aweme_list)
        if not aweme_list:
            raise RuntimeError(
                "抖音接口未返回作品列表（可能触发了反爬限制），"
                "请稍后重试或尝试重新登录抖音刷新 Cookie"
            )
        return aweme_list

    async def _collect_api_items(self, sec_uid: str, user_info: Dict[str, Any]) -> _PostPageResult:
        aweme_list: List[Dict[str, Any]] = []
        max_cursor = 0
        raw_items_seen = 0
        page_number = 0
        number_limit = int(self.downloader.config.get("number", {}).get(self.mode_name, 0) or 0)
        media_filter_enabled = self._media_type_filter_enabled()
        self.downloader._progress_update_step("拉取作品列表", "分页抓取中")

        while True:
            request_cursor = max_cursor
            page_number += 1
            page_data = await self._request_post_page(
                sec_uid,
                request_cursor,
                page_number=page_number,
                collected_count=len(aweme_list),
            )
            if page_data is None:
                return aweme_list, True
            page = self._normalize_page_data(page_data)
            raw_page_count = self._append_page_items(page, aweme_list)
            if raw_page_count == 0:
                return aweme_list, self._empty_page_is_restricted(page, request_cursor)
            raw_items_seen += raw_page_count
            has_more = bool(page.get("has_more", False))
            max_cursor = int(page.get("max_cursor", 0) or 0)
            limit_reached = self._number_limit_reached(
                aweme_list,
                number_limit=number_limit,
                media_filter_enabled=media_filter_enabled,
            )
            should_stop, pagination_restricted = self._page_stop_decision(
                has_more=has_more,
                next_cursor=max_cursor,
                request_cursor=request_cursor,
                limit_reached=limit_reached,
                raw_page_count=raw_page_count,
                raw_items_seen=raw_items_seen,
                user_info=user_info,
            )
            if should_stop:
                if (
                    limit_reached
                    and has_more
                    and not pagination_restricted
                    and not media_filter_enabled
                ):
                    aweme_list = aweme_list[:number_limit]
                return aweme_list, pagination_restricted

    def _append_page_items(self, page: Dict[str, Any], aweme_list: List[Dict[str, Any]]) -> int:
        page_items = self.select_items(page)
        if not page_items:
            return 0
        raw_page_count = len(page_items)
        aweme_list.extend(self._filter_pinned_items(page_items))
        self.downloader._progress_update_step("拉取作品列表", f"已抓取 {len(aweme_list)} 条")
        return raw_page_count

    def _page_stop_decision(
        self,
        *,
        has_more: bool,
        next_cursor: int,
        request_cursor: int,
        limit_reached: bool,
        raw_page_count: int,
        raw_items_seen: int,
        user_info: Dict[str, Any],
    ) -> Tuple[bool, bool]:
        if self._cursor_stalled(has_more, next_cursor, request_cursor):
            return True, True
        if has_more:
            return limit_reached, False

        ended_early = raw_page_count >= _POST_PAGE_SIZE or self._profile_reports_more(
            user_info, raw_items_seen
        )
        if ended_early and not limit_reached:
            logger.warning(
                "User post pagination may have ended early: fetched=%s, profile_count=%s",
                raw_items_seen,
                user_info.get("aweme_count"),
            )
        return True, ended_early and not limit_reached

    async def _request_post_page(
        self,
        sec_uid: str,
        request_cursor: int,
        *,
        page_number: int,
        collected_count: int,
    ) -> Optional[Dict[str, Any]]:
        await self.downloader.rate_limiter.acquire()
        self.downloader._progress_update_step(
            "拉取作品列表",
            f"请求第 {page_number} 页，已抓取 {collected_count} 条",
        )
        page_data = await self._fetch_post_page(sec_uid, request_cursor)
        if page_data is None:
            self.downloader._progress_update_step(
                "拉取作品列表",
                f"第 {page_number} 页请求超时，准备浏览器回补",
            )
        return page_data

    async def _fetch_post_page(self, sec_uid: str, request_cursor: int) -> Optional[Dict[str, Any]]:
        try:
            return await asyncio.wait_for(
                self.downloader.api_client.get_user_post(
                    sec_uid,
                    request_cursor,
                    _POST_PAGE_SIZE,
                ),
                timeout=_POST_PAGE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "User post page timed out at cursor=%s after %.0fs",
                request_cursor,
                _POST_PAGE_TIMEOUT_SECONDS,
            )
            return None

    @staticmethod
    def _empty_page_is_restricted(page: Dict[str, Any], request_cursor: int) -> bool:
        restricted = page.get("status_code") == 0
        if restricted:
            logger.warning(
                "User post page empty at cursor=%s (status_code=0); will attempt browser fallback",
                request_cursor,
            )
        return restricted

    @staticmethod
    def _cursor_stalled(has_more: bool, next_cursor: int, request_cursor: int) -> bool:
        if not has_more or next_cursor != request_cursor:
            return False
        logger.warning(
            "max_cursor did not advance (%s), stop paging to avoid loop",
            next_cursor,
        )
        return True

    def _number_limit_reached(
        self,
        items: List[Dict[str, Any]],
        *,
        number_limit: int,
        media_filter_enabled: bool,
    ) -> bool:
        if number_limit <= 0:
            return False
        if media_filter_enabled:
            return len(self._filter_by_media_type(items)) >= number_limit
        return len(items) >= number_limit

    @staticmethod
    def _profile_reports_more(user_info: Dict[str, Any], raw_items_seen: int) -> bool:
        try:
            profile_count = int(user_info.get("aweme_count") or 0)
        except (TypeError, ValueError):
            return False
        return profile_count > raw_items_seen
