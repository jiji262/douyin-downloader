"""fetch_cookies builds the right Namespace and delegates to capture_cookies."""

from pathlib import Path

import pytest

import tools.cookie_fetcher as cf


@pytest.mark.asyncio
async def test_fetch_cookies_delegates(monkeypatch):
    seen = {}

    async def fake_capture(args):
        seen["args"] = args
        return 0

    monkeypatch.setattr(cf, "capture_cookies", fake_capture)

    rc = await cf.fetch_cookies(output=Path("/tmp/x/cookies.json"))

    assert rc == 0
    args = seen["args"]
    assert args.output == Path("/tmp/x/cookies.json")
    assert args.browser == "chromium"
    assert args.headless is False
    assert args.include_all is False
    assert args.url == cf.DEFAULT_URL
    assert args.config is None
