import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Dict, Optional, Sequence

import yaml


DEFAULT_URL = "https://www.douyin.com/"
DEFAULT_OUTPUT = Path("config/cookies.json")
REQUIRED_KEYS = {"msToken", "ttwid", "odin_tt", "passport_csrf_token"}
SUGGESTED_KEYS = REQUIRED_KEYS | {"sid_guard", "sessionid", "sid_tt"}


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
        print("[ERROR] Playwright is not installed. Run `pip install playwright` first.", file=sys.stderr)
        return 1

    async with async_playwright() as p:
        browser_factory = getattr(p, args.browser)
        browser = await browser_factory.launch(headless=args.headless)
        context = await browser.new_context()
        page = await context.new_page()

        print("[INFO] Browser launched. Please complete Douyin login in the opened window.")
        print("[INFO] Press Enter in this terminal once the homepage shows you are logged in.")

        await page.goto(args.url, wait_until="networkidle")
        await asyncio.to_thread(input)

        storage = await context.storage_state()
        cookies = {
            cookie["name"]: cookie["value"]
            for cookie in storage["cookies"]
            if cookie["domain"].endswith("douyin.com")
        }

        await context.close()
        await browser.close()

    picked = cookies if args.include_all else filter_cookies(cookies)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(picked, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[INFO] Saved {len(picked)} cookie(s) to {args.output.resolve()}")

    missing = REQUIRED_KEYS - picked.keys()
    if missing:
        print(f"[WARN] Missing required cookie keys: {', '.join(sorted(missing))}")

    if args.config:
        update_config(args.config, picked)

    return 0


def filter_cookies(cookies: Dict[str, str]) -> Dict[str, str]:
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
