import asyncio
import json
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


def test_build_signed_path_accepts_absolute_base_override():
    client = DouyinAPIClient({"msToken": "token-1"})
    client._abogus_enabled = False

    signed_url, _ua = client.build_signed_path(
        "/webcast/room/web/enter/",
        {"web_rid": "42075947470"},
        base_url="https://live.douyin.com",
    )

    assert signed_url.startswith("https://live.douyin.com/webcast/room/web/enter/?")


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
async def test_live_replay_endpoints_use_episode_paths(monkeypatch):
    client = DouyinAPIClient({"msToken": "token-1"})
    called_requests = []

    async def _fake_request_json(path, params, suppress_error=False):
        called_requests.append((path, dict(params), suppress_error))
        if path == "/aweme/v1/web/show/episode/enter/":
            return {"status_code": 0, "data": {"episode": {"attach_room_id_str": "room-1"}}}
        if path == "/aweme/v1/web/show/episode/replay_list/":
            return {
                "status_code": 0,
                "data": {
                    "all_replay": [
                        {
                            "info_list": [
                                {"episode_id_str": "ep-1", "replay_id": "rp-1", "title": "回放"}
                            ]
                        }
                    ]
                },
            }
        return {}

    monkeypatch.setattr(client, "_request_json", _fake_request_json)

    episode = await client.get_live_replay_episode("ep-1")
    replay = await client.get_live_replay_info("ep-1", "room-1", replay_id="rp-1")

    assert episode == {"attach_room_id_str": "room-1"}
    assert replay["episode_id_str"] == "ep-1"
    assert [call[0] for call in called_requests] == [
        "/aweme/v1/web/show/episode/enter/",
        "/aweme/v1/web/show/episode/replay_list/",
    ]
    assert called_requests[0][1]["episode_id"] == "ep-1"
    assert called_requests[0][1]["channel"] == ""
    assert called_requests[0][2] is True
    assert called_requests[1][1]["episode_id"] == "ep-1"
    assert called_requests[1][1]["room_id"] == "room-1"
    assert called_requests[1][1]["replay_id"] == "rp-1"
    assert called_requests[1][1]["channel"] == ""
    assert called_requests[1][2] is True


@pytest.mark.asyncio
async def test_live_web_rid_uses_live_domain(monkeypatch):
    client = DouyinAPIClient({"msToken": "token-1"})
    captured = {}

    async def _fake_request_json(path, params, **kwargs):
        captured.update(path=path, params=dict(params), kwargs=kwargs)
        return {
            "data": {
                "data": [{"id_str": "7664563379964595007", "status": 2}],
                "user": {"nickname": "主播"},
            }
        }

    monkeypatch.setattr(client, "_request_json", _fake_request_json)

    info = await client.get_live_room_info("42075947470")

    assert captured["path"] == "/webcast/room/web/enter/"
    assert captured["kwargs"]["base_url"] == "https://live.douyin.com"
    assert captured["kwargs"]["suppress_error"] is True
    assert captured["kwargs"]["request_headers"]["Referer"] == "https://live.douyin.com/"
    assert captured["params"]["web_rid"] == "42075947470"
    assert captured["params"]["app_name"] == "douyin_web"
    assert info["room"]["status"] == 2
    assert info["user"]["nickname"] == "主播"


@pytest.mark.asyncio
async def test_live_internal_room_id_uses_reflow_endpoint(monkeypatch):
    client = DouyinAPIClient({"msToken": "token-1"})
    captured = {}

    async def _fake_request_json(path, params, **kwargs):
        captured.update(path=path, params=dict(params), kwargs=kwargs)
        return {
            "data": {
                "room": {
                    "id_str": "7664563379964595007",
                    "status": 2,
                    "owner": {"nickname": "主播"},
                }
            }
        }

    monkeypatch.setattr(client, "_request_json", _fake_request_json)

    info = await client.get_live_room_info(
        "7664563379964595007",
        room_id_kind="room_id",
        sec_user_id="sec-test",
    )

    assert captured["path"] == "/webcast/room/reflow/info/"
    assert captured["kwargs"]["base_url"] == "https://webcast.amemv.com"
    assert captured["params"]["room_id"] == "7664563379964595007"
    assert captured["params"]["sec_user_id"] == "sec-test"
    assert info["room"]["status"] == 2
    assert info["user"]["nickname"] == "主播"


