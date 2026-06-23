from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.user_modes.base_strategy import BaseUserModeStrategy
from utils.logger import setup_logger

logger = setup_logger("CollectUserModeStrategy")


class CollectUserModeStrategy(BaseUserModeStrategy):
    mode_name = "collect"
    api_method_name = "get_user_collects"

    def __init__(self, downloader, *, collects_id: Optional[str] = None):
        """Optional ``collects_id`` constrains the collection to a single
        folder. When provided we skip the ``get_user_collects`` enumeration
        entirely and only paginate ``get_collect_aweme(collects_id, ...)``
        for that one folder — used by the desktop "我的内容 / 我的收藏"
        sub-tab when the user clicks "下载本收藏夹". When ``None`` (CLI
        default and historic desktop behaviour), we enumerate every folder
        on the account.
        """
        super().__init__(downloader)
        self._collects_id_filter = (collects_id or "").strip() or None

    async def collect_items(self, sec_uid: str, user_info: Dict[str, Any]) -> List[Dict[str, Any]]:
        if self._collects_id_filter:
            return await self._collect_single_folder(self._collects_id_filter)
        return await self._collect_all_folders(sec_uid)

    async def _collect_single_folder(self, collects_id: str) -> List[Dict[str, Any]]:
        """Paginate aweme entries for a single collection folder.

        Mirrors the inner loop of :meth:`_collect_all_folders` but
        intentionally avoids :meth:`api_client.get_user_collects` so we
        never even read the names of other folders on the account
        (Property 4 / R6.4 — single-folder filter does not leak entries
        from sibling folders).
        """
        fetch_collect_aweme = getattr(self.downloader.api_client, "get_collect_aweme", None)
        if not callable(fetch_collect_aweme):
            logger.warning("API client missing get_collect_aweme")
            return []

        expanded: List[Dict[str, Any]] = []
        seen_aweme: set[str] = set()

        cursor = 0
        has_more = True
        while has_more:
            await self.downloader.rate_limiter.acquire()
            page_data = await fetch_collect_aweme(str(collects_id), max_cursor=cursor, count=20)
            page = self._normalize_page_data(page_data)
            page_items = page.get("items", [])
            if not page_items:
                break

            for item in page_items:
                aweme = self._extract_aweme_from_item(item)
                if not aweme:
                    continue
                aweme_id = str(aweme.get("aweme_id") or "")
                if not aweme_id or aweme_id in seen_aweme:
                    continue
                seen_aweme.add(aweme_id)
                expanded.append(aweme)

            has_more = bool(page.get("has_more", False))
            next_cursor = int(page.get("max_cursor", 0) or 0)
            if has_more and next_cursor == cursor:
                logger.warning("Collect folder %s cursor did not advance", collects_id)
                break
            cursor = next_cursor

        return expanded

    async def _collect_all_folders(self, sec_uid: str) -> List[Dict[str, Any]]:
        """Original behaviour: enumerate every folder on the account and
        paginate each one. Kept as a separate method so the filter branch
        in :meth:`collect_items` doesn't accidentally invoke
        :meth:`api_client.get_user_collects`.
        """
        fetch_collect_aweme = getattr(self.downloader.api_client, "get_collect_aweme", None)
        fetch_collects = getattr(self.downloader.api_client, self.api_method_name, None)
        if not callable(fetch_collects):
            logger.warning("API client missing %s", self.api_method_name)
            return []
        if not callable(fetch_collect_aweme):
            logger.warning("API client missing get_collect_aweme")
            return []

        raw_collects = await self._collect_paged_entries(fetch_collects, sec_uid)
        expanded: List[Dict[str, Any]] = []
        seen_aweme: set[str] = set()

        for collect_item in raw_collects:
            collects_id = self._extract_collects_id(collect_item)
            if not collects_id:
                continue

            cursor = 0
            has_more = True
            while has_more:
                await self.downloader.rate_limiter.acquire()
                page_data = await fetch_collect_aweme(str(collects_id), max_cursor=cursor, count=20)
                page = self._normalize_page_data(page_data)
                page_items = page.get("items", [])
                if not page_items:
                    break

                for item in page_items:
                    aweme = self._extract_aweme_from_item(item)
                    if not aweme:
                        continue
                    aweme_id = str(aweme.get("aweme_id") or "")
                    if not aweme_id or aweme_id in seen_aweme:
                        continue
                    seen_aweme.add(aweme_id)
                    expanded.append(aweme)

                has_more = bool(page.get("has_more", False))
                next_cursor = int(page.get("max_cursor", 0) or 0)
                if has_more and next_cursor == cursor:
                    logger.warning("Collect folder %s cursor did not advance", collects_id)
                    break
                cursor = next_cursor

        return expanded

    @staticmethod
    def _extract_collects_id(item: Any) -> str:
        if not isinstance(item, dict):
            return ""
        return str(
            item.get("collects_id")
            or item.get("collects_id_str")
            or item.get("id")
            or ((item.get("collects_info") or {}).get("collects_id"))
            or ((item.get("collects_info") or {}).get("collects_id_str"))
            or ""
        )
