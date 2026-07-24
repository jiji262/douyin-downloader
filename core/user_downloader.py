from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Set

from core.downloader_base import BaseDownloader, DownloadResult
from core.user_mode_registry import UserModeRegistry
from utils.logger import setup_logger

logger = setup_logger("UserDownloader")


def _user_info_summary(user_info: Dict[str, Any]) -> Dict[str, Any]:
    safe_keys = (
        "uid",
        "sec_uid",
        "short_id",
        "unique_id",
        "nickname",
        "aweme_count",
        "favoriting_count",
        "follower_count",
        "following_count",
        "total_favorited",
    )
    return {key: user_info.get(key) for key in safe_keys if key in user_info}


class UserDownloader(BaseDownloader):
    SELF_COLLECT_MODES = {"collect", "collectmix"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mode_registry = UserModeRegistry()
        self._mode_strategy_cache: Dict[str, Any] = {}

    async def download(self, parsed_url: Dict[str, Any]) -> DownloadResult:
        download_started = time.monotonic()
        result = DownloadResult()
        sec_uid = parsed_url.get("sec_uid")
        if not sec_uid:
            raise RuntimeError("无法从链接中解析出用户 ID，请确认链接是否完整")

        modes = self._configured_modes()
        if not self._validate_mode_scope(sec_uid, modes):
            return result

        logger.info(
            "User download started: sec_uid=%s modes=%s number=%s increase=%s",
            sec_uid,
            modes,
            self.config.get("number", {}),
            self.config.get("increase", {}),
        )
        user_info = await self._resolve_user_info_logged(sec_uid, modes)
        self._progress_report_author(
            nickname=user_info.get("nickname"),
            sec_uid=user_info.get("sec_uid") or sec_uid,
        )
        self._progress_update_step("下载模式", f"模式: {', '.join(modes)}")

        seen_aweme_ids: Set[str] = set()
        for mode in modes:
            mode_result = await self._download_mode_logged(mode, sec_uid, user_info, seen_aweme_ids)
            if mode_result is not None:
                self._merge_result(result, mode_result)

        logger.info(
            "User download finished: duration_ms=%s total=%s success=%s failed=%s skipped=%s",
            int((time.monotonic() - download_started) * 1000),
            result.total,
            result.success,
            result.failed,
            result.skipped,
        )
        return result

    def _configured_modes(self) -> List[str]:
        modes_config = self.config.get("mode", ["post"])
        if isinstance(modes_config, str):
            return [modes_config]
        if isinstance(modes_config, list):
            return [str(mode).strip() for mode in modes_config if str(mode).strip()]
        return ["post"]

    async def _resolve_user_info_logged(self, sec_uid: str, modes: List[str]) -> Dict[str, Any]:
        started = time.monotonic()
        user_info = await self._resolve_user_info(sec_uid, modes)
        if not user_info:
            logger.error(
                "Failed to get user info: sec_uid=%s duration_ms=%s",
                sec_uid,
                int((time.monotonic() - started) * 1000),
            )
            raise RuntimeError("获取用户信息失败，请检查 Cookie 是否有效或重新登录抖音")
        logger.info(
            "User info resolved: duration_ms=%s summary=%s",
            int((time.monotonic() - started) * 1000),
            _user_info_summary(user_info),
        )
        return user_info

    async def _download_mode_logged(
        self,
        mode: str,
        sec_uid: str,
        user_info: Dict[str, Any],
        seen_aweme_ids: Set[str],
    ) -> Optional[DownloadResult]:
        strategy = self._get_mode_strategy(mode)
        if strategy is None:
            logger.warning("Unsupported user mode: %s", mode)
            return None
        self._progress_update_step("下载模式", f"开始处理 {mode} 作品")
        started = time.monotonic()
        logger.info("User mode started: mode=%s strategy=%s", mode, type(strategy).__name__)
        result = await strategy.download_mode(sec_uid, user_info, seen_aweme_ids=seen_aweme_ids)
        logger.info(
            "User mode finished: mode=%s duration_ms=%s total=%s success=%s "
            "failed=%s skipped=%s unique_seen=%s",
            mode,
            int((time.monotonic() - started) * 1000),
            result.total,
            result.success,
            result.failed,
            result.skipped,
            len(seen_aweme_ids),
        )
        return result

    @staticmethod
    def _merge_result(target: DownloadResult, source: DownloadResult) -> None:
        target.total += source.total
        target.success += source.success
        target.failed += source.failed
        target.skipped += source.skipped

    def _validate_mode_scope(self, sec_uid: str, modes: List[str]) -> bool:
        normalized_modes = {str(mode or "").strip() for mode in modes}
        has_collect_mode = bool(normalized_modes & self.SELF_COLLECT_MODES)
        has_regular_mode = bool(normalized_modes - self.SELF_COLLECT_MODES)

        if has_collect_mode and sec_uid != "self":
            # Desktop "我的内容 / 下载本收藏夹" sends the real self sec_uid
            # together with a ``collects_id`` filter — by the time the
            # request reaches here the sidecar has already verified via
            # the cookie scope (``_resolve_viewer_sec_uid``) that the
            # caller is the logged-in user, so a real sec_uid + collect
            # mode + collects_id is the legit my-content path. Without
            # this branch ``download()`` would short-circuit and produce
            # an empty DownloadResult, which the JobManager renders as
            # the silent "已完成 0 项" failure.
            collects_id = (str(self.config.get("collects_id") or "")).strip()
            if not collects_id:
                logger.error(
                    "Modes collect/collectmix only support "
                    "/user/self?showTab=favorite_collection or "
                    "my-content 下载本收藏夹 (collects_id required)"
                )
                return False
        if has_collect_mode and has_regular_mode:
            logger.error("Modes collect/collectmix cannot be combined with post/like/mix/music")
            return False
        return True

    def _filter_pinned_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if self._download_pinned_enabled():
            return items
        return [item for item in items if not self._is_pinned_aweme(item)]

    def _download_pinned_enabled(self) -> bool:
        return self._as_bool(self.config.get("download_pinned", False))

    @staticmethod
    def _is_pinned_aweme(item: Dict[str, Any]) -> bool:
        value = item.get("is_top")
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    async def _resolve_user_info(self, sec_uid: str, modes: List[str]) -> Optional[Dict[str, Any]]:
        normalized_modes = {str(mode or "").strip() for mode in modes}
        if sec_uid == "self" and normalized_modes.issubset(self.SELF_COLLECT_MODES):
            self._progress_update_step("获取作者信息", "使用当前登录账号收藏夹上下文")
            return {
                "uid": "self",
                "sec_uid": "self",
                "nickname": "self",
            }

        # Desktop my-content "下载本收藏夹" path: real sec_uid + collect
        # mode + collects_id filter. The cookie scope upstream already
        # guarantees this is the viewer themselves, so we can skip the
        # network round-trip via ``api_client.get_user_info``.
        if (
            normalized_modes.issubset(self.SELF_COLLECT_MODES)
            and (str(self.config.get("collects_id") or "")).strip()
        ):
            self._progress_update_step("获取作者信息", "使用当前登录账号收藏夹上下文")
            return {
                "uid": sec_uid,
                "sec_uid": sec_uid,
                "nickname": "self",
            }

        self._progress_update_step("获取作者信息", f"sec_uid={sec_uid}")
        return await self.api_client.get_user_info(sec_uid)

    def _get_mode_strategy(self, mode: str):
        normalized_mode = (mode or "").strip()

        # The "collect" strategy supports an optional ``collects_id`` filter
        # that constrains paging to a single folder (desktop "我的收藏 / 下载
        # 本收藏夹"). When the filter is set we bypass the cache so the next
        # call with a different (or absent) filter doesn't reuse a stale
        # strategy bound to the previous folder. The no-filter path keeps
        # caching to preserve the existing CLI behaviour.
        if normalized_mode == "collect":
            return self._make_collect_strategy()

        if normalized_mode in self._mode_strategy_cache:
            return self._mode_strategy_cache[normalized_mode]

        strategy_cls = self.mode_registry.get(normalized_mode)
        if strategy_cls is None:
            return None

        strategy = strategy_cls(self)
        self._mode_strategy_cache[normalized_mode] = strategy
        return strategy

    def _make_collect_strategy(self):
        """Construct the collect strategy, threading ``collects_id`` from
        the per-job config when present. Caches only the no-filter path
        (matching the historic CLI behaviour) so a subsequent call with a
        different filter doesn't pick up a stale binding.
        """
        strategy_cls = self.mode_registry.get("collect")
        if strategy_cls is None:
            return None

        raw_filter = self.config.get("collects_id")
        collects_id = (str(raw_filter).strip() if raw_filter is not None else "") or None

        if collects_id is None:
            cached = self._mode_strategy_cache.get("collect")
            if cached is not None:
                return cached
            strategy = strategy_cls(self)
            self._mode_strategy_cache["collect"] = strategy
            return strategy

        # Filtered path is request-scoped — never cached.
        return strategy_cls(self, collects_id=collects_id)

    async def _download_mode_items(
        self,
        mode: str,
        items: List[Dict[str, Any]],
        author_name: str,
        seen_aweme_ids: Optional[Set[str]] = None,
        *,
        author_sec_uid: Optional[str] = None,
    ) -> DownloadResult:
        if seen_aweme_ids is None:
            seen_aweme_ids = set()
        deduped_items: List[Dict[str, Any]] = []
        local_seen: Set[str] = set()

        for item in items:
            aweme_id = str(item.get("aweme_id") or "").strip()
            if not aweme_id:
                continue
            if aweme_id in seen_aweme_ids or aweme_id in local_seen:
                continue
            local_seen.add(aweme_id)
            seen_aweme_ids.add(aweme_id)
            deduped_items.append(item)

        result = DownloadResult()
        result.total = len(deduped_items)
        self._progress_set_item_total(result.total, "作品待下载")
        self._progress_update_step("下载作品", f"待处理 {result.total} 条")

        # Accumulate per-aweme DB records and flush in a single transaction
        # at the end — avoids one fsync per item across the whole batch.
        db_batch: Optional[List[Dict[str, Any]]] = [] if self.database else None

        async def _process_aweme(item: Dict[str, Any]):
            aweme_id = item.get("aweme_id")
            if not await self._should_download(str(aweme_id or "")):
                saved = await self._collect_comments_for_existing_aweme(
                    item,
                    author_name,
                    mode,
                    author_sec_uid=author_sec_uid,
                )
                status = "success" if saved else "skipped"
                self._progress_advance_item(status, str(aweme_id or "unknown"))
                return {"status": status, "aweme_id": aweme_id}

            success = await self._download_aweme_assets(
                item,
                author_name,
                mode=mode,
                db_batch=db_batch,
                author_sec_uid=author_sec_uid,
            )
            status = "success" if success else "failed"
            self._progress_advance_item(status, str(aweme_id or "unknown"))
            return {
                "status": status,
                "aweme_id": aweme_id,
            }

        download_results = await self.queue_manager.download_batch(_process_aweme, deduped_items)

        if db_batch:
            await self.database.add_aweme_batch(db_batch)

        for entry in download_results:
            status = entry.get("status") if isinstance(entry, dict) else None
            if status == "success":
                result.success += 1
            elif status == "failed":
                result.failed += 1
            elif status == "skipped":
                result.skipped += 1
            else:
                result.failed += 1
                self._progress_advance_item("failed", "unknown")

        return result

    # 向后兼容：旧测试仍直接调用 post 下载入口。
    async def _download_user_post(self, sec_uid: str, user_info: Dict[str, Any]) -> DownloadResult:
        strategy = self._get_mode_strategy("post")
        if strategy is None:
            return DownloadResult()
        return await strategy.download_mode(sec_uid, user_info, seen_aweme_ids=set())

    async def _recover_user_post_with_browser(
        self,
        sec_uid: str,
        user_info: Dict[str, Any],
        aweme_list: List[Dict[str, Any]],
        *,
        item_filter: Optional[Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]] = None,
    ) -> None:
        browser_cfg = self.config.get("browser_fallback", {}) or {}
        if not browser_cfg.get("enabled", True):
            logger.info("Browser fallback skipped: disabled by config")
            return

        number_limit = self.config.get("number", {}).get("post", 0)
        # 在分页受限场景下，user_info.aweme_count 常常不可靠（经常只返回 20）
        # 媒体筛选需要先拿到详情才能判断，不能用原始 ID 数提前截断。
        expected_count = 0 if item_filter else int(number_limit or 0)
        if self._post_recovery_limit_reached(aweme_list, number_limit, item_filter):
            logger.info(
                "Browser fallback skipped: number limit reached existing=%s limit=%s",
                len(aweme_list),
                number_limit,
            )
            return

        fallback_started = time.monotonic()
        logger.info(
            "Browser fallback started: existing=%s expected_count=%s headless=%s "
            "max_scrolls=%s idle_rounds=%s timeout_s=%s",
            len(aweme_list),
            expected_count,
            bool(browser_cfg.get("headless", False)),
            int(browser_cfg.get("max_scrolls", 240) or 240),
            int(browser_cfg.get("idle_rounds", 8) or 8),
            int(browser_cfg.get("wait_timeout_seconds", 600) or 600),
        )
        try:
            browser_aweme_ids = await self.api_client.collect_user_post_ids_via_browser(
                sec_uid,
                expected_count=expected_count,
                headless=bool(browser_cfg.get("headless", False)),
                max_scrolls=int(browser_cfg.get("max_scrolls", 240) or 240),
                idle_rounds=int(browser_cfg.get("idle_rounds", 8) or 8),
                wait_timeout_seconds=int(browser_cfg.get("wait_timeout_seconds", 600) or 600),
            )
        except Exception:
            logger.exception(
                "Browser fallback failed: duration_ms=%s",
                int((time.monotonic() - fallback_started) * 1000),
            )
            return

        browser_aweme_items: Dict[str, Dict[str, Any]] = {}
        browser_post_stats: Dict[str, int] = {}
        if hasattr(self.api_client, "pop_browser_post_aweme_items"):
            try:
                browser_aweme_items = self.api_client.pop_browser_post_aweme_items() or {}
            except Exception as exc:
                logger.debug("Fetch browser post items skipped: %s", exc)
        if hasattr(self.api_client, "pop_browser_post_stats"):
            try:
                browser_post_stats = self.api_client.pop_browser_post_stats() or {}
            except Exception as exc:
                logger.debug("Fetch browser post stats skipped: %s", exc)

        if not browser_aweme_ids:
            logger.warning(
                "Browser fallback returned no aweme_id: duration_ms=%s stats=%s",
                int((time.monotonic() - fallback_started) * 1000),
                browser_post_stats,
            )
            return

        existing_ids = {str(item.get("aweme_id")) for item in aweme_list if item.get("aweme_id")}
        missing_ids = [aweme_id for aweme_id in browser_aweme_ids if aweme_id not in existing_ids]
        if not missing_ids:
            logger.info(
                "Browser fallback found no missing items: browser_ids=%s existing=%s "
                "duration_ms=%s",
                len(browser_aweme_ids),
                len(existing_ids),
                int((time.monotonic() - fallback_started) * 1000),
            )
            return

        logger.warning(
            "Recovering aweme details from browser list, missing count=%s",
            len(missing_ids),
        )
        detail_failed = 0
        detail_success = 0
        reused_from_browser_items = 0
        total_missing = len(missing_ids)
        for index, aweme_id in enumerate(missing_ids, start=1):
            if self._post_recovery_limit_reached(aweme_list, number_limit, item_filter):
                break

            if index == 1 or index == total_missing or index % 5 == 0:
                self._progress_update_step("浏览器回补", f"补全详情 {index}/{total_missing}")

            detail = browser_aweme_items.get(str(aweme_id))
            if not detail:
                try:
                    await self.rate_limiter.acquire()
                    detail = await self.api_client.get_video_detail(aweme_id, suppress_error=True)
                except Exception as exc:
                    detail_failed += 1
                    logger.warning(
                        "Browser fallback detail fetch failed for aweme_id=%s: %s",
                        aweme_id,
                        exc,
                    )
                    continue
                if detail:
                    detail_success += 1
            else:
                reused_from_browser_items += 1
            if not detail:
                detail_failed += 1
                continue
            author = detail.get("author", {}) if isinstance(detail, dict) else {}
            detail_sec_uid = author.get("sec_uid") if isinstance(author, dict) else None
            if detail_sec_uid and str(detail_sec_uid) != str(sec_uid):
                logger.warning(
                    "Skip aweme_id=%s due to mismatched sec_uid (%s)",
                    aweme_id,
                    detail_sec_uid,
                )
                continue
            aweme_list.append(detail)

        self._progress_update_step(
            "浏览器回补",
            f"回补完成，复用 {reused_from_browser_items}，补拉成功 {detail_success}，失败 {detail_failed}",
        )
        logger.warning(
            "Browser fallback summary: duration_ms=%s merged_ids=%s selected_ids=%s "
            "post_items=%s post_pages=%s reused=%s detail_success=%s detail_failed=%s",
            int((time.monotonic() - fallback_started) * 1000),
            browser_post_stats.get("merged_ids", 0),
            browser_post_stats.get("selected_ids", len(browser_aweme_ids)),
            browser_post_stats.get("post_items", len(browser_aweme_items)),
            browser_post_stats.get("post_pages", 0),
            reused_from_browser_items,
            detail_success,
            detail_failed,
        )
        if detail_failed > 0:
            logger.warning(
                "Browser fallback detail fetch failed: %s/%s",
                detail_failed,
                total_missing,
            )

    @staticmethod
    def _post_recovery_limit_reached(
        items: List[Dict[str, Any]],
        number_limit: Any,
        item_filter: Optional[Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]],
    ) -> bool:
        limit = int(number_limit or 0)
        if limit <= 0:
            return False
        eligible_items = item_filter(items) if item_filter else items
        return len(eligible_items) >= limit
