"""热榜 / 搜索数据采集模块。

仅负责数据落盘（JSONL），不下载媒体本体。用户拿到结果后可挑感兴趣的链接再丢进下载器。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import aiofiles

from utils.logger import setup_logger

if TYPE_CHECKING:  # pragma: no cover
    from core.api_client import DouyinAPIClient

logger = setup_logger("Discovery")


async def _write_jsonl(path: Path, items: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        for item in items:
            await f.write(json.dumps(item, ensure_ascii=False))
            await f.write("\n")


async def dump_hot_board(
    api_client: "DouyinAPIClient",
    output_dir: Path,
    *,
    limit: int = 0,
) -> Dict[str, Any]:
    """抓取抖音热搜榜并写入 output_dir/hot_board/{ts}.jsonl。

    Args:
        limit: 上限（0=全部）
    Returns:
        dict(items, path)
    """
    page = await api_client.get_hot_search_board()
    items = list(page.get("items") or [])
    if limit and limit > 0:
        items = items[:limit]

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = output_dir / "hot_board" / f"{ts}.jsonl"
    await _write_jsonl(out_path, items)
    logger.info("Hot board snapshot saved: %s items -> %s", len(items), out_path)
    return {"items": items, "path": str(out_path), "count": len(items)}


async def search_and_dump(
    api_client: "DouyinAPIClient",
    keyword: str,
    output_dir: Path,
    *,
    max_items: int = 50,
    page_size: int = 10,
    sort_type: int = 0,
    publish_time: int = 0,
    rate_limiter: Optional[Any] = None,
) -> Dict[str, Any]:
    """搜索作品并将结果写入 output_dir/search/{keyword}_{ts}.jsonl。

    Args:
        max_items: 最多累计条数（0=不限，建议设置以防失控）
    """
    accumulated: List[Dict[str, Any]] = []
    offset = 0
    seen_ids: set = set()

    while True:
        if rate_limiter is not None:
            try:
                await rate_limiter.acquire()
            except Exception:  # noqa: BLE001
                pass

        page = await api_client.search_aweme(
            keyword,
            offset=offset,
            count=page_size,
            sort_type=sort_type,
            publish_time=publish_time,
        )
        items = page.get("items") or []
        if not items:
            break

        for item in items:
            if not isinstance(item, dict):
                continue
            aweme_id = str(item.get("aweme_id") or "")
            if aweme_id in seen_ids:
                continue
            if aweme_id:
                seen_ids.add(aweme_id)
            accumulated.append(item)
            if 0 < max_items <= len(accumulated):
                break

        if 0 < max_items <= len(accumulated):
            break
        if not page.get("has_more"):
            break
        next_offset = int(page.get("max_cursor") or 0)
        if next_offset == offset:
            break
        offset = next_offset
        await asyncio.sleep(0.1)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_keyword = "".join(c if c.isalnum() else "_" for c in keyword)[:40] or "query"
    out_path = output_dir / "search" / f"{safe_keyword}_{ts}.jsonl"
    await _write_jsonl(out_path, accumulated)
    logger.info(
        "Search '%s' saved: %s items -> %s", keyword, len(accumulated), out_path
    )
    return {
        "keyword": keyword,
        "items": accumulated,
        "count": len(accumulated),
        "path": str(out_path),
    }
