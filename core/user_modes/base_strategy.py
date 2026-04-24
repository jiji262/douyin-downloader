from __future__ import annotations

from abc import ABC
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.downloader_base import DownloadResult
from utils.logger import setup_logger

if TYPE_CHECKING:
    from core.user_downloader import UserDownloader

logger = setup_logger("UserModeStrategy")


class BaseUserModeStrategy(ABC):
    mode_name = ""
    api_method_name = ""

    def __init__(self, downloader: "UserDownloader"):
        self.downloader = downloader

    async def download_mode(
        self,
        sec_uid: str,
        user_info: Dict[str, Any],
        seen_aweme_ids: Optional[set[str]] = None,
    ) -> DownloadResult:
        items = await self.collect_items(sec_uid, user_info)
        items = self.apply_filters(items)
        author_name = user_info.get("nickname", "unknown")
        if seen_aweme_ids is None:
            seen_aweme_ids = set()
        return await self.downloader._download_mode_items(
            mode=self.mode_name,
            items=items,
            author_name=author_name,
            seen_aweme_ids=seen_aweme_ids,
        )

    async def collect_items(
        self, sec_uid: str, user_info: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        return await self._collect_paged_aweme(sec_uid, user_info)

    def apply_filters(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        filtered = self.downloader._filter_by_time(items)
        return self.downloader._limit_count(filtered, self.mode_name)

    async def _collect_paged_aweme(
        self, sec_uid: str, user_info: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        fetcher = getattr(self.downloader.api_client, self.api_method_name, None)
        if not callable(fetcher):
            logger.warning(
                "Mode %s skipped: API method %s not implemented",
                self.mode_name,
                self.api_method_name,
            )
            return []

        aweme_list: List[Dict[str, Any]] = []
        max_cursor = 0
        has_more = True

        number_limit = int(
            self.downloader.config.get("number", {}).get(self.mode_name, 0) or 0
        )
        increase_enabled = bool(
            self.downloader.config.get("increase", {}).get(self.mode_name, False)
        )
        latest_time = None
        if increase_enabled and self.downloader.database:
            latest_time = await self.downloader.database.get_latest_aweme_time(
                user_info.get("uid")
            )

        while has_more:
            await self.downloader.rate_limiter.acquire()
            request_cursor = max_cursor
            page_data = await fetcher(sec_uid, request_cursor, 20)
            page = self._normalize_page_data(page_data)
            page_items = self.select_items(page)
            if not page_items:
                break

            if increase_enabled and latest_time:
                new_items = [
                    a for a in page_items if a.get("create_time", 0) > latest_time
                ]
                aweme_list.extend(new_items)
                if len(new_items) < len(page_items):
                    break
            else:
                aweme_list.extend(page_items)

            if number_limit > 0 and len(aweme_list) >= number_limit:
                aweme_list = aweme_list[:number_limit]
                break

            has_more = bool(page.get("has_more", False))
            max_cursor = int(page.get("max_cursor", 0) or 0)
            if has_more and max_cursor == request_cursor:
                logger.warning(
                    "Mode %s cursor did not advance (%s), stop paging",
                    self.mode_name,
                    max_cursor,
                )
                break

        return aweme_list

    def select_items(self, page_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        items = page_data.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        return []

    async def _collect_paged_entries(
        self,
        fetcher,
        *fetch_args: Any,
        count: int = 20,
    ) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        max_cursor = 0
        has_more = True

        while has_more:
            await self.downloader.rate_limiter.acquire()
            request_cursor = max_cursor
            page_data = await fetcher(*fetch_args, request_cursor, count)
            page = self._normalize_page_data(page_data)
            page_items = self.select_items(page)
            if not page_items:
                break

            entries.extend(page_items)
            has_more = bool(page.get("has_more", False))
            max_cursor = int(page.get("max_cursor", 0) or 0)
            if has_more and max_cursor == request_cursor:
                logger.warning(
                    "Mode %s cursor did not advance (%s), stop paging",
                    self.mode_name,
                    max_cursor,
                )
                break

        return entries

    async def _expand_metadata_items(
        self,
        raw_items: List[Dict[str, Any]],
        id_field: str,
        id_aliases: List[str],
        fetch_method_name: str,
    ) -> List[Dict[str, Any]]:
        """Shared expansion logic for mix/music strategies that receive metadata
        items instead of aweme items. Fetches the actual aweme list for each
        metadata entry using the given API method."""
        fetcher = getattr(self.downloader.api_client, fetch_method_name, None)
        if not callable(fetcher):
            return []

        expanded: List[Dict[str, Any]] = []
        seen_aweme: set[str] = set()

        for item in raw_items:
            entry_id = item.get(id_field)
            if not entry_id:
                for alias in id_aliases:
                    candidate = item.get(alias)
                    if not candidate:
                        info = item.get(f"{id_field.split('_')[0]}_info")
                        if isinstance(info, dict):
                            candidate = info.get(id_field) or info.get("id")
                    if candidate:
                        entry_id = candidate
                        break
            if not entry_id:
                continue

            cursor = 0
            has_more = True
            while has_more:
                await self.downloader.rate_limiter.acquire()
                try:
                    page_data = await fetcher(str(entry_id), cursor=cursor, count=20)
                except Exception as exc:
                    logger.warning(
                        "Expansion fetch failed for %s=%s: %s",
                        id_field, entry_id, exc,
                    )
                    break
                page = self._normalize_page_data(page_data)
                page_items = page.get("items", [])
                if not page_items:
                    break

                for aweme in page_items:
                    extracted = self._extract_aweme_from_item(aweme)
                    if not extracted:
                        continue
                    aweme_id = str(extracted.get("aweme_id") or "")
                    if not aweme_id or aweme_id in seen_aweme:
                        continue
                    seen_aweme.add(aweme_id)
                    expanded.append(extracted)

                has_more = bool(page.get("has_more", False))
                next_cursor = int(page.get("max_cursor", 0) or 0)
                if has_more and next_cursor == cursor:
                    logger.warning(
                        "%s %s cursor did not advance",
                        id_field,
                        entry_id,
                    )
                    break
                cursor = next_cursor

        return expanded

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

    @staticmethod
    def _normalize_page_data(data: Any) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return {"items": [], "has_more": False, "max_cursor": 0, "status_code": -1}

        if isinstance(data.get("items"), list):
            return {
                "items": data.get("items") or [],
                "has_more": bool(data.get("has_more")),
                "max_cursor": int(data.get("max_cursor", 0) or 0),
                "status_code": int(data.get("status_code", 0) or 0),
                "raw": data.get("raw", data),
                "risk_flags": data.get("risk_flags", {}),
            }

        raw_items = data.get("aweme_list") or []
        return {
            "items": raw_items if isinstance(raw_items, list) else [],
            "has_more": bool(data.get("has_more")),
            "max_cursor": int(data.get("max_cursor", 0) or 0),
            "status_code": int(data.get("status_code", 0) or 0),
            "raw": data,
            "risk_flags": {},
        }
