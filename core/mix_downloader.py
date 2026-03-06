from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.downloader_base import BaseDownloader, DownloadResult
from core.user_modes.base_strategy import BaseUserModeStrategy
from utils.logger import setup_logger

logger = setup_logger("MixDownloader")


class MixDownloader(BaseDownloader):
    async def download(self, parsed_url: Dict[str, Any]) -> DownloadResult:
        result = DownloadResult()

        mix_id = parsed_url.get("mix_id")
        if not mix_id:
            logger.error("No mix_id found in parsed URL")
            return result

        aweme_list = await self._collect_mix_aweme_list(str(mix_id))

        result.total = len(aweme_list)
        self._progress_set_item_total(result.total, "合集作品待下载")
        self._progress_update_step("下载合集", f"mix_id={mix_id}，待处理 {result.total} 条")

        mix_detail = await self._get_mix_detail(str(mix_id))
        author_name = (
            (mix_detail.get("author") or {}).get("nickname")
            if isinstance(mix_detail, dict)
            else None
        ) or "mix"

        async def _process_aweme(item: Dict[str, Any]):
            aweme_id = item.get("aweme_id")
            if not aweme_id:
                self._progress_advance_item("failed", "missing_aweme_id")
                return {"status": "failed", "aweme_id": None}

            if not await self._should_download(str(aweme_id)):
                self._progress_advance_item("skipped", str(aweme_id))
                return {"status": "skipped", "aweme_id": aweme_id}

            success = await self._download_aweme_assets(item, author_name, mode="mix")
            status = "success" if success else "failed"
            self._progress_advance_item(status, str(aweme_id))
            return {"status": status, "aweme_id": aweme_id}

        download_results = await self.queue_manager.download_batch(
            _process_aweme, aweme_list
        )
        for entry in download_results:
            status = entry.get("status") if isinstance(entry, dict) else None
            if status == "success":
                result.success += 1
            elif status == "skipped":
                result.skipped += 1
            else:
                result.failed += 1
        return result

    async def _collect_mix_aweme_list(self, mix_id: str) -> List[Dict[str, Any]]:
        fetch_mix_aweme = getattr(self.api_client, "get_mix_aweme", None)
        if not callable(fetch_mix_aweme):
            logger.error("API client has no get_mix_aweme implementation")
            return []

        aweme_list: List[Dict[str, Any]] = []
        has_more = True
        cursor = 0
        number_limit = int(self.config.get("number", {}).get("mix", 0) or 0)

        while has_more:
            await self.rate_limiter.acquire()
            raw_page = await fetch_mix_aweme(mix_id, cursor=cursor, count=20)
            page = BaseUserModeStrategy._normalize_page_data(raw_page)
            items = page.get("items", [])
            if not items:
                break

            for item in items:
                aweme = self._extract_aweme_from_item(item)
                if aweme:
                    aweme_list.append(aweme)

            if number_limit > 0 and len(aweme_list) >= number_limit:
                aweme_list = aweme_list[:number_limit]
                break

            has_more = bool(page.get("has_more", False))
            next_cursor = int(page.get("max_cursor", 0) or 0)
            if has_more and next_cursor == cursor:
                logger.warning(
                    "Mix pagination cursor did not advance (%s), stop to avoid loop",
                    cursor,
                )
                break
            cursor = next_cursor

        return aweme_list

    async def _get_mix_detail(self, mix_id: str) -> Optional[Dict[str, Any]]:
        getter = getattr(self.api_client, "get_mix_detail", None)
        if not callable(getter):
            return None
        try:
            return await getter(mix_id)
        except Exception as exc:
            logger.warning("Get mix detail failed: %s", exc)
            return None

    @staticmethod
    def _extract_aweme_from_item(item: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(item, dict):
            return None
        if item.get("aweme_id"):
            return item
        for key in ("aweme", "aweme_info", "aweme_detail"):
            value = item.get(key)
            if isinstance(value, dict) and value.get("aweme_id"):
                return value
        return None
