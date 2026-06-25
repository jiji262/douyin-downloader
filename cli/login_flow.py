"""CLI-only interactive re-login flow.

Detects nothing itself — invoked by cli.main when a LoginRequiredError
bubbles up. Opens a browser via the cookie fetcher, guides manual Douyin
login, then loads the freshly captured cookies from disk.

NOT shared with the desktop project: the desktop app drives its own GUI
login surface instead of this terminal-driven Playwright flow.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

from tools.cookie_fetcher import fetch_cookies
from utils.cookie_utils import sanitize_cookies
from utils.logger import setup_logger

logger = setup_logger("LoginFlow")

_DEFAULT_COOKIES_PATH = Path("config/cookies.json")


def can_interactive_login(*, serve: bool = False) -> bool:
    """True only when we can safely open a browser and read a terminal Enter."""
    if serve:
        return False
    try:
        return bool(sys.stdin.isatty())
    except (AttributeError, ValueError):
        return False


async def interactive_relogin(
    cookies_path: Path = _DEFAULT_COOKIES_PATH,
) -> Optional[dict]:
    """Open a browser, guide login, capture cookies. Returns fresh cookies or None."""
    print(
        "\n[登录态已失效] 抖音要求重新登录。即将打开浏览器，请完成抖音登录，"
        "登录成功后回到本终端按 Enter。\n"
    )
    try:
        rc = await fetch_cookies(output=cookies_path)
    except Exception as exc:  # noqa: BLE001 — surface, don't crash the run
        logger.error("Interactive relogin failed to launch: %s", exc)
        print(
            "[ERROR] 无法启动登录流程。请确认已安装 Playwright："
            "\n  pip install playwright && playwright install chromium"
            "\n或手动更新 config/cookies.json 后重试。"
        )
        return None

    if rc != 0:
        print("[ERROR] 登录流程未成功完成，已中止。")
        return None

    try:
        raw = json.loads(Path(cookies_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Could not read captured cookies from %s: %s", cookies_path, exc)
        return None

    cookies = sanitize_cookies(raw if isinstance(raw, dict) else {})
    if not cookies.get("sessionid"):
        print("[ERROR] 登录后未获取到有效会话（缺少 sessionid），请重试。")
        return None
    return cookies
