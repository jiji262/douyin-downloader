"""`search` 的实现：翻页、投影、区分「确实没有结果」和「请求失败或被拦」。"""

import argparse
import asyncio
import datetime
from typing import Any, Dict, List, Optional

from agent_mode import state
from agent_mode.discovery_common import (
    EMPTY_HINT,
    ID_MAX_CHARS,
    as_dict,
    as_int,
    open_client,
    upstream_empty,
)
from agent_mode.envelope import DESC_MAX_CHARS, NAME_MAX_CHARS, AgentError, clean_text, retry_action

SEARCH_DEFAULT_MAX = 20
SEARCH_MAX_LIMIT = 50
# 与 DouyinAPIClient.search_aweme 的 sort_type / publish_time 取值对应
SEARCH_SORT_TYPES = {"general": 0, "likes": 1, "latest": 2}
SEARCH_PUBLISH_DAYS = (0, 1, 7, 182)
SEARCH_PAGE_SIZE = 10
# 翻页间隔比人用 CLI 保守，避免 agent 连续翻页触发风控
SEARCH_PAGE_INTERVAL_S = 1.0
SEARCH_RETRY_AFTER_S = 120
KEYWORD_MAX_CHARS = 100
MS_PER_SECOND = 1000
DURATION_PRECISION = 1
ISO_UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
UPSTREAM_OK_STATUS = 0
# 状态码为 0 但其实被风控拦下时，响应里 search_nil_info.search_nil_type 的取值
BLOCKED_NIL_TYPES = ("verify_check", "hit_shark")
# 与 core/downloader_base.py 的 BaseDownloader._GALLERY_AWEME_TYPES 一致（有漂移测试）
GALLERY_AWEME_TYPES = (2, 68, 150)
# media_type -> 分享链接的路径段
SHARE_URL_TEMPLATE = "https://www.douyin.com/%s/%s"
SHARE_URL_SEGMENTS = {"video": "video", "gallery": "note"}
_STAT_FIELDS = ("digg", "comment", "share", "collect")


def _media_type(raw: Dict[str, Any]) -> str:
    image_post = as_dict(raw.get("image_post_info"))
    has_images = raw.get("images") or image_post.get("images") or image_post.get("image_list")
    return "gallery" if has_images or raw.get("aweme_type") in GALLERY_AWEME_TYPES else "video"


def _iso_utc(epoch: Any) -> Optional[str]:
    seconds = as_int(epoch) or 0
    try:
        moment = datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return moment.strftime(ISO_UTC_FORMAT) if seconds > 0 else None


def _duration_s(raw: Dict[str, Any]) -> Optional[float]:
    millis = as_int(as_dict(raw.get("video")).get("duration")) or as_int(raw.get("duration"))
    return round(millis / MS_PER_SECOND, DURATION_PRECISION) if millis and millis > 0 else None


