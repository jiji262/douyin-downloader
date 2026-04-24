from __future__ import annotations

from typing import Any, Dict, List

from core.user_modes.base_strategy import BaseUserModeStrategy
from utils.logger import setup_logger

logger = setup_logger("CollectUserModeStrategy")


class CollectUserModeStrategy(BaseUserModeStrategy):
    mode_name = "collect"
    api_method_name = "get_user_collects"

    async def collect_items(
        self, sec_uid: str, user_info: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
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
                page_data = await fetch_collect_aweme(
                    str(collects_id), max_cursor=cursor, count=20
                )
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
                    logger.warning(
                        "Collect folder %s cursor did not advance", collects_id
                    )
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
