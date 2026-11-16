"""`login --from-file`：校验并导入人自己从浏览器导出的 cookie JSON。

接受 name→value 的对象，或 `[{name, value, ...}]` 数组。报错信息里不含任何 cookie 值。
"""

import json
from typing import Any, Dict, List, Optional, Tuple

from agent_mode import state
from agent_mode.envelope import AgentError, add_warning, clean_text

DOUYIN_DOMAIN = "douyin.com"
FROM_FILE_MAX_BYTES = 1024 * 1024
KEY_MAX_CHARS = 40
_DOWNGRADE_HINT = (
    "Have the user log in to douyin.com in their browser and export the cookies again, "
    "or run `douyin-dl agent login`."
)


def is_douyin_domain(domain: Any) -> bool:
    host = str(domain or "").lstrip(".").lower()
    return host == DOUYIN_DOMAIN or host.endswith("." + DOUYIN_DOMAIN)


def has_session(cookies: Dict[str, str]) -> bool:
    return any(cookies.get(key) for key in state.LOGIN_COOKIE_KEYS)


def _bad_file(detail: str) -> AgentError:
    return AgentError(
        "USAGE",
        "--from-file: %s. Expected a JSON object of name->value, or an array of "
        "{name, value} cookie objects." % detail,
    )


def _scalar(value: Any) -> Optional[str]:
    """cookie 值只接受字符串和数字；其他类型返回 None。"""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    return str(value)


def _pairs_from_mapping(raw: Dict[str, Any]) -> List[Tuple[str, str]]:
    pairs = []
    for name, value in raw.items():
        text = _scalar(value)
        if text is None:
            raise _bad_file("value of '%s' is not a string" % clean_text(name, KEY_MAX_CHARS))
        pairs.append((name, text))
    return pairs


def _pairs_from_list(raw: List[Any]) -> List[Tuple[str, str]]:
    pairs = []
    for index, item in enumerate(raw):
        name = item.get("name") if isinstance(item, dict) else None
        text = _scalar(item.get("value")) if isinstance(item, dict) else None
        if not isinstance(name, str) or text is None:
            raise _bad_file("entry %d has no string `name` and `value`" % index)
        # 浏览器导出常带 domain：别的站点的 cookie 不要
        if "domain" in item and not is_douyin_domain(item["domain"]):
            continue
        pairs.append((name, text))
    return pairs


def parse_cookie_export(raw: Any) -> Dict[str, str]:
    """校验人导出的 cookie JSON，返回 name→value。报错信息里不含任何 cookie 值。"""
    from utils.cookie_utils import sanitize_cookies

    if isinstance(raw, dict):
        pairs = _pairs_from_mapping(raw)
    elif isinstance(raw, list):
        pairs = _pairs_from_list(raw)
    else:
        raise _bad_file("top level is neither an object nor an array")
    cookies = {key: value for key, value in sanitize_cookies(dict(pairs)).items() if value}
    if not cookies:
        raise _bad_file("no usable douyin.com cookies found")
    skipped = len(raw) - len(cookies)
    if skipped:
        add_warning(
            "%d entries in the cookie file were skipped (other site, empty value, duplicate "
            "or invalid name)" % skipped
        )
    return cookies


def import_from_file(raw_path: str) -> Dict[str, str]:
    path = state.resolve_path(raw_path)
    if not path.is_file():
        raise AgentError("USAGE", "--from-file: %s is not a file" % path)
    if path.stat().st_size > FROM_FILE_MAX_BYTES:
        raise _bad_file("file is larger than %d bytes" % FROM_FILE_MAX_BYTES)
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        # JSON 解析错误的原文可能带出文件内容，只报类型
        raise _bad_file("cannot read it as JSON (%s)" % type(exc).__name__) from None
    cookies = parse_cookie_export(raw)
    _refuse_harmful_import(cookies)
    return cookies


def _refuse_harmful_import(cookies: Dict[str, str]) -> None:
    """写盘之前拦下两种导入：根本不是抖音 cookie 的文件，以及会冲掉已保存登录态的文件。"""
    if not state.keys_present(cookies):
        raise AgentError(
            "USAGE",
            "--from-file: none of the known Douyin cookie names (such as sessionid or ttwid) "
            "has a value in this file, so it does not look like a douyin.com cookie export. "
            "Nothing was saved.",
        )
    if has_session(cookies):
        return
    if state.login_status(state.read_cookies()) == state.LOGIN_STATUS_LOGGED_IN:
        raise AgentError(
            "USAGE",
            "--from-file: this file has no sessionid, and importing it would replace the "
            "saved logged-in cookies. Nothing was changed.",
            action={"kind": "fix_usage", "hint": _DOWNGRADE_HINT},
        )
    add_warning(
        "the imported cookies contain no sessionid; verbs that need a login will still "
        "return LOGIN_REQUIRED"
    )