def test_extract_live_room_from_react_flight_html():
    room = {
        "id_str": "7664563379964595007",
        "status": 2,
        "stream_url": {"flv_pull_url": {"FULL_HD1": "https://cdn/live.flv"}},
        "owner": {"nickname": "主播"},
    }
    flight = "c:" + json.dumps({"state": {"room": room}}, ensure_ascii=False)
    html = f"<script>self.__pace_f.push({json.dumps([1, flight])})</script>"

    info = DouyinAPIClient._extract_live_room_from_html(html)

    assert info is not None
    assert info["room"] == room
    assert info["user"] == {"nickname": "主播"}
    assert info["raw"] == {"source": "live_page_ssr"}


@pytest.mark.asyncio
async def test_live_web_rid_falls_back_to_ssr_page(monkeypatch):
    client = DouyinAPIClient({"msToken": "token-1"})
    fallback = {
        "room": {"id_str": "7664563379964595007", "status": 2, "stream_url": {}},
        "user": {},
        "raw": {"source": "live_page_ssr"},
    }

    async def _empty_request(*_args, **_kwargs):
        return {}

    async def _fake_page(web_rid):
        assert web_rid == "42075947470"
        return fallback

    monkeypatch.setattr(client, "_request_json", _empty_request)
    monkeypatch.setattr(client, "_fetch_live_room_from_page", _fake_page)

    assert await client.get_live_room_info("42075947470") == fallback


@pytest.mark.asyncio
async def test_live_replay_info_accepts_response_variants(monkeypatch):
    client = DouyinAPIClient({"msToken": "token-1"})

    responses = [
        {"status_code": 0, "data": {"info_list": [{"replay_id": "rp-1", "title": "flat"}]}},
        {"status_code": 0, "data": {"replay_list": [{"id": "rp-2", "title": "list"}]}},
        {"status_code": 0, "data": {"replay": {"episode_id_str": "ep-3", "title": "single"}}},
    ]

    async def _fake_request_json(path, params, suppress_error=False):
        return responses.pop(0)

    monkeypatch.setattr(client, "_request_json", _fake_request_json)

    assert (await client.get_live_replay_info("ep-1", "room-1", replay_id="rp-1"))[
        "title"
    ] == "flat"
    assert (await client.get_live_replay_info("ep-2", "room-1", replay_id="rp-2"))[
        "title"
    ] == "list"
    assert (await client.get_live_replay_info("ep-3", "room-1"))["title"] == "single"


@pytest.mark.asyncio
async def test_live_replay_info_uses_strict_replay_id_match(monkeypatch):
    client = DouyinAPIClient({"msToken": "token-1"})

    async def _fake_request_json(path, params, suppress_error=False):
        return {
            "status_code": 0,
            "data": {
                "info_list": [
                    {"episode_id_str": "ep-1", "replay_id": "wrong", "title": "wrong"},
                    {"episode_id_str": "ep-1", "replay_id": "rp-1", "title": "right"},
                ]
            },
        }

    monkeypatch.setattr(client, "_request_json", _fake_request_json)

    replay = await client.get_live_replay_info("ep-1", "room-1", replay_id="rp-1")

    assert replay is not None
    assert replay["title"] == "right"


@pytest.mark.asyncio
async def test_live_replay_info_rejects_single_candidate_with_wrong_replay_id(monkeypatch):
    client = DouyinAPIClient({"msToken": "token-1"})

    async def _fake_request_json(path, params, suppress_error=False):
        return {
            "status_code": 0,
            "data": {"replay": {"episode_id_str": "ep-1", "replay_id": "wrong", "title": "wrong"}},
        }

    monkeypatch.setattr(client, "_request_json", _fake_request_json)

    assert await client.get_live_replay_info("ep-1", "room-1", replay_id="rp-1") is None


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
async def test_get_user_mix_normalizes_real_mix_infos_response(monkeypatch):
    client = DouyinAPIClient({"msToken": "token-1"})
    mix_infos = [
        {
            "mix_id": "7600000000000000001",
            "mix_name": "合集 A",
            "statis": {"updated_to_episode": 30},
            "author": {"nickname": "作者 A", "sec_uid": "SEC_AUTHOR"},
        }
    ]

    async def _fake_request_json(path, params, suppress_error=False):
        return {
            "cursor": 0,
            "extra": {"fatal_item_ids": [], "logid": "log-1", "now": 1},
            "has_more": 0,
            "log_pb": {"impr_id": "impr-1"},
            "min_cursor": 0,
            "mix_infos": mix_infos,
            "status_code": 0,
            "status_msg": None,
            "total": 1,
        }

    monkeypatch.setattr(client, "_request_json", _fake_request_json)

    data = await client.get_user_mix("SEC_AUTHOR", max_cursor=0, count=20)

    assert data["items"] == mix_infos


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response_items", "expected_items"),
    [
        ({"mix_list": [{"mix_id": "legacy"}]}, [{"mix_id": "legacy"}]),
        (
            {"mix_infos": [], "mix_list": [{"mix_id": "legacy"}]},
            [],
        ),
    ],
)
async def test_get_user_mix_preserves_legacy_fallback_and_new_field_priority(
    monkeypatch, response_items, expected_items
):
    client = DouyinAPIClient({"msToken": "token-1"})

    async def _fake_request_json(path, params, suppress_error=False):
        return {"status_code": 0, **response_items}

    monkeypatch.setattr(client, "_request_json", _fake_request_json)

    data = await client.get_user_mix("SEC_AUTHOR", max_cursor=0, count=20)

    assert data["items"] == expected_items


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
    collect_aweme_data = await client.get_collect_aweme("collect-1", max_cursor=0, count=10)
    collect_mix_data = await client.get_user_collect_mix("self", max_cursor=0, count=12)

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


