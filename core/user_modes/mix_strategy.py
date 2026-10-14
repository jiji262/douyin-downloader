from __future__ import annotations

from typing import Any, Dict, List

from core.user_modes.base_strategy import BaseUserModeStrategy
from core.user_modes.mix_routing import collection_directory


class MixUserModeStrategy(BaseUserModeStrategy):
    mode_name = "mix"
    api_method_name = "get_user_mix"

    def __init__(self, downloader):
        super().__init__(downloader)
        self.collection_dirs: Dict[str, str] = {}

    async def collect_items(self, sec_uid: str, user_info: Dict[str, Any]) -> List[Dict[str, Any]]:
        self.collection_dirs = {}
        self.incomplete_reason = None
        fetcher = getattr(self.downloader.api_client, self.api_method_name, None)
        if not callable(fetcher):
            return []
        # number.mix 限制作品数量，不能截断合集目录，否则 post 无法排除剩余成员。
        raw_items = await self._collect_paged_entries(fetcher, sec_uid, scope=self._scope_label())
        aweme_items = [
            a for item in raw_items if (a := self._extract_aweme_from_item(item)) is not None
        ]
        if aweme_items:
            for item in aweme_items:
                directory = collection_directory(item)
                if directory:
                    self.collection_dirs[str(item["aweme_id"])] = directory
        metadata_items = [item for item in raw_items if self._extract_aweme_from_item(item) is None]
        return await self._expand_collections(metadata_items, aweme_items)

    async def _expand_collections(
        self, raw_items: List[Dict[str, Any]], aweme_items: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        expanded = {str(item["aweme_id"]): item for item in aweme_items}
        for metadata in raw_items:
            items = await self._expand_metadata_items(
                [metadata],
                id_field="mix_id",
                id_aliases=["mixId"],
                fetch_method_name="get_mix_aweme",
            )
            directory = collection_directory(metadata)
            for item in items:
                aid = str(item["aweme_id"])
                if aid in expanded:
                    continue
                expanded[aid] = item
                if directory:
                    self.collection_dirs[aid] = directory
        return list(expanded.values())
