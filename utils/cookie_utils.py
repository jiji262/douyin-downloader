from __future__ import annotations

from typing import Any, Dict, Mapping

# RFC6265 token 分隔符与空白字符
INVALID_COOKIE_NAME_CHARS = set('()<>@,;:\\"/[]?={} \t\r\n')


def is_valid_cookie_name(name: str) -> bool:
    if not name or not isinstance(name, str):
        return False
    if any(ord(ch) < 33 or ord(ch) > 126 for ch in name):
        return False
    if any(ch in INVALID_COOKIE_NAME_CHARS for ch in name):
        return False
    return True


def sanitize_cookies(cookies: Mapping[Any, Any]) -> Dict[str, str]:
    sanitized: Dict[str, str] = {}
    for raw_key, raw_value in (cookies or {}).items():
        if not isinstance(raw_key, str):
            continue
        key = raw_key.strip()
        if not is_valid_cookie_name(key):
            continue
        value = "" if raw_value is None else str(raw_value).strip()
        sanitized[key] = value
    return sanitized


def parse_cookie_header(cookie_header: str) -> Dict[str, str]:
    if not cookie_header:
        return {}
    parsed: Dict[str, str] = {}
    for item in cookie_header.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        if not is_valid_cookie_name(key):
            continue
        parsed[key] = value.strip()
    return parsed
