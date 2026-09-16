from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Tuple

from core.user_modes.base_strategy import (
    BaseUserModeStrategy,
    PageRequestFailedError,
    fetch_page_folding_bridge_failure,
    page_failure_advice,
)
from core.user_modes.post_time_boundary import PostTimeBoundary, TimeBoundaryDecision
from utils.logger import setup_logger

logger = setup_logger("PostUserModeStrategy")

_POST_PAGE_TIMEOUT_SECONDS = 45.0
_POST_PAGE_SIZE = 20
# 单页放弃前的最大请求次数（含首次）。api_client 内部已按 [1,2,5]s 重试
# 3 次，这里是整页级别的外层重试：抖音边缘 WAF 对 /aweme/post/ 回 403
# 时内层重试会全部落空并返回 {}，归一化成「空页」后旧逻辑直接截断主页。
_POST_PAGE_MAX_ATTEMPTS = 3
# 第 2、3 次尝试前的退避秒数（下标即已完成的尝试数 - 1）。
_POST_PAGE_RETRY_BACKOFF_SECONDS = (2.0, 5.0)
# 单页重试的墙钟预算：只拦「要不要再发起一次尝试」，拦不住已经在飞的那次。
# 超时页每次挂满 45s，所以一页最坏 = 60s 内起第 2 次 + 45s 超时 ≈ 90s，比不设
# 闸门的 3 次 135s 短一截；它不是 60s 的硬上限，别照字面理解。
_POST_PAGE_RETRY_BUDGET_SECONDS = 60.0
_PostPageResult = Tuple[List[Dict[str, Any]], bool]
# 一条作品都没拿到、又说不出更具体原因时的兜底文案。
_EMPTY_POST_LIST_MESSAGE = (
    "抖音接口未返回作品列表（可能触发了反爬限制），请稍后重试或尝试重新登录抖音刷新 Cookie"
)


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
    # 由 ``UserDownloader._download_mode_logged`` 在本轮结束后读走，写进
    # ``DownloadResult``；每次 collect_items 开头重置（策略实例会被缓存）。
    incomplete_reason: Optional[str] = None
    pinned_excluded = 0
    # 本轮走完的原始条目数，以及「确凿的截断证据」（请求失败 / 超时）。
    # aweme_count 之类的启发式一律不写这里——它不足以把任务判成不完整。
    _raw_items_seen = 0
    _hard_truncation: Optional[str] = None
    # 失败页说得出具体原因(被拒绝 / 页面通道错误码)时的完整文案,否则 None。
    _failure_message: Optional[str] = None

    async def collect_items(self, sec_uid: str, user_info: Dict[str, Any]) -> List[Dict[str, Any]]:
        self.incomplete_reason = None
        fetcher = getattr(self.downloader.api_client, self.api_method_name, None)
        if not callable(fetcher):
            logger.error("API client missing get_user_post")
            return []

        aweme_list, pagination_restricted = await self._collect_api_items(sec_uid, user_info)
        if pagination_restricted:
            recovered = await self._recover_with_browser(sec_uid, user_info, aweme_list)
            if not aweme_list:
                # PageRequestFailedError 让 UserDownloader 接着跑剩下的模式。
                raise PageRequestFailedError(self._failure_message or _EMPTY_POST_LIST_MESSAGE)
            if self._hard_truncation and not recovered:
                self.incomplete_reason = self._hard_truncation
                logger.warning("User post walk incomplete: %s", self.incomplete_reason)
        self._report_walk_summary(aweme_list, user_info)
        return aweme_list

    async def _recover_with_browser(
        self,
        sec_uid: str,
        user_info: Dict[str, Any],
        aweme_list: List[Dict[str, Any]],
    ) -> bool:
        """调用浏览器回补，返回是否真的补进了新条目。

        发行版构建用 ``--exclude-module=playwright`` 裁掉了浏览器组件，
        回补必然空转；不可用时不再打「尝试浏览器回补」这句会误导用户的
        提示，honest 的原因由 ``_recover_user_post_with_browser`` 负责说。
        """
        before = len(aweme_list)
        if self._browser_recovery_available():
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
        return len(aweme_list) > before

    def _browser_recovery_available(self) -> bool:
        checker = getattr(self.downloader, "_browser_recovery_unavailable_reason", None)
        return checker() is None if callable(checker) else True

    def _report_walk_summary(
        self,
        aweme_list: List[Dict[str, Any]],
        user_info: Dict[str, Any],
    ) -> None:
        """收尾时把「抓了多少 / 排除了多少 / 主页标称多少」摊开说明。

        用户看到的「主页 164 条、只下了 161 条」多半来自置顶排除，缺了
        这一行，差额无处解释。
        """
        parts = [f"抓取完成，共 {self._raw_items_seen} 条"]
        if self.pinned_excluded:
            parts.append(f"已排除作者置顶 {self.pinned_excluded} 条，待下载 {len(aweme_list)} 条")
        profile_count = self._profile_aweme_count(user_info)
        if profile_count > self._raw_items_seen:
            parts.append(f"主页标称 {profile_count} 条")
        if self.incomplete_reason:
            parts.append(self.incomplete_reason)
        self.downloader._progress_update_step("拉取作品列表", "，".join(parts))
        logger.info(
            "User post walk summary: fetched=%s raw_items=%s pinned_excluded=%s "
            "profile_count=%s incomplete=%s",
            len(aweme_list),
            self._raw_items_seen,
            self.pinned_excluded,
            profile_count,
            self.incomplete_reason or "-",
        )

    async def _collect_api_items(self, sec_uid: str, user_info: Dict[str, Any]) -> _PostPageResult:
        aweme_list: List[Dict[str, Any]] = []
        max_cursor = raw_items_seen = page_number = candidate_count = 0
        # 策略实例会被 UserDownloader 缓存复用，走查状态必须每轮清零。
        self.pinned_excluded = 0
        self._raw_items_seen = 0
        self._hard_truncation = None
        self._failure_message = None
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
                self._hard_truncation = self._truncation_reason(
                    page_number, raw_items_seen, "请求超时"
                )
                return aweme_list, True
            page = self._normalize_page_data(page_data)
            page_items = self.select_items(page)
            raw_page_count = self._append_page_items(page, aweme_list)
            if raw_page_count == 0:
                # 空页本身是模糊信号（抖音会在最后一页仍报 has_more=1），一律
                # 尝试浏览器回补；只有确凿证据才把这一轮标成「不完整」。
                cause = self._empty_page_failure_cause(page)
                if cause:
                    advice = page_failure_advice(page)
                    self._hard_truncation = self._truncation_reason(
                        page_number, raw_items_seen, cause, advice
                    )
                    if advice:
                        self._failure_message = f"作品列表 第 {page_number} 页{cause}：{advice}"
                self._log_empty_page(request_cursor, cause)
                return aweme_list, True
            raw_items_seen += raw_page_count
            self._raw_items_seen = raw_items_seen
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
        kept = self._filter_pinned_items(page_items)
        # download_pinned=False 时置顶作品被静默丢掉，用户只看到「164 变
        # 161」；这里累计差额，收尾时一并汇报。
        self.pinned_excluded += raw_page_count - len(kept)
        aweme_list.extend(kept)
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
        """取一页，瞬时失败（超时 / 请求失败）时有限次退避重试。

        单页失败在旧逻辑里等价于「列表到头了」，一次边缘 WAF 403 就能把
        整个主页截断且任务仍报成功。重试次数与总时长都设了上限，避免把
        进度条挂到用户以为卡死。
        """
        page_started = time.monotonic()
        page_data: Optional[Dict[str, Any]] = None
        for attempt in range(1, _POST_PAGE_MAX_ATTEMPTS + 1):
            page_data = await self._attempt_post_page(
                sec_uid,
                request_cursor,
                page_number=page_number,
                collected_count=collected_count,
            )
            if not self._page_needs_retry(page_data):
                return page_data
            if not self._may_retry_page(attempt, page_started):
                break
            self.downloader._progress_update_step(
                "拉取作品列表",
                f"第 {page_number} 页请求失败，正在重试"
                f"（第 {attempt + 1}/{_POST_PAGE_MAX_ATTEMPTS} 次）",
            )
            logger.warning(
                "User post page retry: page=%s request_cursor=%s attempt=%s timed_out=%s",
                page_number,
                request_cursor,
                attempt,
                page_data is None,
            )
            # 超时页已经自带 45s 的等待，不再叠加退避；快速失败（WAF 403
            # 打回 {}）才需要等一等，让限流窗口过去。
            if page_data is not None:
                await asyncio.sleep(self._retry_backoff_seconds(attempt))
        return page_data

    @staticmethod
    def _retry_backoff_seconds(attempt: int) -> float:
        """退避表比尝试次数短时取最后一档，别让下标越界（对齐 api_client）。"""
        return _POST_PAGE_RETRY_BACKOFF_SECONDS[
            min(attempt - 1, len(_POST_PAGE_RETRY_BACKOFF_SECONDS) - 1)
        ]

    @staticmethod
    def _may_retry_page(attempt: int, page_started: float) -> bool:
        """次数与墙钟预算都还有余量时才允许再发一次（预算按尝试开始时刻判定）。"""
        if attempt >= _POST_PAGE_MAX_ATTEMPTS:
            return False
        return time.monotonic() - page_started < _POST_PAGE_RETRY_BUDGET_SECONDS

    def _page_needs_retry(self, page_data: Optional[Dict[str, Any]]) -> bool:
        """None = 超时；其余带失败证据的空页（请求失败 / 接口报错 / 列表缺失）
        都可能只是瞬时限流，值得再试一次。被抖音确定性拒绝的例外：api_client
        已经不重试它，这里再整页重发只会更快撞上验证码。"""
        if page_data is None:
            return True
        page = self._normalize_page_data(page_data)
        if self._page_rejected(page):
            return False
        return self._empty_page_failure_cause(page) is not None

    async def _attempt_post_page(
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
                f"第 {page_number} 页请求超时",
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
                fetch_page_folding_bridge_failure(
                    self.downloader.api_client.get_user_post,
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
    def _log_empty_page(request_cursor: int, cause: Optional[str]) -> None:
        logger.warning(
            "User post page empty at cursor=%s (cause=%s); will attempt browser fallback",
            request_cursor,
            cause or "-",
        )

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
    def _truncation_reason(
        page_number: int, raw_items_seen: int, cause: str, advice: Optional[str] = None
    ) -> str:
        if advice:
            return (
                f"第 {page_number} 页{cause}：{advice}；仅取到 {raw_items_seen} 条，作品列表不完整"
            )
        return (
            f"第 {page_number} 页{cause}（可能被限流），"
            f"仅取到 {raw_items_seen} 条，作品列表不完整，请稍后重试"
        )

    @staticmethod
    def _profile_aweme_count(user_info: Dict[str, Any]) -> int:
        try:
            return int(user_info.get("aweme_count") or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _profile_reports_more(user_info: Dict[str, Any], raw_items_seen: int) -> bool:
        return PostUserModeStrategy._profile_aweme_count(user_info) > raw_items_seen
