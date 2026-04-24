"""热榜 / 搜索落盘模块测试。"""

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from core.discovery import dump_hot_board, search_and_dump


class _FakeAPIClient:
    def __init__(
        self,
        hot_items: List[Dict[str, Any]] | None = None,
        search_pages: List[Dict[str, Any]] | None = None,
    ):
        self._hot_items = hot_items or []
        self._search_pages = list(search_pages or [])
        self.search_calls: List[Dict[str, Any]] = []

    async def get_hot_search_board(self) -> Dict[str, Any]:
        return {
            "items": self._hot_items,
            "has_more": False,
            "max_cursor": 0,
        }

    async def search_aweme(
        self, keyword, *, offset, count, sort_type=0, publish_time=0
    ):
        self.search_calls.append(
            {"keyword": keyword, "offset": offset, "count": count}
        )
        if not self._search_pages:
            return {"items": [], "has_more": False, "max_cursor": offset}
        return self._search_pages.pop(0)


@pytest.mark.asyncio
async def test_dump_hot_board_writes_jsonl(tmp_path):
    api = _FakeAPIClient(
        hot_items=[{"word": "foo", "hot_value": 100}, {"word": "bar", "hot_value": 50}]
    )
    result = await dump_hot_board(api, tmp_path)
    assert result["count"] == 2
    out = Path(result["path"])
    assert out.exists()
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["word"] == "foo"


@pytest.mark.asyncio
async def test_dump_hot_board_respects_limit(tmp_path):
    api = _FakeAPIClient(
        hot_items=[{"word": f"w{i}"} for i in range(20)]
    )
    result = await dump_hot_board(api, tmp_path, limit=5)
    assert result["count"] == 5


@pytest.mark.asyncio
async def test_search_and_dump_accumulates_pages(tmp_path):
    api = _FakeAPIClient(
        search_pages=[
            {
                "items": [{"aweme_id": "1"}, {"aweme_id": "2"}],
                "has_more": True,
                "max_cursor": 2,
            },
            {
                "items": [{"aweme_id": "3"}, {"aweme_id": "2"}],  # dup
                "has_more": False,
                "max_cursor": 4,
            },
        ]
    )
    result = await search_and_dump(api, "cat", tmp_path, max_items=0)
    assert result["count"] == 3
    assert {"1", "2", "3"} == {c["aweme_id"] for c in result["items"]}


@pytest.mark.asyncio
async def test_search_respects_max_items(tmp_path):
    api = _FakeAPIClient(
        search_pages=[
            {"items": [{"aweme_id": str(i)} for i in range(5)], "has_more": True, "max_cursor": 5},
            {"items": [{"aweme_id": str(i)} for i in range(5, 10)], "has_more": False, "max_cursor": 10},
        ]
    )
    result = await search_and_dump(api, "cat", tmp_path, max_items=3, page_size=5)
    assert result["count"] == 3


@pytest.mark.asyncio
async def test_search_stops_on_stuck_cursor(tmp_path):
    api = _FakeAPIClient(
        search_pages=[
            {"items": [{"aweme_id": "1"}], "has_more": True, "max_cursor": 0},
        ]
        * 10
    )
    result = await search_and_dump(api, "cat", tmp_path, max_items=0)
    # 第一页 cursor 未推进应立即停止
    assert len(api.search_calls) == 1
    assert result["count"] == 1
