"""`inspect <url>`：只做 URL 分类，告诉 agent 这个链接能不能处理、该用哪个 verb。

除短链的 1 次重定向解析外不联网，不调用任何 API。`user`、`live` 的短链也经这里的
`resolve_input_url` 解析：不带 cookie、只走 https、只认抖音短链域名。
"""

import argparse
import re
from typing import Any, Dict, Optional

from agent_mode import state
from agent_mode.capabilities import (
    ARGUS_GATED,
    UNKNOWN_URL_NOTE,
    UNRESOLVED_URL_TYPE,
    UNSUPPORTED,
    URL_TYPE_NOTES,
    URL_TYPE_SUGGESTED_VERB,
    url_type_level,
)
from agent_mode.envelope import AgentError, clean_text, retry_action

URL_MAX_CHARS = 2048
SHORT_LINK_RETRY_AFTER_S = 30
HTTP_SCHEME = "http://"
HTTPS_SCHEME = "https://"
_URL_IN_TEXT_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_PARSER_META_KEYS = ("original_url", "type")


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "inspect", help="classify a Douyin URL and report what agent mode can do with it"
    )
    parser.add_argument("url", help="Douyin URL, short link, or share text containing one")
    parser.set_defaults(handler=run_inspect)


def extract_url(text: str) -> str:
    """分享文案里取第一个 URL；裸域名补上 https://。"""
    candidate = text.strip()
    match = _URL_IN_TEXT_RE.search(candidate)
    if match:
        return match.group(0)
    if candidate and not _SCHEME_RE.match(candidate) and " " not in candidate:
        return HTTPS_SCHEME + candidate
    return candidate


def _force_https(url: str) -> str:
    if url.lower().startswith(HTTP_SCHEME):
        return HTTPS_SCHEME + url[len(HTTP_SCHEME) :]
    return url


async def resolve_short_url(url: str) -> Optional[str]:
    """跟随抖音短链的重定向；不是抖音短链或解析失败时返回 None。

    明文 http 会让路径上的任何人看到请求，所以一律改成 https；
    解析也不需要登录态，client 不带任何 cookie。
    """
    from core.api_client import DouyinAPIClient
    from utils.validators import is_short_url, normalize_short_url

    if not is_short_url(url):
        return None
    target = _force_https(normalize_short_url(url))
    async with DouyinAPIClient({}, proxy=state.build_config().get("proxy")) as client:
        return await client.resolve_short_url(target)


async def resolve_input_url(raw: str) -> Optional[str]:
    """用户输入（URL、短链或分享文案）-> 可分类的长链；短链解析不出来时返回 None。"""
    from utils.validators import is_short_url

    url = extract_url(raw)
    if not is_short_url(url):
        return url
    resolved = await resolve_short_url(url) or ""
    return None if is_short_url(resolved) else resolved or None


def _classify(raw_input: str, resolved_url: str) -> Dict[str, Any]:
    from core.url_parser import URLParser
    from utils.validators import parse_url_type

    url_type = parse_url_type(resolved_url)
    ids: Dict[str, str] = {}
    if url_type:
        parsed = URLParser.parse(resolved_url) or {}
        ids = {k: str(v) for k, v in parsed.items() if k not in _PARSER_META_KEYS}
    return {
        "input": clean_text(raw_input, URL_MAX_CHARS),
        "resolved_url": clean_text(resolved_url, URL_MAX_CHARS),
        "url_type": url_type,
        "ids": ids,
        "capability": url_type_level(url_type),
        "suggested_verb": URL_TYPE_SUGGESTED_VERB.get(str(url_type)),
        "note": URL_TYPE_NOTES.get(str(url_type), UNKNOWN_URL_NOTE),
    }


def _unresolved(raw_input: str) -> AgentError:
    return AgentError(
        "UPSTREAM_EMPTY",
        "Could not resolve the short link (network error, expired link, or rate limit).",
        action=retry_action(SHORT_LINK_RETRY_AFTER_S),
        data={"input": clean_text(raw_input, URL_MAX_CHARS), "resolved_url": None},
    )


async def run_inspect(args: argparse.Namespace) -> Dict[str, Any]:
    url = await resolve_input_url(args.url)
    if url is None:
        raise _unresolved(args.url)

    data = _classify(args.url, url)
    if data["url_type"] == UNRESOLVED_URL_TYPE:
        raise _unresolved(args.url)
    if data["capability"] == ARGUS_GATED:
        raise AgentError(
            "ARGUS_GATED",
            "URL type '%s' is behind Douyin's Argus gate; agent mode cannot download it."
            % data["url_type"],
            data=data,
        )
    if data["capability"] == UNSUPPORTED:
        raise AgentError("UNSUPPORTED", data["note"], data=data)
    return data
