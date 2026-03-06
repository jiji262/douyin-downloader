from __future__ import annotations

from typing import Any, Dict, List

from core.user_modes.base_strategy import BaseUserModeStrategy


class MixUserModeStrategy(BaseUserModeStrategy):
    mode_name = "mix"
    api_method_name = "get_user_mix"

    async def collect_items(
        self, sec_uid: str, user_info: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        raw_items = await self._collect_paged_aweme(sec_uid, user_info)
        aweme_items = [
            a for item in raw_items
            if (a := self._extract_aweme_from_item(item)) is not None
        ]
        if aweme_items:
            return aweme_items

        return await self._expand_metadata_items(
            raw_items,
            id_field="mix_id",
            id_aliases=["mixId"],
            fetch_method_name="get_mix_aweme",
        )
