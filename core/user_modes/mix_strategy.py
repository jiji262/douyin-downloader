from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from core.downloader_base import DownloadResult
from core.user_modes.base_strategy import BaseUserModeStrategy
from core.user_modes.mix_routing import collection_directory, post_selected


class MixUserModeStrategy(BaseUserModeStrategy):
    mode_name = "mix"
    api_method_name = "get_user_mix"

    def __init__(self, downloader):
        super().__init__(downloader)
        self.collection_dirs: Dict[str, str] = {}
        # 本轮展开到多少个可下载作品就停，0 = 不限。只由 download_mode 设置。
        self._expansion_limit = 0

    async def download_mode(
        self,
        sec_uid: str,
        user_info: Dict[str, Any],
        seen_aweme_ids: Optional[set[str]] = None,
    ) -> DownloadResult:
        # 只勾合集时 number.mix 就是最终作品数，再展开的合集全是无用请求；
        # 与 post 同选时 number.mix 不能截断，post 要靠完整归属表排除成员。
        # 失败项重试直接调 collect_items，同样需要完整表，所以上限不放进去。
        if not post_selected(self.downloader.config):
            number = self.downloader.config.get("number", {})
            self._expansion_limit = int(number.get(self.mode_name, 0) or 0)
        try:
            return await super().download_mode(sec_uid, user_info, seen_aweme_ids)
        finally:
            self._expansion_limit = 0

    async def collect_items(self, sec_uid: str, user_info: Dict[str, Any]) -> List[Dict[str, Any]]:
        self.collection_dirs = {}
        self.incomplete_reason = None
        fetcher = getattr(self.downloader.api_client, self.api_method_name, None)
        if not callable(fetcher):
            return []
        # 合集目录要扫全：与 post 同选时 number.mix 不能截断它，否则 post 无法排除剩余成员。
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
        limit = self._expansion_limit
        for metadata in raw_items:
            if limit and self._eligible_count(expanded.values()) >= limit:
                break
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

    def _eligible_count(self, items: Iterable[Dict[str, Any]]) -> int:
        """按 apply_filters 同一组筛选（不含数量截断）数出还能下载的作品。"""
        eligible = self._filter_pinned_items(list(items))
        eligible = self.downloader._filter_by_time(eligible)
        return len(self._filter_by_media_type(eligible))
