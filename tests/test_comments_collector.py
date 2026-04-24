"""CommentsCollector 测试。"""

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from core.comments_collector import CommentsCollector
from storage.metadata_handler import MetadataHandler


class _FakeAPIClient:
    def __init__(self, pages: List[Dict[str, Any]]):
        self._pages = list(pages)
        self.call_count = 0

    async def get_aweme_comments(
        self, aweme_id, *, cursor, count, include_replies
    ):
        self.call_count += 1
        if not self._pages:
            return {"items": [], "has_more": False, "max_cursor": cursor}
        return self._pages.pop(0)


@pytest.mark.asyncio
async def test_collector_paginates_until_no_more(tmp_path):
    api = _FakeAPIClient(
        [
            {
                "items": [{"cid": "1", "text": "a"}, {"cid": "2", "text": "b"}],
                "has_more": True,
                "max_cursor": 10,
            },
            {
                "items": [{"cid": "3", "text": "c"}],
                "has_more": False,
                "max_cursor": 20,
            },
        ]
    )
    collector = CommentsCollector(api, MetadataHandler())
    out = tmp_path / "out.json"
    payload = await collector.collect_and_save("A1", out)
    assert payload is not None
    assert payload["count"] == 3
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["aweme_id"] == "A1"
    assert len(data["comments"]) == 3


@pytest.mark.asyncio
async def test_collector_respects_max_comments(tmp_path):
    api = _FakeAPIClient(
        [
            {
                "items": [{"cid": str(i)} for i in range(5)],
                "has_more": True,
                "max_cursor": 5,
            },
            {
                "items": [{"cid": str(i)} for i in range(5, 10)],
                "has_more": False,
                "max_cursor": 10,
            },
        ]
    )
    collector = CommentsCollector(api, MetadataHandler(), max_comments=3)
    out = tmp_path / "out.json"
    payload = await collector.collect_and_save("B1", out)
    assert payload is not None
    assert payload["count"] == 3


@pytest.mark.asyncio
async def test_collector_deduplicates_by_cid(tmp_path):
    api = _FakeAPIClient(
        [
            {
                "items": [{"cid": "1"}, {"cid": "2"}, {"cid": "1"}],
                "has_more": False,
                "max_cursor": 3,
            }
        ]
    )
    collector = CommentsCollector(api, MetadataHandler())
    out = tmp_path / "out.json"
    payload = await collector.collect_and_save("C1", out)
    assert payload is not None
    cids = [c["cid"] for c in payload["comments"]]
    assert cids == ["1", "2"]


@pytest.mark.asyncio
async def test_collector_stops_when_cursor_stuck(tmp_path):
    # 模拟 cursor 一直未推进、has_more=True 的病态场景，防止死循环。
    same_cursor_page = {
        "items": [{"cid": "1"}],
        "has_more": True,
        "max_cursor": 0,
    }
    api = _FakeAPIClient([same_cursor_page] * 10)
    collector = CommentsCollector(api, MetadataHandler())
    out = tmp_path / "out.json"
    payload = await collector.collect_and_save("D1", out)
    assert payload is not None
    # 第一页后 cursor 未推进，应立即停止
    assert api.call_count == 1


@pytest.mark.asyncio
async def test_collector_returns_none_on_api_error(tmp_path):
    class _FlakyAPI:
        async def get_aweme_comments(self, *args, **kwargs):
            raise RuntimeError("boom")

    collector = CommentsCollector(_FlakyAPI(), MetadataHandler())
    out = tmp_path / "out.json"
    payload = await collector.collect_and_save("E1", out)
    assert payload is None
    assert not out.exists()
