"""CommentsCollector 测试。"""

import json
from typing import Any, Dict, List

import pytest

from core.api_client import DouyinAPIClient
from core.comments_collector import (
    CommentsCollector,
    CommentsSaveResult,
    needs_backfill,
    read_comments_state,
)
from storage.metadata_handler import MetadataHandler


def _api_page(raw: Dict[str, Any]) -> Dict[str, Any]:
    """按真实接口的归一化逻辑构造一页，失败页和空页的形状都与线上一致。"""
    return DouyinAPIClient._normalize_paged_response(raw, item_keys=["comments"])


# _request_json 碰到风控空 200 / 非 JSON / 终态 4xx 时返回 {}，不抛异常。
_FAILED_PAGE = _api_page({})


class _FakeAPIClient:
    def __init__(self, pages: List[Dict[str, Any]], replies=None):
        self._pages = list(pages)
        self._replies = dict(replies or {})
        self.call_count = 0
        self.reply_calls: List[str] = []

    async def get_aweme_comments(self, aweme_id, *, cursor, count, include_replies):
        self.call_count += 1
        assert include_replies is False
        if not self._pages:
            return {"items": [], "has_more": False, "max_cursor": cursor}
        return self._pages.pop(0)

    async def get_aweme_comment_replies(self, *, aweme_id, comment_id, count):
        self.reply_calls.append(comment_id)
        return self._replies[comment_id]


class _CountingLimiter:
    def __init__(self):
        self.acquired = 0

    async def acquire(self):
        self.acquired += 1


async def _save(pages, out, **kwargs):
    collector = CommentsCollector(_FakeAPIClient(pages), MetadataHandler(), **kwargs)
    return await collector.collect_and_save("X1", out)


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_comments_file(path, comments, complete=None):
    payload = {"aweme_id": "X", "count": len(comments), "include_replies": False}
    if complete is not None:
        payload["complete"] = complete
    payload["comments"] = comments
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


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
    outcome = await collector.collect_and_save("A1", out)
    assert outcome.saved and outcome.complete and outcome.fetched
    assert outcome.count == 3
    data = _read(out)
    assert data["aweme_id"] == "A1"
    assert data["complete"] is True
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
    outcome = await collector.collect_and_save("B1", out)
    assert outcome.complete
    assert outcome.count == 3
    assert _read(out)["count"] == 3


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
    await collector.collect_and_save("C1", out)
    cids = [c["cid"] for c in _read(out)["comments"]]
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
    outcome = await collector.collect_and_save("D1", out)
    assert outcome.saved
    # 第一页后 cursor 未推进，应立即停止
    assert api.call_count == 1


@pytest.mark.asyncio
async def test_collector_does_not_write_when_request_raises(tmp_path):
    class _FlakyAPI:
        async def get_aweme_comments(self, *args, **kwargs):
            raise RuntimeError("boom")

    collector = CommentsCollector(_FlakyAPI(), MetadataHandler())
    out = tmp_path / "out.json"
    outcome = await collector.collect_and_save("E1", out)
    assert not outcome.saved and not outcome.complete and not outcome.fetched
    assert not out.exists()


@pytest.mark.asyncio
async def test_failed_first_page_is_not_saved_as_empty_comments(tmp_path):
    # 2026-11 用户反馈：风控空 200 被存成 comments=[] 的文件，作品还算成功。
    out = tmp_path / "out.json"
    outcome = await _save([_FAILED_PAGE], out)
    assert outcome == CommentsSaveResult(fetched=False, complete=False, saved=False, count=0)
    assert not out.exists()

    _write_comments_file(out, [{"cid": "1"}], complete=True)
    before = out.read_bytes()
    outcome = await _save([_FAILED_PAGE], out)
    # 盘上已有的完整评论原样保留，所以此刻仍算完整。
    assert outcome == CommentsSaveResult(fetched=False, complete=True, saved=False, count=0)
    assert out.read_bytes() == before


@pytest.mark.asyncio
async def test_partial_result_is_marked_incomplete_and_keeps_fuller_files(tmp_path):
    def _pages():
        return [
            {"items": [{"cid": "1"}, {"cid": "2"}], "has_more": True, "max_cursor": 2},
            _FAILED_PAGE,
        ]

    out = tmp_path / "out.json"
    outcome = await _save(_pages(), out)
    assert outcome == CommentsSaveResult(fetched=False, complete=False, saved=True, count=2)
    data = _read(out)
    assert data["complete"] is False
    assert [c["cid"] for c in data["comments"]] == ["1", "2"]

    for existing, complete in (([{"cid": "9"}], True), ([{"cid": str(i)} for i in range(5)], None)):
        _write_comments_file(out, existing, complete=complete)
        before = out.read_bytes()
        outcome = await _save(_pages(), out)
        assert not outcome.saved
        assert out.read_bytes() == before


