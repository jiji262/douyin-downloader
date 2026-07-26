"""CommentsCollector 测试。"""

import json
from typing import Any, Dict, List

import pytest

from core.api_client import LoginRequiredError
from core.comments_collector import (
    CommentCollectionResult,
    CommentsCollector,
    ReplyFailure,
)
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


class _ReplyAPI:
    def __init__(self, roots, replies):
        self.roots = roots
        self.replies = replies
        self.reply_calls = []

    async def get_aweme_comments(self, aweme_id, *, cursor, count, include_replies):
        assert include_replies is False
        return {
            "items": self.roots,
            "has_more": False,
            "max_cursor": 0,
            "status_code": 0,
            "risk_flags": {"login_tip": False, "verify_page": False},
        }

    async def get_aweme_comment_replies(
        self, *, aweme_id, comment_id, cursor=0, count=20
    ):
        self.reply_calls.append((comment_id, cursor, count))
        value = self.replies[comment_id].pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def _reply_page(ids, *, has_more=False, cursor=0, verify=False):
    return {
        "items": [{"cid": item, "text": item} for item in ids],
        "has_more": has_more,
        "max_cursor": cursor,
        "status_code": 0,
        "risk_flags": {"login_tip": False, "verify_page": verify},
    }


def _reply_collector(api, *, per_parent=20, per_content=200, root_limit=100):
    return CommentsCollector(
        api,
        MetadataHandler(),
        include_replies=True,
        max_comments=root_limit,
        max_replies_per_comment=per_parent,
        max_replies_per_content=per_content,
        retry_delay_seconds=0,
    )


@pytest.mark.asyncio
async def test_collect_flattens_roots_and_replies_in_thread_order():
    api = _ReplyAPI(
        [
            {"cid": "root-1", "reply_comment_total": 2},
            {"cid": "root-2", "reply_comment_total": 0},
        ],
        {"root-1": [_reply_page(["reply-1", "reply-2"])]},
    )

    result = await _reply_collector(api).collect("video-1")

    assert isinstance(result, CommentCollectionResult)
    assert [row["cid"] for row in result.comments] == [
        "root-1",
        "reply-1",
        "reply-2",
        "root-2",
    ]
    assert [row["parent_comment_id"] for row in result.comments] == [
        "",
        "root-1",
        "root-1",
        "",
    ]
    assert result.top_level_comment_count == 2
    assert result.reply_comment_count == 2
    assert result.replies_truncated is False


@pytest.mark.asyncio
async def test_reply_limit_per_parent_is_normal_bounded_result():
    api = _ReplyAPI(
        [{"cid": "root-1", "reply_comment_total": 30}],
        {
            "root-1": [
                _reply_page(
                    [f"reply-{i}" for i in range(1, 21)],
                    has_more=True,
                    cursor=20,
                ),
                _reply_page([f"reply-{i}" for i in range(21, 31)]),
            ]
        },
    )

    result = await _reply_collector(api, per_parent=20).collect("video-1")

    assert result.reply_comment_count == 20
    assert result.replies_truncated is False
    assert api.reply_calls == [("root-1", 0, 20)]


@pytest.mark.asyncio
async def test_reply_cursor_stuck_marks_incomplete_without_dropping_rows():
    api = _ReplyAPI(
        [{"cid": "root-1", "reply_comment_total": 2}],
        {"root-1": [_reply_page(["reply-1"], has_more=True, cursor=0)]},
    )

    result = await _reply_collector(api).collect("video-1")

    assert [row["cid"] for row in result.comments] == ["root-1", "reply-1"]
    assert result.replies_truncated is True
    assert result.reply_failures == (
        ReplyFailure("video-1", "root-1", "reply_response_invalid"),
    )


@pytest.mark.asyncio
async def test_reply_login_failure_preserves_other_threads():
    api = _ReplyAPI(
        [
            {"cid": "root-1", "reply_comment_total": 1},
            {"cid": "root-2", "reply_comment_total": 1},
        ],
        {
            "root-1": [LoginRequiredError(2483, "login", "/reply/")],
            "root-2": [_reply_page(["reply-2"])],
        },
    )

    result = await _reply_collector(api).collect("video-1")

    assert [row["cid"] for row in result.comments] == [
        "root-1",
        "root-2",
        "reply-2",
    ]
    assert result.reply_failures == (
        ReplyFailure("video-1", "root-1", "reply_login_required"),
    )


@pytest.mark.asyncio
async def test_collect_and_save_serializes_safe_reply_metadata(tmp_path):
    api = _ReplyAPI(
        [{"cid": "root-1", "reply_comment_total": 1}],
        {"root-1": [RuntimeError("Cookie=sessionid=secret Authorization=Bearer x")]},
    )
    collector = _reply_collector(api)

    payload = await collector.collect_and_save("video-1", tmp_path / "comments.json")

    assert payload["replies_truncated"] is True
    assert payload["reply_failures"][0]["error_code"] == "reply_api_failed"
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "sessionid" not in serialized.lower()
    assert "authorization" not in serialized.lower()
