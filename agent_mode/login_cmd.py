"""`login`：让人在有界面的 Chromium 里登录抖音，或导入人自己导出的 cookie 文件。

浏览器流程不读终端：每隔几秒看一次浏览器里的 cookie，出现会话 cookie 就收尾保存。
cookie 只写进状态目录（0600），输出里只有键名。接口见 agent_mode/entry.py。
"""

import argparse
import asyncio
import contextlib
import os
import sys
import time
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

from agent_mode import state
from agent_mode.entry import bounded_int
from agent_mode.envelope import AgentError, add_warning, install_action, python_command
from agent_mode.login_import import has_session, import_from_file, is_douyin_domain

LOGIN_DEFAULT_TIMEOUT_S = 300
LOGIN_MAX_TIMEOUT_S = 900
LOGIN_URL = "https://www.douyin.com/"
POLL_INTERVAL_S = 2.0
# 看到会话 cookie 后再等一会儿，让登录跳转带来的其他 cookie 落定
SETTLE_SECONDS = 3.0
DISPLAY_ENV_KEYS = ("DISPLAY", "WAYLAND_DISPLAY")
_BROWSER_MISSING_MARKERS = ("Executable doesn't exist", "playwright install")
_FROM_FILE_HINT = (
    "On a machine without a display, have the user export their douyin.com cookies as JSON "
    "from their own browser and run `douyin-dl agent login --from-file PATH`."
)

CookieReader = Callable[[], Awaitable[List[Dict[str, Any]]]]


def register(subparsers: Any) -> None:
    login = subparsers.add_parser(
        "login", help="open a browser for the user to log in, then save cookies"
    )
    login.add_argument(
        "--timeout",
        type=bounded_int(1, LOGIN_MAX_TIMEOUT_S),
        default=LOGIN_DEFAULT_TIMEOUT_S,
        metavar="S",
        help="seconds to wait for the user to finish logging in",
    )
    login.add_argument(
        "--from-file", metavar="PATH", help="import a cookie JSON file the user exported"
    )
    login.set_defaults(handler=run_login)


def cookies_to_dict(browser_cookies: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    """浏览器 cookie 列表 -> name→value，只留 douyin 域、值非空的。"""
    from utils.cookie_utils import sanitize_cookies

    picked = {
        cookie.get("name"): cookie.get("value")
        for cookie in browser_cookies or []
        if isinstance(cookie, dict) and is_douyin_domain(cookie.get("domain"))
    }
    return {key: value for key, value in sanitize_cookies(picked).items() if value}


async def wait_for_session(
    read_cookies: CookieReader,
    *,
    timeout_s: float,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Optional[Dict[str, str]]:
    """轮询到出现会话 cookie 为止；超时返回 None。时钟和读取函数都可注入。"""
    deadline = clock() + timeout_s
    while True:
        if has_session(cookies_to_dict(await read_cookies())):
            await sleep(SETTLE_SECONDS)
            return cookies_to_dict(await read_cookies())
        remaining = deadline - clock()
        if remaining <= 0:
            return None
        await sleep(min(POLL_INTERVAL_S, remaining))


def display_missing(platform: str, environ: Any) -> bool:
    """Linux 上既没有 X 也没有 Wayland 时，有界面的浏览器起不来。"""
    if not platform.startswith("linux"):
        return False
    return not any(str(environ.get(key) or "").strip() for key in DISPLAY_ENV_KEYS)


def _browser_install_command() -> str:
    return python_command("-m", "playwright", "install", "chromium")


def _load_playwright() -> Any:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise AgentError(
            "DEPENDENCY_MISSING",
            "Playwright is not installed; `login` needs it to open a browser.",
            action=install_action("playwright", hint="Then run: %s" % _browser_install_command()),
        ) from None
    return async_playwright


def _launch_error(exc: Exception) -> Exception:
    """Chromium 没装时给出精确的安装命令；其他启动失败原样抛出。"""
    if any(marker in str(exc) for marker in _BROWSER_MISSING_MARKERS):
        return AgentError(
            "DEPENDENCY_MISSING",
            "Playwright's Chromium browser is not installed.",
            action={"kind": "install", "command": _browser_install_command()},
        )
    return exc


def _timeout_error(timeout_s: int) -> AgentError:
    return AgentError(
        "LOGIN_REQUIRED",
        "No Douyin login was detected within %d seconds. Ask the user to finish logging in "
        "in the browser window, then run login again." % timeout_s,
        retryable=True,
    )


async def _finish_navigation(nav: Any) -> None:
    if not nav.done():
        nav.cancel()
    outcome = (await asyncio.gather(nav, return_exceptions=True))[0]
    if isinstance(outcome, Exception):
        add_warning("opening %s failed (%s)" % (LOGIN_URL, type(outcome).__name__))


async def _add_ms_token(page: Any, cookies: Dict[str, str]) -> None:
    """msToken 有时不在 cookie 里：复用人用流程的兜底提取，失败不影响登录结果。"""
    from tools.cookie_fetcher import try_extract_ms_token

    with contextlib.suppress(Exception):
        token = await try_extract_ms_token(page, cookies, [], [])
        if token and not cookies.get("msToken"):
            cookies["msToken"] = token


async def _drive_login(browser: Any, timeout_s: int) -> Dict[str, str]:
    from tools.cookie_fetcher import goto_with_fallback, is_target_closed_error

    context = await browser.new_context()
    page = await context.new_page()
    # 导航放后台：抖音首页常年达不到 networkidle，不能让它挡住轮询
    nav = asyncio.ensure_future(goto_with_fallback(page, LOGIN_URL))
    print("A Chromium window is open. Log in to Douyin there; this command ends on its own.")
    try:
        cookies = await wait_for_session(context.cookies, timeout_s=timeout_s)
        if cookies is None:
            raise _timeout_error(timeout_s)
        await _add_ms_token(page, cookies)
        return cookies
    except AgentError:
        raise
    except Exception as exc:
        if is_target_closed_error(exc):
            raise AgentError(
                "LOGIN_REQUIRED",
                "The browser window was closed before a Douyin login was detected.",
                retryable=True,
            ) from None
        raise
    finally:
        await _finish_navigation(nav)


async def _capture_from_browser(timeout_s: int) -> Dict[str, str]:
    async_playwright = _load_playwright()
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch(headless=False)
        except Exception as exc:
            raise _launch_error(exc) from None
        try:
            return await _drive_login(browser, timeout_s)
        finally:
            with contextlib.suppress(Exception):
                await browser.close()


async def run_login(args: argparse.Namespace) -> Dict[str, Any]:
    if args.from_file:
        cookies = import_from_file(args.from_file)
    else:
        if display_missing(sys.platform, os.environ):
            raise AgentError(
                "USAGE",
                "No display found (DISPLAY and WAYLAND_DISPLAY are unset), so a browser "
                "window cannot be opened for login.",
                action={"kind": "fix_usage", "hint": _FROM_FILE_HINT},
            )
        cookies = await _capture_from_browser(args.timeout)
    saved_to = state.write_cookies(cookies)
    return {
        "saved_to": str(saved_to),
        "keys_present": state.keys_present(cookies),
        "login_status": state.login_status(cookies),
    }