@pytest.mark.asyncio
async def test_zero_comment_response_is_saved_as_complete(tmp_path):
    page = _api_page({"status_code": 0, "comments": None, "has_more": 0, "cursor": 0})
    out = tmp_path / "out.json"
    outcome = await _save([page], out)
    assert outcome == CommentsSaveResult(fetched=True, complete=True, saved=True, count=0)
    data = _read(out)
    assert data["complete"] is True
    assert data["comments"] == []


@pytest.mark.asyncio
async def test_empty_response_does_not_overwrite_saved_comments(tmp_path):
    out = tmp_path / "out.json"
    _write_comments_file(out, [{"cid": "1"}, {"cid": "2"}], complete=True)
    before = out.read_bytes()
    page = _api_page({"status_code": 0, "comments": [], "has_more": 0, "cursor": 0})
    outcome = await _save([page], out)
    assert outcome == CommentsSaveResult(fetched=True, complete=True, saved=False, count=0)
    assert out.read_bytes() == before


@pytest.mark.asyncio
async def test_every_page_and_reply_request_goes_through_rate_limiter(tmp_path):
    api = _FakeAPIClient(
        [
            {
                "items": [{"cid": "1", "reply_comment_total": 2}, {"cid": "2"}],
                "has_more": True,
                "max_cursor": 2,
            },
            {"items": [{"cid": "3"}], "has_more": False, "max_cursor": 3},
        ],
        replies={"1": _api_page({"status_code": 0, "comments": [{"cid": "r1"}]})},
    )
    limiter = _CountingLimiter()
    collector = CommentsCollector(
        api, MetadataHandler(), include_replies=True, rate_limiter=limiter
    )
    out = tmp_path / "out.json"
    outcome = await collector.collect_and_save("J1", out)
    assert outcome.complete
    assert api.reply_calls == ["1"]
    assert limiter.acquired == 3  # 两页评论 + 一次回复
    assert _read(out)["comments"][0]["_replies"] == [{"cid": "r1"}]


@pytest.mark.asyncio
async def test_failed_reply_request_marks_comments_incomplete(tmp_path):
    api = _FakeAPIClient(
        [{"items": [{"cid": "1", "reply_comment_total": 3}], "has_more": False}],
        replies={"1": _FAILED_PAGE},
    )
    collector = CommentsCollector(api, MetadataHandler(), include_replies=True)
    out = tmp_path / "out.json"
    outcome = await collector.collect_and_save("K1", out)
    assert not outcome.fetched and not outcome.complete
    data = _read(out)
    assert data["complete"] is False
    assert "_replies" not in data["comments"][0]


@pytest.mark.asyncio
async def test_read_comments_state_decides_backfill(tmp_path):
    missing = await read_comments_state(tmp_path / "missing.json")
    assert needs_backfill(missing)

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text('{"comments": [', encoding="utf-8")
    assert needs_backfill(await read_comments_state(corrupt))

    cases = [
        # 旧版本的失败产物：没有 complete 字段、0 条，分不清是不是真没评论。
        ([], None, True),
        ([{"cid": "1"}], None, False),
        ([{"cid": "1"}], False, True),
        ([], True, False),
    ]
    for index, (comments, complete, expected) in enumerate(cases):
        path = tmp_path / "case_{0}.json".format(index)
        _write_comments_file(path, comments, complete=complete)
        state = await read_comments_state(path)
        assert state.count == len(comments)
        assert needs_backfill(state) is expected, (comments, complete)


@pytest.mark.asyncio
async def test_read_comments_state_reads_large_files_from_header(tmp_path):
    comments = [{"cid": str(i), "text": "评论" * 20} for i in range(800)]
    for complete in (True, False):
        out = tmp_path / "large_{0}.json".format(complete)
        assert await CommentsCollector(None, MetadataHandler())._write(
            "L1", comments, complete, out
        )
        assert out.stat().st_size > 64 * 1024
        state = await read_comments_state(out)
        assert (state.exists, state.complete, state.count) == (True, complete, 800)
