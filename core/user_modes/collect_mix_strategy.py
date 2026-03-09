from __future__ import annotations

from typing import Any, Dict, List

from core.user_modes.base_strategy import BaseUserModeStrategy
from utils.logger import setup_logger

logger = setup_logger("CollectMixUserModeStrategy")


class CollectMixUserModeStrategy(BaseUserModeStrategy):
    mode_name = "collectmix"
    api_method_name = "get_user_collect_mix"

    async def collect_items(
        self, sec_uid: str, user_info: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        fetch_collect_mix = getattr(self.downloader.api_client, self.api_method_name, None)
        if not callable(fetch_collect_mix):
            logger.warning("API client missing %s", self.api_method_name)
            return []

        raw_items = await self._collect_paged_entries(fetch_collect_mix, sec_uid)
        aweme_items = [
            a for item in raw_items
            if (a := self._extract_aweme_from_item(item)) is not None
        ]
        if aweme_items:
            return aweme_items

        normalized_mix_items = [self._normalize_mix_item(item) for item in raw_items]
        return await self._expand_metadata_items(
            normalized_mix_items,
            id_field="mix_id",
            id_aliases=["mixId"],
            fetch_method_name="get_mix_aweme",
        )

    @staticmethod
    def _normalize_mix_item(item: Any) -> Dict[str, Any]:
        if not isinstance(item, dict):
            return {}
        if item.get("mix_id") or item.get("mixId"):
            return item
        mix_info = item.get("mix_info")
        if isinstance(mix_info, dict):
            return {
                **item,
                "mix_id": mix_info.get("mix_id") or mix_info.get("id"),
            }
        return item
