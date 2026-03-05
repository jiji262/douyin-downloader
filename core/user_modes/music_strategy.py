from __future__ import annotations

from typing import Any, Dict, List

from core.user_modes.base_strategy import BaseUserModeStrategy
from utils.logger import setup_logger

logger = setup_logger("MusicUserModeStrategy")


class MusicUserModeStrategy(BaseUserModeStrategy):
    mode_name = "music"
    api_method_name = "get_user_music"

    async def collect_items(
        self, sec_uid: str, user_info: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        raw_items = await self._collect_paged_aweme(sec_uid, user_info)
        if any(item.get("aweme_id") for item in raw_items):
            return raw_items

        # 用户音乐列表若返回 music 元信息，则展开到该音乐下作品。
        expanded: List[Dict[str, Any]] = []
        fetch_music_aweme = getattr(self.downloader.api_client, "get_music_aweme", None)
        if not callable(fetch_music_aweme):
            return expanded

        number_limit = int(
            self.downloader.config.get("number", {}).get(self.mode_name, 0) or 0
        )
        seen_aweme: set[str] = set()

        for item in raw_items:
            music_id = (
                item.get("music_id")
                or item.get("musicId")
                or (item.get("music_info") or {}).get("id")
                or (item.get("music_info") or {}).get("music_id")
            )
            if not music_id:
                continue

            cursor = 0
            has_more = True
            while has_more:
                await self.downloader.rate_limiter.acquire()
                page_data = await fetch_music_aweme(
                    str(music_id), cursor=cursor, count=20
                )
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
                    logger.warning("Music %s cursor did not advance", music_id)
                    break
                cursor = next_cursor

        return expanded
