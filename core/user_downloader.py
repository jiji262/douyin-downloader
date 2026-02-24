from typing import Any, Dict, List

from core.downloader_base import BaseDownloader, DownloadResult
from utils.logger import setup_logger

logger = setup_logger("UserDownloader")


class UserDownloader(BaseDownloader):
    async def download(self, parsed_url: Dict[str, Any]) -> DownloadResult:
        result = DownloadResult()

        sec_uid = parsed_url.get("sec_uid")
        if not sec_uid:
            logger.error("No sec_uid found in parsed URL")
            return result

        self._progress_update_step("获取作者信息", f"sec_uid={sec_uid}")
        user_info = await self.api_client.get_user_info(sec_uid)
        if not user_info:
            logger.error(f"Failed to get user info: {sec_uid}")
            return result

        modes = self.config.get("mode", ["post"])
        self._progress_update_step("下载模式", f"模式: {', '.join(modes)}")

        for mode in modes:
            if mode == "post":
                self._progress_update_step("下载模式", "开始处理 post 作品")
                mode_result = await self._download_user_post(sec_uid, user_info)
                result.total += mode_result.total
                result.success += mode_result.success
                result.failed += mode_result.failed
                result.skipped += mode_result.skipped

        return result

    async def _download_user_post(
        self, sec_uid: str, user_info: Dict[str, Any]
    ) -> DownloadResult:
        result = DownloadResult()
        aweme_list: List[Dict[str, Any]] = []
        max_cursor = 0
        has_more = True
        pagination_restricted = False

        increase_enabled = self.config.get("increase", {}).get("post", False)
        latest_time = None

        if increase_enabled and self.database:
            latest_time = await self.database.get_latest_aweme_time(
                user_info.get("uid")
            )

        self._progress_update_step("拉取作品列表", "分页抓取中")
        while has_more:
            await self.rate_limiter.acquire()

            request_cursor = max_cursor
            data = await self.api_client.get_user_post(sec_uid, request_cursor)
            if not data:
                break

            not_login = (
                (data.get("not_login_module") or {}) if isinstance(data, dict) else {}
            )
            if isinstance(not_login, dict) and not_login.get("guide_login_tip_exist"):
                logger.warning(
                    "Detected login tip in user post response, pagination may be restricted"
                )

            aweme_items = data.get("aweme_list", [])
            if not aweme_items:
                if request_cursor and data.get("status_code") == 0:
                    pagination_restricted = True
                    logger.warning(
                        "User post pagination likely blocked at cursor=%s, switching to browser fallback",
                        request_cursor,
                    )
                break

            if increase_enabled and latest_time:
                new_items = [
                    a for a in aweme_items if a.get("create_time", 0) > latest_time
                ]
                aweme_list.extend(new_items)
                if len(new_items) < len(aweme_items):
                    break
            else:
                aweme_list.extend(aweme_items)
            self._progress_update_step(
                "拉取作品列表", f"已抓取 {len(aweme_list)} 条"
            )

            has_more = data.get("has_more", False)
            max_cursor = data.get("max_cursor", 0)
            if has_more and max_cursor == request_cursor:
                logger.warning(
                    "max_cursor did not advance (%s), stop paging to avoid loop",
                    max_cursor,
                )
                break

            number_limit = self.config.get("number", {}).get("post", 0)
            if number_limit > 0 and len(aweme_list) >= number_limit:
                aweme_list = aweme_list[:number_limit]
                break

        if pagination_restricted:
            self._progress_update_step("拉取作品列表", "分页受限，尝试浏览器回补")
            await self._recover_user_post_with_browser(sec_uid, user_info, aweme_list)

        aweme_list = self._filter_by_time(aweme_list)
        aweme_list = self._limit_count(aweme_list, "post")

        result.total = len(aweme_list)
        self._progress_set_item_total(result.total, "作品待下载")
        self._progress_update_step("下载作品", f"待处理 {result.total} 条")

        author_name = user_info.get("nickname", "unknown")

        async def _process_aweme(item: Dict[str, Any]):
            aweme_id = item.get("aweme_id")
            if not await self._should_download(aweme_id):
                self._progress_advance_item("skipped", str(aweme_id or "unknown"))
                return {"status": "skipped", "aweme_id": aweme_id}

            success = await self._download_aweme_assets(item, author_name, mode="post")
            status = "success" if success else "failed"
            self._progress_advance_item(status, str(aweme_id or "unknown"))
            return {
                "status": status,
                "aweme_id": aweme_id,
            }

        download_results = await self.queue_manager.download_batch(
            _process_aweme, aweme_list
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
