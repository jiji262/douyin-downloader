"""`hot`、`search`、`user`：联网只读 verb，只输出投影字段，绝不含媒体直链。接口见 entry.py。

`search` 的实现在 discovery_search.py，这里只负责注册。
"""

import argparse
import re
from typing import Any, Dict
from urllib.parse import quote

from agent_mode import discovery_search, state
from agent_mode.capabilities import URL_TYPE_NOTES
from agent_mode.discovery_common import (
    ID_MAX_CHARS,
    RETRY_AFTER_S,
    as_dict,
    as_int,
    open_client,
    upstream_empty,
)
from agent_mode.entry import bounded_int
from agent_mode.envelope import DESC_MAX_CHARS, NAME_MAX_CHARS, AgentError, clean_text

HOT_DEFAULT_MAX = 50
HOT_MAX_LIMIT = 100
SEARCH_URL_PREFIX = "https://www.douyin.com/search/"
PUBLISH_DAYS_HELP = "only posts published within N days (0 = any time)"
# sec_uid 都以这个前缀开头；抖音号、数字 uid 等其他裸 id 不能拿去查主页
SEC_UID_PREFIX = "MS4wLjAB"
SEC_UID_HINT = "pass the profile URL or a sec_uid (starts with %s)" % SEC_UID_PREFIX
_SEC_UID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_HOT_OPTIONAL_FIELDS = ("label", "video_count")
_USER_STAT_FIELDS = ("follower_count", "following_count", "aweme_count", "total_favorited")


def register(subparsers: Any) -> None:
    hot = subparsers.add_parser("hot", help="list the Douyin hot-search board")
    hot.add_argument(
        "--max", type=bounded_int(1, HOT_MAX_LIMIT), default=HOT_DEFAULT_MAX, metavar="N"
    )
    hot.set_defaults(handler=run_hot)
    search = subparsers.add_parser("search", help="search posts by keyword (needs login)")
    search.add_argument("keyword")
    search.add_argument(
        "--max",
        type=bounded_int(1, discovery_search.SEARCH_MAX_LIMIT),
        default=discovery_search.SEARCH_DEFAULT_MAX,
        metavar="N",
    )
    search.add_argument(
        "--sort", choices=sorted(discovery_search.SEARCH_SORT_TYPES), default="general"
    )
    search.add_argument(
        "--publish-days",
        type=int,
        choices=discovery_search.SEARCH_PUBLISH_DAYS,
        default=0,
        help=PUBLISH_DAYS_HELP,
    )
    search.set_defaults(handler=discovery_search.run_search)
    user = subparsers.add_parser("user", help="show a user's public profile (needs cookies)")
    user.add_argument("target", help="profile URL or sec_uid")
    user.set_defaults(handler=run_user)


def _project_hot_item(raw: Dict[str, Any], rank: int) -> Dict[str, Any]:
    word = clean_text(raw.get("word"), NAME_MAX_CHARS)
    item = {"rank": rank, "word": word, "hot_value": as_int(raw.get("hot_value"))}
    for field in _HOT_OPTIONAL_FIELDS:
        value = as_int(raw.get(field))
        if value:
            item[field] = value
    item["search_url"] = SEARCH_URL_PREFIX + quote(word, safe="")
    return item


async def run_hot(args: argparse.Namespace) -> Dict[str, Any]:
    from core.api_client import LoginRequiredError

    try:
        async with open_client(state.read_cookies()) as client:
            page = await client.get_hot_search_board()
    except LoginRequiredError:
        raise AgentError("LOGIN_REQUIRED", "Douyin asked for a login for the hot board.") from None
    rows = [raw for raw in page.get("items") or [] if clean_text(as_dict(raw).get("word"))]
    items = [_project_hot_item(raw, rank) for rank, raw in enumerate(rows[: args.max], 1)]
    if not items:
        empty = {"items": [], "count": 0}
        raise upstream_empty("Douyin returned an empty hot board.", RETRY_AFTER_S, empty)
    return {"items": items, "count": len(items)}


def _not_a_profile() -> AgentError:
    action = {"kind": "fix_usage", "hint": SEC_UID_HINT}
    return AgentError("USAGE", "Expected a Douyin profile URL or a sec_uid.", action=action)


async def _resolve_sec_uid(target: str) -> str:
    """裸 sec_uid 直接用；否则按 URL（含短链、分享文案）解析出 sec_uid。"""
    from agent_mode.inspect_cmd import resolve_input_url
    from core.url_parser import URLParser

    bare = target.strip()
    if _SEC_UID_RE.match(bare):
        if not bare.startswith(SEC_UID_PREFIX):
            # 抖音号之类的裸 id 查不到主页，不发请求
            raise _not_a_profile()
        return bare
    url = await resolve_input_url(target)
    if url is None:
        raise upstream_empty("Could not resolve the link.", RETRY_AFTER_S, {"sec_uid": None})
    parsed = URLParser.parse(url) or {}
    sec_uid = str(parsed.get("sec_uid") or "") if parsed.get("type") == "user" else ""
    if not _SEC_UID_RE.match(sec_uid):
        raise _not_a_profile()
    return sec_uid


def _project_user(raw: Dict[str, Any], sec_uid: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "sec_uid": clean_text(raw.get("sec_uid") or sec_uid, ID_MAX_CHARS),
        "uid": clean_text(raw.get("uid"), ID_MAX_CHARS),
        "unique_id": clean_text(raw.get("unique_id") or raw.get("short_id"), NAME_MAX_CHARS),
        "nickname": clean_text(raw.get("nickname"), NAME_MAX_CHARS),
        "signature": clean_text(raw.get("signature"), DESC_MAX_CHARS),
    }
    for field in _USER_STAT_FIELDS:
        data[field] = as_int(raw.get(field))
    ip_location = clean_text(raw.get("ip_location"), NAME_MAX_CHARS)
    if ip_location:
        data["ip_location"] = ip_location
    data["note"] = URL_TYPE_NOTES["user"]
    return data


async def run_user(args: argparse.Namespace) -> Dict[str, Any]:
    cookies = state.read_cookies()
    if not cookies:
        # 没有 cookie 文件时 profile 接口拿不到数据，不发任何请求（含短链解析）
        raise AgentError("LOGIN_REQUIRED", "The user verb needs saved cookies; none are saved.")

    from core.api_client import LoginRequiredError

    sec_uid = await _resolve_sec_uid(args.target)
    try:
        async with open_client(cookies) as client:
            raw = await client.get_user_info(sec_uid)
    except LoginRequiredError:
        raise AgentError("LOGIN_REQUIRED", "Douyin asked for a login for this profile.") from None
    if not isinstance(raw, dict) or not raw:
        raise upstream_empty("Douyin returned no profile.", RETRY_AFTER_S, {"sec_uid": sec_uid})
    return _project_user(raw, sec_uid)
