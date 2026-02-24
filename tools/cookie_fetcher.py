import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import parse_qs, unquote, urlparse

import yaml
from utils.cookie_utils import parse_cookie_header, sanitize_cookies

DEFAULT_URL = "https://www.douyin.com/"
DEFAULT_OUTPUT = Path("config/cookies.json")
REQUIRED_KEYS = {"msToken", "ttwid", "odin_tt", "passport_csrf_token"}
SUGGESTED_KEYS = REQUIRED_KEYS | {"sid_guard", "sessionid", "sid_tt"}
PRIMARY_WAIT_UNTIL = "networkidle"
FALLBACK_WAIT_UNTIL = "domcontentloaded"
PRIMARY_TIMEOUT_MS = 300_000
FALLBACK_TIMEOUT_MS = 300_000


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Launch a browser, guide manual login, then dump Douyin cookies.",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"Login page to open (default: {DEFAULT_URL})",
    )
    parser.add_argument(
        "--browser",
        choices=["chromium", "firefox", "webkit"],
        default="chromium",
        help="Playwright browser engine (default: chromium)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser headless (not recommended for manual login)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="JSON file to write collected cookies",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Optional config.yml to update with captured cookies",
    )
    parser.add_argument(
        "--include-all",
        action="store_true",
        help="Store every cookie from douyin.com instead of the recommended subset",
    )
    return parser.parse_args(argv)


