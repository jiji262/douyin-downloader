from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Tuple

from core.user_modes.base_strategy import BaseUserModeStrategy
from core.user_modes.post_time_boundary import PostTimeBoundary, TimeBoundaryDecision
from utils.logger import setup_logger

logger = setup_logger("PostUserModeStrategy")

_POST_PAGE_TIMEOUT_SECONDS = 45.0
_POST_PAGE_SIZE = 20
_PostPageResult = Tuple[List[Dict[str, Any]], bool]


def _log_page_response(
    page_data: Dict[str, Any],
    *,
    page_number: int,
    request_cursor: int,
    started: float,
) -> None:
    page_items = page_data.get("items") or page_data.get("aweme_list") or []
    risk_flags = page_data.get("risk_flags")
    logger.info(
        "User post page response: page=%s request_cursor=%s duration_ms=%s "
        "item_count=%s status_code=%s has_more=%s next_cursor=%s source=%s "
        "risk_flags=%s",
        page_number,
        request_cursor,
        int((time.monotonic() - started) * 1000),
        len(page_items) if isinstance(page_items, list) else 0,
        page_data.get("status_code"),
        page_data.get("has_more"),
        page_data.get("max_cursor"),
        page_data.get("source", "unknown"),
        risk_flags if isinstance(risk_flags, dict) else {},
    )


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
        max_cursor = raw_items_seen = page_number = candidate_count = 0
        number_limit = int(self.downloader.config.get("number", {}).get(self.mode_name, 0) or 0)
        time_boundary = self._time_boundary_for_config()
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
            page_items = self.select_items(page)
            raw_page_count = self._append_page_items(page, aweme_list)
            if raw_page_count == 0:
                return aweme_list, self._empty_page_is_restricted(page, request_cursor)
            raw_items_seen += raw_page_count
            candidate_count += self._count_page_candidates(page_items)
            has_more = bool(page.get("has_more", False))
            max_cursor = int(page.get("max_cursor", 0) or 0)
            time_decision = self._observe_time_boundary(
                time_boundary,
                page_items,
                page_number,
            )
            limit_reached = number_limit > 0 and candidate_count >= number_limit
            should_stop, pagination_restricted = self._page_stop_decision(
                has_more=has_more,
                next_cursor=max_cursor,
                request_cursor=request_cursor,
                limit_reached=limit_reached,
                time_boundary_confirmed=time_decision.should_stop,
                time_boundary_reached=time_decision.boundary_reached,
                raw_page_count=raw_page_count,
                raw_items_seen=raw_items_seen,
                user_info=user_info,
            )
            if should_stop:
                if time_decision.should_stop and not pagination_restricted:
                    self._report_time_boundary_stop(page_number, raw_items_seen)
                return aweme_list, pagination_restricted

    def _time_boundary_for_config(self) -> PostTimeBoundary:
        bounds_getter = getattr(self.downloader, "_time_range_bounds", None)
        start_ts = bounds_getter()[0] if callable(bounds_getter) else None
        return PostTimeBoundary(start_ts)

    def _observe_time_boundary(
        self,
        boundary: PostTimeBoundary,
        page_items: List[Dict[str, Any]],
        page_number: int,
    ) -> TimeBoundaryDecision:
        decision = boundary.observe_page(
            page_items,
            is_pinned=getattr(self.downloader, "_is_pinned_aweme", None),
        )
        if decision.degraded_reason:
            logger.warning(
                "Post time early-stop disabled: page=%s reason=%s",
                page_number,
                decision.degraded_reason,
            )
        return decision

    def _count_page_candidates(self, items: List[Dict[str, Any]]) -> int:
        filtered = self._filter_pinned_items(items)
        filtered = self.downloader._filter_by_time(filtered)
        return len(self._filter_by_media_type(filtered))

    def _report_time_boundary_stop(self, page_number: int, raw_items_seen: int) -> None:
        detail = f"已到达起始日期，提前结束翻页（检查 {page_number} 页，共 {raw_items_seen} 条）"
        self.downloader._progress_update_step("拉取作品列表", detail)
        logger.info(
            "User post pagination stopped at time boundary: pages=%s raw_items=%s",
            page_number,
            raw_items_seen,
        )

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
        time_boundary_confirmed: bool,
        time_boundary_reached: bool,
        raw_page_count: int,
        raw_items_seen: int,
        user_info: Dict[str, Any],
    ) -> Tuple[bool, bool]:
        if self._cursor_stalled(has_more, next_cursor, request_cursor):
            return True, True
        if time_boundary_confirmed:
            return True, False
        if has_more:
            return limit_reached, False
        if time_boundary_reached:
            return True, False

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
        started = time.monotonic()
        await self.downloader.rate_limiter.acquire()
        self.downloader._progress_update_step(
            "拉取作品列表",
            f"请求第 {page_number} 页，已抓取 {collected_count} 条",
        )
        logger.info(
            "User post page request: page=%s request_cursor=%s collected=%s page_size=%s",
            page_number,
            request_cursor,
            collected_count,
            _POST_PAGE_SIZE,
        )
        page_data = await self._fetch_post_page(sec_uid, request_cursor)
        if page_data is None:
            self.downloader._progress_update_step(
                "拉取作品列表",
                f"第 {page_number} 页请求超时，准备浏览器回补",
            )
            logger.warning(
                "User post page response missing: page=%s request_cursor=%s duration_ms=%s",
                page_number,
                request_cursor,
                int((time.monotonic() - started) * 1000),
            )
            return None

        _log_page_response(
            page_data,
            page_number=page_number,
            request_cursor=request_cursor,
            started=started,
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

    @staticmethod
    def _profile_reports_more(user_info: Dict[str, Any], raw_items_seen: int) -> bool:
        try:
            profile_count = int(user_info.get("aweme_count") or 0)
        except (TypeError, ValueError):
            return False
        return profile_count > raw_items_seen
