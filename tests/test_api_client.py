import asyncio
import sys
import types

from core.api_client import DouyinAPIClient


def test_default_query_uses_existing_ms_token():
    client = DouyinAPIClient({"msToken": "token-1"})
    params = asyncio.run(client._default_query())
    assert params["msToken"] == "token-1"


def test_build_signed_path_fallbacks_to_xbogus_when_abogus_disabled():
    client = DouyinAPIClient({"msToken": "token-1"})
    client._abogus_enabled = False
    signed_url, _ua = client.build_signed_path("/aweme/v1/web/aweme/detail/", {"a": 1})
    assert "X-Bogus=" in signed_url


def test_build_signed_path_prefers_abogus(monkeypatch):
    class _FakeFp:
        @staticmethod
        def generate_fingerprint(_browser):
            return "fp"

    class _FakeABogus:
        def __init__(self, fp, user_agent):
            self.fp = fp
            self.user_agent = user_agent

        def generate_abogus(self, params, body=""):
            return (f"{params}&a_bogus=fake_ab", "fake_ab", self.user_agent, body)

    import core.api_client as api_module

    monkeypatch.setattr(api_module, "BrowserFingerprintGenerator", _FakeFp)
    monkeypatch.setattr(api_module, "ABogus", _FakeABogus)

    client = DouyinAPIClient({"msToken": "token-1"})
    client._abogus_enabled = True

    signed_url, _ua = client.build_signed_path("/aweme/v1/web/aweme/detail/", {"a": 1})
    assert "a_bogus=fake_ab" in signed_url


def test_browser_fallback_caps_warmup_wait(monkeypatch):
    class _FakeMouse:
        async def wheel(self, _x, _y):
            return

    class _FakePage:
        def __init__(self):
            self.mouse = _FakeMouse()
            self.wait_calls = 0
            self._response_handler = None

        def on(self, event_name, callback):
            if event_name == "response":
                self._response_handler = callback

        async def goto(self, *_args, **_kwargs):
            return

        async def title(self):
            return "抖音"

        def is_closed(self):
            return False

        async def wait_for_timeout(self, _ms):
            self.wait_calls += 1

    class _FakeContext:
        def __init__(self, page):
            self._page = page

        async def add_cookies(self, _cookies):
            return

        async def new_page(self):
            return self._page

        async def cookies(self, _base_url):
            return []

        async def close(self):
            return

    class _FakeBrowser:
        def __init__(self, context):
            self._context = context

        async def new_context(self, **_kwargs):
            return self._context

        async def close(self):
            return

    class _FakeChromium:
        def __init__(self, browser):
            self._browser = browser

        async def launch(self, **_kwargs):
            return self._browser

    class _FakePlaywright:
        def __init__(self, chromium):
            self.chromium = chromium

    class _FakePlaywrightManager:
        def __init__(self, playwright):
            self._playwright = playwright

        async def __aenter__(self):
            return self._playwright

        async def __aexit__(self, *_args):
            return

    page = _FakePage()
    context = _FakeContext(page)
    browser = _FakeBrowser(context)
    chromium = _FakeChromium(browser)
    playwright = _FakePlaywright(chromium)
    manager = _FakePlaywrightManager(playwright)

    fake_playwright_pkg = types.ModuleType("playwright")
    fake_async_api = types.ModuleType("playwright.async_api")
    fake_async_api.async_playwright = lambda: manager
    monkeypatch.setitem(sys.modules, "playwright", fake_playwright_pkg)
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_async_api)

    client = DouyinAPIClient({"msToken": "token-1"})

    async def _fake_extract(_page):
        return []

    monkeypatch.setattr(client, "_extract_aweme_ids_from_page", _fake_extract)

    ids = asyncio.run(
        client.collect_user_post_ids_via_browser(
            "sec_uid_x",
            expected_count=0,
            headless=False,
            max_scrolls=240,
            idle_rounds=3,
            wait_timeout_seconds=600,
        )
    )

    assert ids == []
    # warmup should be capped instead of waiting full wait_timeout_seconds
    # and scrolling should stop after idle rounds even when no id is found
    assert page.wait_calls <= 30
    stats = client.pop_browser_post_stats()
    assert stats["selected_ids"] == 0
    assert client.pop_browser_post_stats() == {}