async def capture_cookies(args: argparse.Namespace) -> int:
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except ImportError:  # pragma: no cover - defensive path
        print(
            "[ERROR] Playwright is not installed. Run `pip install playwright` first.",
            file=sys.stderr,
        )
        return 1

    async with async_playwright() as p:
        browser_factory = getattr(p, args.browser)
        browser = await browser_factory.launch(headless=args.headless)
        context = await browser.new_context()
        page = await context.new_page()
        observed_cookie_headers: List[str] = []
        observed_mstokens: List[str] = []

        def _on_request(request: Any) -> None:
            try:
                headers = request.headers or {}
                cookie_header = headers.get("cookie")
                if cookie_header:
                    observed_cookie_headers.append(cookie_header)
                url = request.url or ""
                query = parse_qs(urlparse(url).query)
                if "msToken" in query and query["msToken"]:
                    observed_mstokens.append((query["msToken"][0] or "").strip())
                token = extract_ms_token_from_text(url)
                if token:
                    observed_mstokens.append(token)
            except Exception:
                # 观察请求失败不应影响主流程
                return

        page.on("request", _on_request)

        print(
            "[INFO] Browser launched. Please complete Douyin login in the opened window."
        )
        print(
            "[INFO] Press Enter in this terminal once the homepage shows you are logged in."
        )

        await wait_for_login_confirmation(page, args.url)

        storage = await context.storage_state()
        cookies = {
            cookie["name"]: cookie["value"]
            for cookie in storage["cookies"]
            if cookie["domain"].endswith("douyin.com")
        }
        cookies = sanitize_cookies(cookies)

        ms_token = await try_extract_ms_token(
            page, cookies, observed_cookie_headers, observed_mstokens
        )
        if ms_token and not cookies.get("msToken"):
            cookies["msToken"] = ms_token
            print("[INFO] Extracted msToken from alternate sources.")

        await context.close()
        await browser.close()

    picked = cookies if args.include_all else filter_cookies(cookies)
    picked = sanitize_cookies(picked)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(picked, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[INFO] Saved {len(picked)} cookie(s) to {args.output.resolve()}")

    missing = REQUIRED_KEYS - picked.keys()
    if missing:
        print(f"[WARN] Missing required cookie keys: {', '.join(sorted(missing))}")

    if args.config:
        update_config(args.config, picked)

    return 0


def is_timeout_error(exc: Exception) -> bool:
    return exc.__class__.__name__ == "TimeoutError" or "Timeout" in str(exc)


def is_target_closed_error(exc: Exception) -> bool:
    return (
        exc.__class__.__name__ == "TargetClosedError"
        or "Target page, context or browser has been closed" in str(exc)
    )


async def goto_with_fallback(page: Any, url: str) -> str:
    # 部分站点会持续发请求，networkidle 可能一直达不到，超时后降级等待策略。
    try:
        await page.goto(url, wait_until=PRIMARY_WAIT_UNTIL, timeout=PRIMARY_TIMEOUT_MS)
        return PRIMARY_WAIT_UNTIL
    except Exception as exc:
        if is_target_closed_error(exc):
            print(
                "[WARN] Browser/page was closed during initial navigation, "
                "continuing with current browser state."
            )
            return "target_closed"
        if not is_timeout_error(exc):
            raise
        print(
            f"[WARN] goto(wait_until={PRIMARY_WAIT_UNTIL}) timed out after {PRIMARY_TIMEOUT_MS}ms, "
            f"falling back to {FALLBACK_WAIT_UNTIL}."
        )
    try:
        await page.goto(
            url, wait_until=FALLBACK_WAIT_UNTIL, timeout=FALLBACK_TIMEOUT_MS
        )
        return FALLBACK_WAIT_UNTIL
    except Exception as exc:
        if is_target_closed_error(exc):
            print(
                "[WARN] Browser/page was closed during fallback navigation, "
                "continuing with current browser state."
            )
            return "target_closed"
        if is_timeout_error(exc):
            print(
                f"[WARN] goto(wait_until={FALLBACK_WAIT_UNTIL}) also timed out after {FALLBACK_TIMEOUT_MS}ms, "
                "continuing anyway."
            )
            return "timeout"
        raise


async def wait_for_login_confirmation(
    page: Any, url: str, input_func: Any = input
) -> None:
    # 页面导航放到后台执行，避免在导航等待期间终端无法响应 Enter。
    nav_task = asyncio.create_task(goto_with_fallback(page, url))
    await asyncio.to_thread(input_func)

    if not nav_task.done():
        nav_task.cancel()
        try:
            await nav_task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            print(f"[WARN] Navigation task ended with error after cancel: {exc}")
        return

    try:
        await nav_task
    except Exception as exc:
        print(f"[WARN] Navigation task ended with error: {exc}")


async def try_extract_ms_token(
    page: Any,
    cookies: Dict[str, str],
    observed_cookie_headers: List[str],
    observed_mstokens: List[str],
) -> Optional[str]:
    existing = cookies.get("msToken")
    if existing:
        return existing

    for token in reversed(observed_mstokens):
        token = (token or "").strip()
        if token:
            return token

    for header in reversed(observed_cookie_headers):
        parsed = parse_cookie_header(header)
        token = (parsed.get("msToken") or "").strip()
        if token:
            return token
        extra = extract_ms_token_from_text(header)
        if extra:
            return extra

    try:
        doc_cookie = await page.evaluate("() => document.cookie || ''")
        parsed = parse_cookie_header(doc_cookie)
        token = (parsed.get("msToken") or "").strip()
        if token:
            return token
        extra = extract_ms_token_from_text(doc_cookie)
        if extra:
            return extra
    except Exception:
        pass

    js = """
() => {
  const values = [];
  const pushIf = (v) => {
    if (typeof v === 'string' && v.trim()) values.push(v.trim());
  };
  try {
    for (const key of Object.keys(localStorage || {})) {
      if (key.toLowerCase().includes('mstoken')) {
        pushIf(localStorage.getItem(key));
      }
    }
  } catch (e) {}
  try {
    for (const key of Object.keys(sessionStorage || {})) {
      if (key.toLowerCase().includes('mstoken')) {
        pushIf(sessionStorage.getItem(key));
      }
    }
  } catch (e) {}
  return values;
}
"""
    try:
        candidates = await page.evaluate(js)
        for candidate in candidates or []:
            if not isinstance(candidate, str):
                continue
            text = candidate.strip()
            if not text:
                continue
            parsed = parse_cookie_header(text)
            if parsed.get("msToken"):
                return parsed["msToken"]
            extra = extract_ms_token_from_text(text)
            if extra:
                return extra
            if len(text) <= 2048 and all(
                ch not in text for ch in [";", " ", "\n", "\r", "\t"]
            ):
                return text
    except Exception:
        pass

    return None


def extract_ms_token_from_text(text: str) -> Optional[str]:
    if not text:
        return None

    patterns = [
        r"(?:^|[;,&\s\"'])msToken=([^;,&\s\"']+)",
        r'"msToken"\s*:\s*"([^"]+)"',
        r"'msToken'\s*:\s*'([^']+)'",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        token = (match.group(1) or "").strip()
        if token:
            return unquote(token)
    return None


def filter_cookies(cookies: Dict[str, str]) -> Dict[str, str]:
    cookies = sanitize_cookies(cookies)
    picked = {k: v for k, v in cookies.items() if k in SUGGESTED_KEYS}
    if not picked:
        return cookies
    return picked


def update_config(config_path: Path, cookies: Dict[str, str]) -> None:
    existing: Dict[str, object] = {}
    if config_path.exists():
        existing = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    existing["cookies"] = cookies

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        yaml.safe_dump(existing, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(f"[INFO] Updated config file: {config_path.resolve()}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    return asyncio.run(capture_cookies(args))


if __name__ == "__main__":
    raise SystemExit(main())
