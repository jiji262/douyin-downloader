"""`doctor`：报告运行环境、状态目录、cookie 状态、依赖和各 verb 的可用性。

默认完全离线；加 `--online` 时用 profile/self 发 1 次请求验证登录态。
除 INTERNAL 外总是退出 0。输出里只有 cookie 的键名，没有值。
"""

import argparse
import datetime
import importlib.util
import platform
import sys
import time
from typing import Any, Dict, List, Optional

from agent_mode import state
from agent_mode.capabilities import URL_TYPE_CAPABILITIES, VERB_CAPABILITIES
from agent_mode.envelope import add_warning, clean_text, install_action, python_command
from agent_mode.version import get_version

SELF_PROFILE_ENDPOINT = "/aweme/v1/web/user/profile/self/"
SECONDS_PER_DAY = 86400
AGE_DAYS_PRECISION = 1
ONLINE_DETAIL_MAX_CHARS = 120
ONLINE_LOGGED_IN = "logged_in"
ONLINE_LOGIN_REQUIRED = "login_required"
ONLINE_EMPTY = "empty"
ONLINE_ERROR = "error"
ONLINE_SKIPPED = "skipped"
# verb -> 它额外依赖的本地组件（deps 里的键名）
VERB_DEPENDENCIES = {
    "login": ("playwright",),
    "live": ("ffmpeg",),
    "transcribe": ("whisper",),
}
# 能力级别 -> (满足它的登录状态, 不满足时写进 blocked_by 的名字)
_LEVEL_REQUIREMENTS = {
    "cookie": ((state.LOGIN_STATUS_DEVICE_ONLY, state.LOGIN_STATUS_LOGGED_IN), "cookies"),
    "login": ((state.LOGIN_STATUS_LOGGED_IN,), "login"),
}


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "doctor", help="report environment, login state and which verbs are usable"
    )
    parser.add_argument(
        "--online",
        action="store_true",
        help="also verify the saved login with one request to Douyin",
    )
    parser.set_defaults(handler=run_doctor)


def _state_dir_info() -> Dict[str, Any]:
    path = state.state_dir()
    return {"path": str(path), "exists": path.is_dir(), "mode": state.mode_string(path)}


def _cookies_info(cookies: Dict[str, str]) -> Dict[str, Any]:
    path = state.cookies_path()
    exists = path.is_file()
    mtime = None
    age_days = None
    if exists:
        stamp = path.stat().st_mtime
        mtime = datetime.datetime.fromtimestamp(stamp, datetime.timezone.utc).isoformat()
        age_days = round(max(time.time() - stamp, 0) / SECONDS_PER_DAY, AGE_DAYS_PRECISION)
    return {
        "path": str(path),
        "exists": exists,
        "mode": state.mode_string(path),
        "mtime": mtime,
        "age_days": age_days,
        "keys_present": state.keys_present(cookies),
        "login_status": state.login_status(cookies),
    }


def _module_dep(module: str, *pip_args: str, hint: str = "") -> Dict[str, Any]:
    try:
        available = importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        available = False
    info: Dict[str, Any] = {"available": available}
    if not available:
        info["install"] = install_action(*pip_args, hint=hint)
    return info


def _ffmpeg_dep() -> Dict[str, Any]:
    from core.ffmpeg import resolve_ffmpeg_path

    path = resolve_ffmpeg_path()
    info: Dict[str, Any] = {"available": bool(path), "path": path or None}
    if not path:
        info["install"] = install_action("imageio-ffmpeg==0.6.0")
    return info


def _deps_info() -> Dict[str, Any]:
    return {
        "ffmpeg": _ffmpeg_dep(),
        "playwright": _module_dep(
            "playwright",
            "playwright",
            hint="Then run: %s" % python_command("-m", "playwright", "install", "chromium"),
        ),
        "whisper": _module_dep("whisper", "openai-whisper"),
    }


def _verb_blockers(verb: str, login_state: str, deps: Dict[str, Any]) -> List[str]:
    blockers = []
    requirement = _LEVEL_REQUIREMENTS.get(VERB_CAPABILITIES[verb])
    if requirement is not None and login_state not in requirement[0]:
        blockers.append(requirement[1])
    for dep in VERB_DEPENDENCIES.get(verb, ()):
        if not deps[dep]["available"]:
            blockers.append(dep)
    return blockers


def _capabilities_info(login_state: str, deps: Dict[str, Any]) -> Dict[str, Any]:
    verbs = {}
    for verb, level in VERB_CAPABILITIES.items():
        blockers = _verb_blockers(verb, login_state, deps)
        verbs[verb] = {"level": level, "ready": not blockers, "blocked_by": blockers}
    return {"verbs": verbs, "url_types": dict(URL_TYPE_CAPABILITIES)}


def _online_result(status: str, detail: str = "") -> Dict[str, Any]:
    return {
        "endpoint": SELF_PROFILE_ENDPOINT,
        "status": status,
        "logged_in": status == ONLINE_LOGGED_IN,
        "detail": clean_text(detail, ONLINE_DETAIL_MAX_CHARS),
    }


async def _online_check(cookies: Dict[str, str]) -> Dict[str, Any]:
    """用 profile/self 验证登录态。没有会话 cookie 时不发请求。"""
    if state.login_status(cookies) != state.LOGIN_STATUS_LOGGED_IN:
        return _online_result(ONLINE_SKIPPED, "no session cookie saved; nothing to verify")

    from core.api_client import DouyinAPIClient, LoginRequiredError

    proxy = state.build_config().get("proxy")
    try:
        async with DouyinAPIClient(cookies, proxy=proxy) as client:
            user = await client.get_self_info()
    except LoginRequiredError:
        return _online_result(ONLINE_LOGIN_REQUIRED, "Douyin rejected the saved session")
    except Exception as exc:  # noqa: BLE001 — doctor 只报告，不因联网失败而失败
        return _online_result(ONLINE_ERROR, type(exc).__name__)
    if user:
        return _online_result(ONLINE_LOGGED_IN)
    return _online_result(ONLINE_EMPTY, "empty response (rate limit, anti-bot or network)")


async def run_doctor(args: argparse.Namespace) -> Dict[str, Any]:
    cookies = state.read_cookies()
    for warning in state.permission_warnings():
        add_warning(warning)
    deps = _deps_info()
    online: Optional[Dict[str, Any]] = None
    if args.online:
        online = await _online_check(cookies)
    return {
        "version": get_version(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "executable": sys.executable,
        "state_dir": _state_dir_info(),
        "cookies": _cookies_info(cookies),
        "deps": deps,
        "capabilities": _capabilities_info(state.login_status(cookies), deps),
        "online_check": online,
    }
