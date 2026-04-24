from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from core.downloader_base import BaseDownloader, DownloadResult
from core.user_mode_registry import UserModeRegistry
from utils.logger import setup_logger

logger = setup_logger("UserDownloader")


class UserDownloader(BaseDownloader):
    SELF_COLLECT_MODES = {"collect", "collectmix"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mode_registry = UserModeRegistry()
        self._mode_strategy_cache: Dict[str, Any] = {}

    async def download(self, parsed_url: Dict[str, Any]) -> DownloadResult:
        result = DownloadResult()

        sec_uid = parsed_url.get("sec_uid")
        if not sec_uid:
            logger.error("No sec_uid found in parsed URL")
            return result

        modes_config = self.config.get("mode", ["post"])
        if isinstance(modes_config, str):
            modes = [modes_config]
        elif isinstance(modes_config, list):
            modes = [str(mode).strip() for mode in modes_config if str(mode).strip()]
        else:
            modes = ["post"]

        if not self._validate_mode_scope(sec_uid, modes):
            return result

        user_info = await self._resolve_user_info(sec_uid, modes)
        if not user_info:
            logger.error("Failed to get user info: %s", sec_uid)
            return result

        self._progress_update_step("下载模式", f"模式: {', '.join(modes)}")

        seen_aweme_ids: Set[str] = set()
        for mode in modes:
            strategy = self._get_mode_strategy(mode)
            if strategy is None:
                logger.warning("Unsupported user mode: %s", mode)
                continue

            self._progress_update_step("下载模式", f"开始处理 {mode} 作品")
            mode_result = await strategy.download_mode(
                sec_uid, user_info, seen_aweme_ids=seen_aweme_ids
            )
            result.total += mode_result.total
            result.success += mode_result.success
            result.failed += mode_result.failed
            result.skipped += mode_result.skipped

        return result

    def _validate_mode_scope(self, sec_uid: str, modes: List[str]) -> bool:
        normalized_modes = {str(mode or "").strip() for mode in modes}
        has_collect_mode = bool(normalized_modes & self.SELF_COLLECT_MODES)
        has_regular_mode = bool(normalized_modes - self.SELF_COLLECT_MODES)

        if has_collect_mode and sec_uid != "self":
            logger.error(
                "Modes collect/collectmix only support /user/self?showTab=favorite_collection"
            )
            return False
        if has_collect_mode and has_regular_mode:
            logger.error(
                "Modes collect/collectmix cannot be combined with post/like/mix/music"
            )
            return False
        return True

    async def _resolve_user_info(
        self, sec_uid: str, modes: List[str]
    ) -> Optional[Dict[str, Any]]:
        normalized_modes = {str(mode or "").strip() for mode in modes}
        if sec_uid == "self" and normalized_modes.issubset(self.SELF_COLLECT_MODES):
            self._progress_update_step("获取作者信息", "使用当前登录账号收藏夹上下文")
            return {
                "uid": "self",
                "sec_uid": "self",
                "nickname": "self",
            }

        self._progress_update_step("获取作者信息", f"sec_uid={sec_uid}")
        return await self.api_client.get_user_info(sec_uid)

    def _get_mode_strategy(self, mode: str):
        normalized_mode = (mode or "").strip()
        if normalized_mode in self._mode_strategy_cache:
            return self._mode_strategy_cache[normalized_mode]

        strategy_cls = self.mode_registry.get(normalized_mode)
        if strategy_cls is None:
            return None

        strategy = strategy_cls(self)
        self._mode_strategy_cache[normalized_mode] = strategy
        return strategy

    async def _download_mode_items(
        self,
        mode: str,
        items: List[Dict[str, Any]],
        author_name: str,
        seen_aweme_ids: Optional[Set[str]] = None,
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

        async def _process_aweme(item: Dict[str, Any]):
            aweme_id = item.get("aweme_id")
            if not await self._should_download(str(aweme_id or "")):
                self._progress_advance_item("skipped", str(aweme_id or "unknown"))
                return {"status": "skipped", "aweme_id": aweme_id}

            success = await self._download_aweme_assets(item, author_name, mode=mode)
            status = "success" if success else "failed"
            self._progress_advance_item(status, str(aweme_id or "unknown"))
            return {
                "status": status,
                "aweme_id": aweme_id,
            }

        download_results = await self.queue_manager.download_batch(
            _process_aweme, deduped_items
        )

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
    async def _download_user_post(
        self, sec_uid: str, user_info: Dict[str, Any]
    ) -> DownloadResult:
        strategy = self._get_mode_strategy("post")
        if strategy is None:
            return DownloadResult()
        return await strategy.download_mode(sec_uid, user_info, seen_aweme_ids=set())

    async def _recover_user_post_with_browser(
        self,
        sec_uid: str,
        user_info: Dict[str, Any],
        aweme_list: List[Dict[str, Any]],
    ) -> None:
        browser_cfg = self.config.get("browser_fallback", {}) or {}
        if not browser_cfg.get("enabled", True):
            return

        number_limit = self.config.get("number", {}).get("post", 0)
        # 在分页受限场景下，user_info.aweme_count 常常不可靠（经常只返回 20）
        # 因此仅在用户显式设置 number_limit 时才限制浏览器采集目标数量。
        expected_count = int(number_limit or 0)
        if expected_count and len(aweme_list) >= expected_count:
            return

        try:
            browser_aweme_ids = await self.api_client.collect_user_post_ids_via_browser(
                sec_uid,
                expected_count=expected_count,
                headless=bool(browser_cfg.get("headless", False)),
                max_scrolls=int(browser_cfg.get("max_scrolls", 240) or 240),
                idle_rounds=int(browser_cfg.get("idle_rounds", 8) or 8),
                wait_timeout_seconds=int(
                    browser_cfg.get("wait_timeout_seconds", 600) or 600
                ),
            )
        except Exception as exc:
            logger.error("Browser fallback failed: %s", exc)
            return

        browser_aweme_items: Dict[str, Dict[str, Any]] = {}
        browser_post_stats: Dict[str, int] = {}
        if hasattr(self.api_client, "pop_browser_post_aweme_items"):
            try:
                browser_aweme_items = (
                    self.api_client.pop_browser_post_aweme_items() or {}
                )
            except Exception as exc:
                logger.debug("Fetch browser post items skipped: %s", exc)
        if hasattr(self.api_client, "pop_browser_post_stats"):
            try:
                browser_post_stats = self.api_client.pop_browser_post_stats() or {}
            except Exception as exc:
                logger.debug("Fetch browser post stats skipped: %s", exc)

        if not browser_aweme_ids:
            logger.warning("Browser fallback returned no aweme_id")
            return

        existing_ids = {
            str(item.get("aweme_id")) for item in aweme_list if item.get("aweme_id")
        }
        missing_ids = [
            aweme_id for aweme_id in browser_aweme_ids if aweme_id not in existing_ids
        ]
        if not missing_ids:
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
            if number_limit > 0 and len(aweme_list) >= number_limit:
                break

            if index == 1 or index == total_missing or index % 5 == 0:
                self._progress_update_step(
                    "浏览器回补", f"补全详情 {index}/{total_missing}"
                )

            detail = browser_aweme_items.get(str(aweme_id))
            if not detail:
                await self.rate_limiter.acquire()
                detail = await self.api_client.get_video_detail(
                    aweme_id, suppress_error=True
                )
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
            "Browser fallback summary: merged_ids=%s selected_ids=%s post_items=%s post_pages=%s reused=%s detail_success=%s detail_failed=%s",
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