def _project_aweme(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """只挑契约里的字段；video / images 等带直链的子树整个不输出。"""
    aweme_id = clean_text(raw.get("aweme_id"), ID_MAX_CHARS)
    if not aweme_id.isdigit():
        return None
    author = as_dict(raw.get("author"))
    stats = as_dict(raw.get("statistics"))
    media_type = _media_type(raw)
    return {
        "aweme_id": aweme_id,
        "desc": clean_text(raw.get("desc"), DESC_MAX_CHARS),
        "author": {
            "nickname": clean_text(author.get("nickname"), NAME_MAX_CHARS),
            "sec_uid": clean_text(author.get("sec_uid"), ID_MAX_CHARS),
        },
        "stats": {name: as_int(stats.get(name + "_count")) for name in _STAT_FIELDS},
        "create_time": _iso_utc(raw.get("create_time")),
        "duration_s": _duration_s(raw),
        "media_type": media_type,
        "share_url": SHARE_URL_TEMPLATE % (SHARE_URL_SEGMENTS[media_type], aweme_id),
    }


def _absorb_page(page: Dict[str, Any], items: List[Dict[str, Any]], limit: int) -> bool:
    """把一页结果去重后并入 items；返回这一页是否还有没放进去的新条目。"""
    seen = {item["aweme_id"] for item in items}
    for raw in page.get("items") or []:
        projected = _project_aweme(as_dict(raw))
        if projected is None or projected["aweme_id"] in seen:
            continue
        if len(items) >= limit:
            return True
        seen.add(projected["aweme_id"])
        items.append(projected)
    return False


class _PageFailed(Exception):
    """某一页请求失败或被拦；调用方按已拿到的条数决定错误码。"""


def _is_failed_page(page: Dict[str, Any]) -> bool:
    """空页是请求失败或被拦，而不是确实没有结果（或自然翻到底）。

    只有上游明确报成功（响应体里 status_code 为 0）且没有风控标记，才算真的没有结果。
    """
    if page.get("items"):
        return False
    raw = as_dict(page.get("raw"))
    if page.get("status_code") or as_int(raw.get("status_code")) != UPSTREAM_OK_STATUS:
        return True
    nil_type = as_dict(raw.get("search_nil_info")).get("search_nil_type")
    return nil_type in BLOCKED_NIL_TYPES


async def _collect_search(client: Any, args: argparse.Namespace, items: List[Any]) -> bool:
    """翻页直到凑够 args.max 或到底，结果就地写进 items；返回 has_more。"""
    offset = 0
    while True:
        page = await client.search_aweme(
            args.keyword,
            offset=offset,
            count=SEARCH_PAGE_SIZE,
            sort_type=SEARCH_SORT_TYPES[args.sort],
            publish_time=args.publish_days,
        )
        if _is_failed_page(page):
            raise _PageFailed("empty response at offset %d" % offset)
        leftover = _absorb_page(page, items, args.max)
        has_more = bool(page.get("has_more"))
        if len(items) >= args.max:
            return leftover or has_more
        next_offset = as_int(page.get("max_cursor")) or 0
        if not has_more or not page.get("items") or next_offset == offset:
            return False
        offset = next_offset
        await asyncio.sleep(SEARCH_PAGE_INTERVAL_S)


def _search_data(keyword: str, items: List[Dict[str, Any]], has_more: bool) -> Dict[str, Any]:
    keyword = clean_text(keyword, KEYWORD_MAX_CHARS)
    return {"keyword": keyword, "items": items, "count": len(items), "has_more": has_more}


def _search_failure(args: argparse.Namespace, items: List[Any], reason: str) -> AgentError:
    if not items:
        empty = _search_data(args.keyword, [], False)
        return upstream_empty("Search returned no items.", SEARCH_RETRY_AFTER_S, empty)
    return AgentError(
        "PARTIAL",
        "Search stopped after %d of %d requested items (%s)." % (len(items), args.max, reason),
        action=retry_action(SEARCH_RETRY_AFTER_S, EMPTY_HINT),
        data=_search_data(args.keyword, items, True),
    )


async def run_search(args: argparse.Namespace) -> Dict[str, Any]:
    cookies = state.read_cookies()
    if state.login_status(cookies) != state.LOGIN_STATUS_LOGGED_IN:
        # 没有会话 cookie 时搜索必然被拒，不构造 client，也不发请求
        raise AgentError("LOGIN_REQUIRED", "Search needs a logged-in session; none is saved.")

    from core.api_client import LoginRequiredError

    items: List[Dict[str, Any]] = []
    try:
        async with open_client(cookies) as client:
            has_more = await _collect_search(client, args, items)
    except LoginRequiredError:
        partial = _search_data(args.keyword, items, True) if items else None
        message = "Douyin rejected the saved session for search."
        raise AgentError("LOGIN_REQUIRED", message, data=partial) from None
    except _PageFailed as exc:
        raise _search_failure(args, items, str(exc)) from None
    except Exception as exc:  # noqa: BLE001 — 已有部分结果时带着返回，否则交给入口报 INTERNAL
        if not items:
            raise
        raise _search_failure(args, items, type(exc).__name__) from None
    # 走到这里而 items 为空：上游明确成功，只是这个关键词没有结果
    return _search_data(args.keyword, items, has_more)
