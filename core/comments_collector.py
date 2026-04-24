"""评论采集：针对单个作品拉取全部评论（可选含二级回复），导出为 JSON。

设计要点：
- 复用 DouyinAPIClient 的分页请求与签名
- 与下载流程解耦：作为独立的 helper，由 BaseDownloader 在保存媒体后按需调用
- 输出位置：与媒体同目录，文件名 `{file_stem}_comments.json`
- 支持上限 max_comments（默认 0 = 不限）和 include_replies
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from utils.logger import setup_logger

if TYPE_CHECKING:  # pragma: no cover
    from core.api_client import DouyinAPIClient
    from storage.metadata_handler import MetadataHandler

logger = setup_logger("CommentsCollector")


class CommentsCollector:
    def __init__(
        self,
        api_client: "DouyinAPIClient",
        metadata_handler: "MetadataHandler",
        *,
        include_replies: bool = False,
        max_comments: int = 0,
        page_size: int = 20,
        retry_delay_seconds: float = 1.0,
    ):
        self.api_client = api_client
        self.metadata_handler = metadata_handler
        self.include_replies = include_replies
        self.max_comments = int(max_comments or 0)
        self.page_size = max(1, int(page_size or 20))
        self.retry_delay_seconds = float(retry_delay_seconds or 1.0)

    async def collect_and_save(
        self, aweme_id: str, output_path: Path
    ) -> Optional[Dict[str, Any]]:
        """抓取评论并写入 output_path，失败时返回 None。"""
        comments = await self.collect(aweme_id)
        if comments is None:
            return None

        payload = {
            "aweme_id": aweme_id,
            "count": len(comments),
            "include_replies": self.include_replies,
            "comments": comments,
        }
        # MetadataHandler.save_metadata 内部已吞异常并返回 bool
        saved = await self.metadata_handler.save_metadata(payload, output_path)
        if not saved:
            logger.warning(
                "Failed to save comments for %s to %s", aweme_id, output_path
            )
            return None
        return payload

    async def collect(self, aweme_id: str) -> Optional[List[Dict[str, Any]]]:
        """抓取评论列表（不写盘），失败返回 None。"""
        all_comments: List[Dict[str, Any]] = []
        cursor = 0
        seen_ids: set = set()

        while True:
            try:
                page = await self.api_client.get_aweme_comments(
                    aweme_id,
                    cursor=cursor,
                    count=self.page_size,
                    include_replies=self.include_replies,
                )
            except Exception as exc:
                logger.warning(
                    "Comments fetch error for %s cursor=%s: %s",
                    aweme_id,
                    cursor,
                    exc,
                )
                return None

            items = page.get("items") or []
            if not items:
                break

            for item in items:
                if not isinstance(item, dict):
                    continue
                cid = item.get("cid") or item.get("comment_id")
                key = str(cid) if cid else None
                if key and key in seen_ids:
                    continue
                if key:
                    seen_ids.add(key)
                all_comments.append(item)
                if 0 < self.max_comments <= len(all_comments):
                    return all_comments[: self.max_comments]

            if not page.get("has_more"):
                break
            next_cursor = page.get("max_cursor") or 0
            if next_cursor == cursor:
                # cursor 未推进但服务器称 has_more=True：可能是接口变更或异常返回，
                # 升级为 warning 便于线上观察。
                logger.warning(
                    "Comments cursor stuck (aweme=%s, cursor=%s, has_more=True); "
                    "stopping to avoid infinite loop.",
                    aweme_id,
                    cursor,
                )
                break
            cursor = next_cursor
            await asyncio.sleep(self.retry_delay_seconds * 0.1)  # 轻度节流

        return all_comments