class _FakeRedirectResp:
    def __init__(self, status: int, final_url: str):
        self.status = status
        self.url = final_url

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeSession:
    def __init__(self, status: int, final_url: str):
        self._status = status
        self._final_url = final_url
        self.closed = False

    def get(self, url, allow_redirects=True, timeout=None, proxy=None):
        return _FakeRedirectResp(self._status, self._final_url)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_resolve_short_url_returns_final_url_on_200():
    client = DouyinAPIClient({"msToken": "t"})
    client._session = _FakeSession(200, "https://www.douyin.com/video/123")
    resolved = await client.resolve_short_url("https://v.douyin.com/abc")
    assert resolved == "https://www.douyin.com/video/123"
    await client.close()


@pytest.mark.asyncio
async def test_resolve_short_url_returns_none_on_404():
    """HTTP 4xx 不应把错误 URL 继续传给 parser。"""
    client = DouyinAPIClient({"msToken": "t"})
    client._session = _FakeSession(404, "https://www.douyin.com/error")
    resolved = await client.resolve_short_url("https://v.douyin.com/deadbeef")
    assert resolved is None
    await client.close()


@pytest.mark.asyncio
async def test_resolve_short_url_returns_none_on_500():
    client = DouyinAPIClient({"msToken": "t"})
    client._session = _FakeSession(502, "https://www.douyin.com/error")
    resolved = await client.resolve_short_url("https://v.douyin.com/xyz")
    assert resolved is None
    await client.close()


@pytest.mark.asyncio
async def test_get_video_detail_retries_with_different_aid_on_filter():
    """When the first aid candidate returns filter_reason, get_video_detail
    should retry with the next candidate and return the detail."""
    client = DouyinAPIClient({"msToken": "t"})
    call_count = 0

    async def _fake_request_json(path, params, **kwargs):
        nonlocal call_count
        call_count += 1
        aid = params.get("aid")
        if aid == client._DETAIL_AID_CANDIDATES[0]:
            # Simulate filter on the first candidate
            return {
                "aweme_detail": None,
                "filter_detail": {
                    "filter_reason": "images_base",
                    "aweme_id": "123",
                },
                "status_code": 0,
            }
        # Second candidate returns the detail successfully
        return {
            "aweme_detail": {
                "aweme_id": "123",
                "aweme_type": 68,
                "images": [{"url_list": ["https://example.com/img.webp"]}],
            },
            "status_code": 0,
        }

    client._request_json = _fake_request_json

    detail = await client.get_video_detail("123")

    assert detail is not None
    assert detail["aweme_id"] == "123"
    assert detail["aweme_type"] == 68
    assert call_count == 2  # first call filtered, second succeeded


@pytest.mark.asyncio
async def test_get_video_detail_returns_on_first_success():
    """When the first aid candidate returns valid detail, no retry happens."""
    client = DouyinAPIClient({"msToken": "t"})
    call_count = 0

    async def _fake_request_json(path, params, **kwargs):
        nonlocal call_count
        call_count += 1
        return {
            "aweme_detail": {"aweme_id": "456", "aweme_type": 4},
            "status_code": 0,
        }

    client._request_json = _fake_request_json

    detail = await client.get_video_detail("456")

    assert detail is not None
    assert detail["aweme_id"] == "456"
    assert call_count == 1  # no retry needed
