"""CommentsCollector 测试。"""

import json
from typing import Any, Dict, List

import pytest

from core.comments_collector import CommentsCollector
from storage.metadata_handler import MetadataHandler


class _FakeAPIClient:
    def __init__(self, pages: List[Dict[str, Any]]):
        self._pages = list(pages)
        self.call_count = 0

    async def get_aweme_comments(self, aweme_id, *, cursor, count, include_replies):
        self.call_count += 1
        if not self._pages:
            return {"items": [], "has_more": False, "max_cursor": cursor}
        return self._pages.pop(0)


class _ReplyAPIClient:
    def __init__(self, reply_page=None, reply_error=None):
        self.reply_page = reply_page
        self.reply_error = reply_error
        self.reply_calls = []

    async def get_aweme_comments(self, aweme_id, *, cursor, count, include_replies):
        assert include_replies is False
        return {
            "items": [
                {
                    "cid": "root-1",
                    "text": "root",
                    "reply_comment_total": 1,
                }
            ],
            "has_more": False,
            "max_cursor": 0,
        }

    async def get_aweme_comment_replies(self, *, aweme_id, comment_id, cursor, count):
        self.reply_calls.append((aweme_id, comment_id, cursor, count))
        if self.reply_error is not None:
            raise self.reply_error
        return self.reply_page


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


@pytest.mark.asyncio
async def test_collector_flattens_api_replies_with_parent_id(tmp_path):
    api = _ReplyAPIClient(
        reply_page={
            "items": [{"cid": "reply-1", "text": "reply"}],
            "has_more": False,
            "max_cursor": 0,
        }
    )
    collector = CommentsCollector(
        api,
        MetadataHandler(),
        include_replies=True,
        max_replies_per_comment=20,
        max_replies_per_content=200,
    )

    payload = await collector.collect_and_save("A1", tmp_path / "out.json")

    assert payload is not None
    assert [item["cid"] for item in payload["comments"]] == ["root-1", "reply-1"]
    assert payload["comments"][0]["parent_comment_id"] == ""
    assert payload["comments"][1]["parent_comment_id"] == "root-1"
    assert payload["reply_api_attempted"] == 1
    assert payload["reply_browser_fallback_attempted"] == 0


@pytest.mark.asyncio
async def test_collector_uses_browser_fallback_after_reply_api_failure(tmp_path):
    class _ReplyFailure(RuntimeError):
        error_code = "reply_response_invalid"

    api = _ReplyAPIClient(reply_error=_ReplyFailure("empty"))
    fallback_calls = []

    async def _fallback(aweme_id, content_url, failed, per_comment, remaining):
        fallback_calls.append((aweme_id, content_url, failed, per_comment, remaining))
        return {
            "replies": [
                {
                    "cid": "reply-1",
                    "text": "browser reply",
                    "parent_comment_id": "root-1",
                }
            ]
        }

    collector = CommentsCollector(
        api,
        MetadataHandler(),
        include_replies=True,
        max_replies_per_comment=20,
        max_replies_per_content=200,
        reply_browser_fallback=_fallback,
    )
    collector.set_content_url("A1", "https://www.douyin.com/video/123")

    payload = await collector.collect_and_save("A1", tmp_path / "out.json")

    assert payload is not None
    assert [item["cid"] for item in payload["comments"]] == ["root-1", "reply-1"]
    assert fallback_calls[0][0:2] == ("A1", "https://www.douyin.com/video/123")
    assert fallback_calls[0][2][0]["error_code"] == "reply_response_invalid"
    assert payload["reply_browser_fallback_attempted"] == 1
    assert payload["reply_browser_fallback_succeeded"] == 1


@pytest.mark.asyncio
async def test_collector_keeps_top_level_when_reply_fallback_fails(tmp_path):
    class _ReplyFailure(RuntimeError):
        error_code = "reply_login_required"

    api = _ReplyAPIClient(reply_error=_ReplyFailure("login"))

    async def _fallback(*_args):
        return {"failures": [{"error_code": "reply_login_required"}]}

    collector = CommentsCollector(
        api,
        MetadataHandler(),
        include_replies=True,
        max_replies_per_comment=20,
        max_replies_per_content=200,
        reply_browser_fallback=_fallback,
    )

    payload = await collector.collect_and_save("A1", tmp_path / "out.json")

    assert payload is not None
    assert [item["cid"] for item in payload["comments"]] == ["root-1"]
    assert payload["reply_failures"] == [{"error_code": "reply_login_required"}]
    assert payload["reply_browser_fallback_failed"] == 1


@pytest.mark.asyncio
async def test_collector_deduplicates_reply_ids_and_honors_limits(tmp_path):
    class _PagedReplyAPI(_ReplyAPIClient):
        def __init__(self):
            super().__init__()
            self.pages = [
                {
                    "items": [{"cid": "reply-1"}, {"cid": "reply-2"}],
                    "has_more": True,
                    "max_cursor": 2,
                },
                {
                    "items": [{"cid": "reply-2"}, {"cid": "reply-3"}],
                    "has_more": False,
                    "max_cursor": 3,
                },
            ]

        async def get_aweme_comment_replies(self, **kwargs):
            self.reply_calls.append(kwargs)
            return self.pages.pop(0)

    api = _PagedReplyAPI()
    collector = CommentsCollector(
        api,
        MetadataHandler(),
        include_replies=True,
        max_replies_per_comment=2,
        max_replies_per_content=2,
    )

    payload = await collector.collect_and_save("A1", tmp_path / "out.json")

    assert payload is not None
    assert [item["cid"] for item in payload["comments"]] == ["root-1", "reply-1", "reply-2"]
    assert payload["replies_truncated"] is True
