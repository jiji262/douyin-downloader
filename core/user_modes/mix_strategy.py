from __future__ import annotations

from typing import Any, Dict, List

from core.user_modes.base_strategy import BaseUserModeStrategy
from utils.logger import setup_logger

logger = setup_logger("MixUserModeStrategy")


class MixUserModeStrategy(BaseUserModeStrategy):
    mode_name = "mix"
    api_method_name = "get_user_mix"

    async def collect_items(
        self, sec_uid: str, user_info: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        raw_items = await self._collect_paged_aweme(sec_uid, user_info)
        aweme_items = [item for item in raw_items if item.get("aweme_id")]
        if aweme_items:
            return aweme_items

        # 用户合集列表通常返回 mix 元信息，需再展开到 aweme 列表。
        expanded: List[Dict[str, Any]] = []
        fetch_mix_aweme = getattr(self.downloader.api_client, "get_mix_aweme", None)
        if not callable(fetch_mix_aweme):
            return expanded

        number_limit = int(
            self.downloader.config.get("number", {}).get(self.mode_name, 0) or 0
        )
        seen_aweme: set[str] = set()

        for item in raw_items:
            mix_id = (
                item.get("mix_id")
                or item.get("mixId")
                or (item.get("mix_info") or {}).get("mix_id")
            )
            if not mix_id:
                continue

            cursor = 0
            has_more = True
            while has_more:
                await self.downloader.rate_limiter.acquire()
                page_data = await fetch_mix_aweme(str(mix_id), cursor=cursor, count=20)
                page = self._normalize_page_data(page_data)
                page_items = page.get("items", [])
                if not page_items:
                    break

                for aweme in page_items:
                    aweme_id = str(aweme.get("aweme_id") or "")
                    if not aweme_id or aweme_id in seen_aweme:
                        continue
                    seen_aweme.add(aweme_id)
                    expanded.append(aweme)

                if number_limit > 0 and len(expanded) >= number_limit:
                    return expanded[:number_limit]

                has_more = bool(page.get("has_more", False))
                next_cursor = int(page.get("max_cursor", 0) or 0)
                if has_more and next_cursor == cursor:
                    logger.warning("Mix %s cursor did not advance", mix_id)
                    break
                cursor = next_cursor

        return expanded
