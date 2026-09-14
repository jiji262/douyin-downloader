from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.user_modes.base_strategy import (
    PAGE_SIZE,
    BaseUserModeStrategy,
    fetch_page_folding_bridge_failure,
)
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

        return await self._walk_folder_awemes(fetch_collect_aweme, collects_id, set())

    async def _walk_folder_awemes(
        self,
        fetch_collect_aweme,
        collects_id: str,
        seen_aweme: set[str],
    ) -> List[Dict[str, Any]]:
        """分页抓取单个收藏夹的作品；``seen_aweme`` 跨收藏夹共享用于去重。

        失败页(``raw`` 为空)必须报错而不是 ``break``：两者的 ``items`` 都是
        空列表，当成「翻到底了」会让 200 条的收藏夹只下 20 条还报成功。
        """
        collected: List[Dict[str, Any]] = []
        cursor = 0
        has_more = True
        page_index = 0
        while has_more:
            await self.downloader.rate_limiter.acquire()
            page_index += 1
            page_data = await fetch_page_folding_bridge_failure(
                fetch_collect_aweme, str(collects_id), max_cursor=cursor, count=PAGE_SIZE
            )
            page = self._normalize_page_data(page_data)
            page_items = page.get("items", [])
            if not page_items:
                self._raise_if_page_request_failed(
                    page, scope=f"收藏夹 {collects_id}", page_index=page_index
                )
                break

            for item in page_items:
                aweme = self._extract_aweme_from_item(item)
                if not aweme:
                    continue
                aweme_id = str(aweme.get("aweme_id") or "")
                if not aweme_id or aweme_id in seen_aweme:
                    continue
                seen_aweme.add(aweme_id)
                collected.append(aweme)

            has_more = bool(page.get("has_more", False))
            next_cursor = int(page.get("max_cursor", 0) or 0)
            if has_more and next_cursor == cursor:
                logger.warning("Collect folder %s cursor did not advance", collects_id)
                break
            cursor = next_cursor

        return collected

    async def _collect_all_folders(self, sec_uid: str) -> List[Dict[str, Any]]:
        """Collect the account-level feed plus every custom folder."""
        fetch_collect_aweme = getattr(self.downloader.api_client, "get_collect_aweme", None)
        fetch_collects = getattr(self.downloader.api_client, self.api_method_name, None)
        fetch_account_collection = getattr(self.downloader.api_client, "get_user_collection", None)
        if not callable(fetch_collects):
            logger.warning("API client missing %s", self.api_method_name)
            return []
        if not callable(fetch_collect_aweme):
            logger.warning("API client missing get_collect_aweme")
            return []

        expanded: List[Dict[str, Any]] = []
        seen_aweme: set[str] = set()

        # The default/account-level collection is a separate endpoint from
        # custom folders. Older API doubles may not implement it, so keep the
        # custom-folder path backward-compatible while real clients include it.
        if callable(fetch_account_collection):
            account_items = await self._collect_paged_entries(
                fetch_account_collection, "self", scope="全部收藏"
            )
            for item in account_items:
                aweme = self._extract_aweme_from_item(item)
                if not aweme:
                    continue
                aweme_id = str(aweme.get("aweme_id") or "")
                if not aweme_id or aweme_id in seen_aweme:
                    continue
                seen_aweme.add(aweme_id)
                expanded.append(aweme)

        raw_collects = await self._collect_paged_entries(
            fetch_collects, sec_uid, scope="收藏夹列表"
        )
        for collect_item in raw_collects:
            collects_id = self._extract_collects_id(collect_item)
            if not collects_id:
                continue
            expanded.extend(
                await self._walk_folder_awemes(fetch_collect_aweme, collects_id, seen_aweme)
            )

        return expanded

    @staticmethod
    def _extract_collects_id(item: Any) -> str:
        """Prefer ``collects_id_str``: the numeric ``collects_id`` is an int64 that
        loses its low digits once the desktop page bridge parses it in JavaScript."""
        if not isinstance(item, dict):
            return ""
        info = item.get("collects_info")
        sources = [item, info] if isinstance(info, dict) else [item]
        for source in sources:
            for key in ("collects_id_str", "collects_id", "id"):
                value = source.get(key)
                if value not in (None, ""):
                    return str(value)
        return ""
