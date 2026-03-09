import asyncio
import sys
import types

import pytest

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


@pytest.mark.asyncio
async def test_get_user_post_returns_normalized_dto(monkeypatch):
    client = DouyinAPIClient({"msToken": "token-1"})
    captured_params = {}

    async def _fake_request_json(path, params, suppress_error=False):
        assert path == "/aweme/v1/web/aweme/post/"
        captured_params.update(params)
        return {
            "status_code": 0,
            "aweme_list": [{"aweme_id": "111"}],
            "has_more": 1,
            "max_cursor": 9,
        }

    monkeypatch.setattr(client, "_request_json", _fake_request_json)
    data = await client.get_user_post("sec-1", max_cursor=0, count=20)

    assert data["items"] == [{"aweme_id": "111"}]
    assert data["aweme_list"] == [{"aweme_id": "111"}]
    assert data["has_more"] is True
    assert data["max_cursor"] == 9
    assert data["status_code"] == 0
    assert data["source"] == "api"
    assert isinstance(data["raw"], dict)
    assert captured_params["show_live_replay_strategy"] == "1"
    assert captured_params["need_time_list"] == "1"
    assert captured_params["time_list_query"] == "0"


@pytest.mark.asyncio
async def test_user_mode_endpoints_use_shared_paged_normalization(monkeypatch):
    client = DouyinAPIClient({"msToken": "token-1"})
    called_requests = []

    async def _fake_request_json(path, params, suppress_error=False):
        called_requests.append((path, dict(params)))
        return {"status_code": 0, "aweme_list": [], "has_more": 0, "max_cursor": 0}

    monkeypatch.setattr(client, "_request_json", _fake_request_json)

    like_data = await client.get_user_like("sec-1", max_cursor=0, count=20)
    mix_data = await client.get_user_mix("sec-1", max_cursor=0, count=20)
    music_data = await client.get_user_music("sec-1", max_cursor=0, count=20)

    assert [path for path, _params in called_requests] == [
        "/aweme/v1/web/aweme/favorite/",
        "/aweme/v1/web/mix/list/",
        "/aweme/v1/web/music/list/",
    ]
    mix_params = called_requests[1][1]
    music_params = called_requests[2][1]
    for forbidden_key in (
        "show_live_replay_strategy",
        "need_time_list",
        "time_list_query",
    ):
        assert forbidden_key not in mix_params
        assert forbidden_key not in music_params
    assert like_data["items"] == []
    assert mix_data["items"] == []
    assert music_data["items"] == []


@pytest.mark.asyncio
async def test_collect_endpoints_use_expected_paths_and_normalization(monkeypatch):
    client = DouyinAPIClient({"msToken": "token-1"})
    called_requests = []

    async def _fake_request_json(path, params, suppress_error=False):
        called_requests.append((path, dict(params)))
        if path == "/aweme/v1/web/collects/list/":
            return {
                "status_code": 0,
                "collects_list": [{"collects_id_str": "collect-1"}],
                "has_more": 1,
                "cursor": 9,
            }
        if path == "/aweme/v1/web/collects/video/list/":
            return {
                "status_code": 0,
                "aweme_list": [{"aweme_id": "aweme-1"}],
                "has_more": 0,
                "cursor": 0,
            }
        if path == "/aweme/v1/web/mix/listcollection/":
            return {
                "status_code": 0,
                "mix_infos": [{"mix_id": "mix-1"}],
                "has_more": 0,
                "cursor": 0,
            }
        return {"status_code": 0, "has_more": 0, "cursor": 0}

    monkeypatch.setattr(client, "_request_json", _fake_request_json)

    collects_data = await client.get_user_collects("self", max_cursor=0, count=10)
    collect_aweme_data = await client.get_collect_aweme(
        "collect-1", max_cursor=0, count=10
    )
    collect_mix_data = await client.get_user_collect_mix(
        "self", max_cursor=0, count=12
    )

    assert [path for path, _params in called_requests] == [
        "/aweme/v1/web/collects/list/",
        "/aweme/v1/web/collects/video/list/",
        "/aweme/v1/web/mix/listcollection/",
    ]
    assert called_requests[0][1]["count"] == 10
    assert called_requests[0][1]["version_code"] == "170400"
    assert called_requests[1][1]["collects_id"] == "collect-1"
    assert called_requests[1][1]["count"] == 10
    assert called_requests[2][1]["count"] == 12
    assert collects_data["items"] == [{"collects_id_str": "collect-1"}]
    assert collects_data["has_more"] is True
    assert collects_data["max_cursor"] == 9
    assert collect_aweme_data["items"] == [{"aweme_id": "aweme-1"}]
    assert collect_mix_data["items"] == [{"mix_id": "mix-1"}]


@pytest.mark.asyncio
async def test_mix_and_music_endpoints_are_normalized(monkeypatch):
    client = DouyinAPIClient({"msToken": "token-1"})

    async def _fake_request_json(path, _params, suppress_error=False):
        if path == "/aweme/v1/web/mix/detail/":
            return {"mix_info": {"mix_id": "mix-1"}}
        if path == "/aweme/v1/web/mix/aweme/":
            return {"status_code": 0, "aweme_list": [{"aweme_id": "a-1"}], "has_more": 0}
        if path == "/aweme/v1/web/music/detail/":
            return {"music_info": {"id": "music-1"}}
        if path == "/aweme/v1/web/music/aweme/":
            return {"status_code": 0, "aweme_list": [{"aweme_id": "a-2"}], "has_more": 0}
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(client, "_request_json", _fake_request_json)

    mix_detail = await client.get_mix_detail("mix-1")
    mix_page = await client.get_mix_aweme("mix-1", cursor=0, count=20)
    music_detail = await client.get_music_detail("music-1")
    music_page = await client.get_music_aweme("music-1", cursor=0, count=20)

    assert mix_detail == {"mix_id": "mix-1"}
    assert music_detail == {"id": "music-1"}
    assert mix_page["items"] == [{"aweme_id": "a-1"}]
    assert music_page["items"] == [{"aweme_id": "a-2"}]
