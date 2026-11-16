"""`hot`、`search`、`user` 共用的常量与小工具。"""

from typing import Any, Dict, Optional

from agent_mode import state
from agent_mode.envelope import AgentError, retry_action

RETRY_AFTER_S = 60
ID_MAX_CHARS = 128
EMPTY_HINT = "Likely a rate limit or anti-bot response. Wait before retrying; do not loop."


def as_int(value: Any) -> Optional[int]:
    """平台数值可能是 int、数字字符串或缺失；解析不了就是 None。"""
    try:
        return None if isinstance(value, bool) else int(value)
    except (TypeError, ValueError):
        return None


def as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def open_client(cookies: Dict[str, str]) -> Any:
    from core.api_client import DouyinAPIClient

    return DouyinAPIClient(cookies, proxy=state.build_config().get("proxy"))


def upstream_empty(message: str, retry_after_s: int, data: Dict[str, Any]) -> AgentError:
    action = retry_action(retry_after_s, EMPTY_HINT)
    return AgentError("UPSTREAM_EMPTY", message, action=action, data=data)
