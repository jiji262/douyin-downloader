import asyncio
import base64
import json
import logging
import sys
import types
from pathlib import Path

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
    captured = {}

    class _FakeFp:
        @staticmethod
        def generate_fingerprint(_browser):
            return "fp"

    class _FakeABogus:
        def __init__(self, fp, user_agent):
            self.fp = fp
            self.user_agent = user_agent

        def generate_abogus(self, params, body=""):
            captured.update(params=params, body=body)
            return (f"{params}&a_bogus=fake_ab", "fake_ab", self.user_agent, body)

    import core.api_client as api_module

    monkeypatch.setattr(api_module, "BrowserFingerprintGenerator", _FakeFp)
    monkeypatch.setattr(api_module, "ABogus", _FakeABogus)

    client = DouyinAPIClient({"msToken": "token-1"})
    client._abogus_enabled = True

    signed_url, _ua = client.build_signed_path(
        "/aweme/v1/web/aweme/detail/", {"a": 1}, request_data={"cursor": 3, "count": 20}
    )
    assert "a_bogus=fake_ab" in signed_url
    assert captured["body"] == "cursor=3&count=20"


def test_homepage_screenshot_bridge_emits_encoded_request(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DOUYIN_HOMEPAGE_SCREENSHOT_BRIDGE", "electron")
    client = DouyinAPIClient({"msToken": "token-1"})
    target = (tmp_path / "作者" / "主页截图.png").resolve()

    saved = asyncio.run(
        client.save_user_homepage_screenshot(
            "sec_uid_x",
            target,
            profile={
                "nickname": "测试作者",
                "follower_count": 0,
                "following_count": 12,
                "total_favorited": 345,
                "signature": "must-not-cross-bridge",
            },
        )
    )

    assert saved is True
    line = capsys.readouterr().out.strip()
    prefix = "DOUYIN_HOMEPAGE_SCREENSHOT_REQUEST "
    assert line.startswith(prefix)
    encoded = line[len(prefix) :]
    encoded += "=" * (-len(encoded) % 4)
    payload = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
    assert payload == {
        "version": 1,
        "sec_uid": "sec_uid_x",
        "save_path": str(target),
        "profile": {
            "nickname": "测试作者",
            "follower_count": 0,
            "following_count": 12,
            "total_favorited": 345,
        },
    }


def test_homepage_screenshot_playwright_captures_viewport(tmp_path, monkeypatch):
    captured = {}

    class _FakePage:
        async def goto(self, url, **kwargs):
            captured["url"] = url
            captured["goto"] = kwargs

        async def title(self):
            return "作者主页"

        async def wait_for_function(self, expression, **kwargs):
            captured["wait_for_function"] = {"expression": expression, **kwargs}

        async def evaluate(self, expression):
            captured["evaluate"] = expression
            return ""

        async def screenshot(self, **kwargs):
            captured["screenshot"] = kwargs
            Path(kwargs["path"]).write_bytes(b"png")

    class _FakeContext:
        async def add_cookies(self, cookies):
            captured["cookies"] = cookies

        async def new_page(self):
            return _FakePage()

        async def close(self):
            captured["context_closed"] = True

    class _FakeBrowser:
        async def new_context(self, **kwargs):
            captured["context"] = kwargs
            return _FakeContext()

        async def close(self):
            captured["browser_closed"] = True

    class _FakeChromium:
        async def launch(self, **kwargs):
            captured["launch"] = kwargs
            return _FakeBrowser()

    class _FakePlaywright:
        chromium = _FakeChromium()

    class _FakeManager:
        async def __aenter__(self):
            return _FakePlaywright()

        async def __aexit__(self, *_args):
            return None

    fake_playwright_pkg = types.ModuleType("playwright")
    fake_async_api = types.ModuleType("playwright.async_api")
    fake_async_api.async_playwright = lambda: _FakeManager()
    monkeypatch.setitem(sys.modules, "playwright", fake_playwright_pkg)
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_async_api)
    monkeypatch.delenv("DOUYIN_HOMEPAGE_SCREENSHOT_BRIDGE", raising=False)

    client = DouyinAPIClient({"msToken": "token-1", "sessionid_ss": "cookie"})
    target = tmp_path / "主页截图.png"
    saved = asyncio.run(client.save_user_homepage_screenshot("sec_uid_x", target))

    assert saved is True
    assert target.read_bytes() == b"png"
    assert captured["context"]["viewport"] == {"width": 1600, "height": 900}
    assert "粉丝" in captured["wait_for_function"]["expression"]
    # 就绪判定必须覆盖整个视口的图片（含作品网格），只看资料区会在网格还是
    # 灰块时就放行；与桌面版 inspectHomepageProfileContent 保持一致。
    assert "document.images" in captured["wait_for_function"]["expression"]
    assert captured["wait_for_function"]["timeout"] == 45_000
    assert "count >= 3" in captured["wait_for_function"]["expression"]
    assert captured["wait_for_function"]["arg"] == {}
    assert captured["wait_for_function"]["polling"] == 250
    assert "PROFILE_BLOCKED_REASON" in captured["evaluate"]
    assert captured["screenshot"]["full_page"] is False
    assert captured["screenshot"]["type"] == "png"
    assert captured["context_closed"] is True
    assert captured["browser_closed"] is True


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

    async def _fake_request_json(path, params, suppress_error=False, **_kwargs):
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

    async def _fake_request_json(path, params, suppress_error=False, **_kwargs):
        called_requests.append((path, dict(params)))
        return {"status_code": 0, "aweme_list": [], "has_more": 0, "max_cursor": 0}

    monkeypatch.setattr(client, "_request_json", _fake_request_json)

    like_data = await client.get_user_like("sec-1", max_cursor=0, count=20)
    mix_data = await client.get_user_mix("sec-1", max_cursor=0, count=20)
    music_data = await client.get_user_music("sec-1", max_cursor=0, count=20)

    assert [path for path, _params in called_requests] == [
        "/aweme/v1/web/aweme/favorite/",
        "/aweme/v1/web/mix/list/",
        # 合集的第二个来源，见 get_user_mix 的文档串。
        "/aweme/v1/web/series/list/",
        "/aweme/v1/web/music/list/",
    ]
    mix_params = called_requests[1][1]
    music_params = called_requests[3][1]
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

    async def _fake_request_json(path, params, suppress_error=False, **_kwargs):
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

    async def _fake_request_json(path, params, suppress_error=False, **_kwargs):
        return {"status_code": 0, **response_items}

    monkeypatch.setattr(client, "_request_json", _fake_request_json)

    data = await client.get_user_mix("SEC_AUTHOR", max_cursor=0, count=20)

    assert data["items"] == expected_items


@pytest.mark.asyncio
async def test_collect_endpoints_use_expected_paths_and_normalization(monkeypatch):
    client = DouyinAPIClient({"msToken": "token-1"})
    called_requests = []

    async def _fake_request_json(path, params, suppress_error=False, **kwargs):
        called_requests.append((path, dict(params), kwargs))
        if path == "/aweme/v1/web/aweme/listcollection/":
            return {
                "status_code": 0,
                "aweme_list": [{"aweme_id": "account-aweme-1"}],
                "has_more": 1,
                "cursor": 7,
            }
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

    account_collection_data = await client.get_user_collection("self", max_cursor=3, count=20)
    collects_data = await client.get_user_collects("self", max_cursor=0, count=10)
    collect_aweme_data = await client.get_collect_aweme("collect-1", max_cursor=0, count=10)
    collect_mix_data = await client.get_user_collect_mix("self", max_cursor=0, count=12)

    assert [path for path, _params, _kwargs in called_requests] == [
        "/aweme/v1/web/aweme/listcollection/",
        "/aweme/v1/web/collects/list/",
        "/aweme/v1/web/collects/video/list/",
        "/aweme/v1/web/mix/listcollection/",
    ]
    account_path, account_params, account_kwargs = called_requests[0]
    assert account_path == "/aweme/v1/web/aweme/listcollection/"
    assert account_params["publish_video_strategy_type"] == "2"
    assert account_params["version_code"] == "170400"
    assert account_kwargs["method"] == "POST"
    assert account_kwargs["data"] == {"count": 20, "cursor": 3}
    assert account_kwargs["request_headers"]["Content-Type"] == (
        "application/x-www-form-urlencoded"
    )
    assert account_kwargs["request_headers"]["Referer"].endswith("showTab=favorite_collection")
    assert called_requests[1][1]["count"] == 10
    assert called_requests[1][1]["version_code"] == "170400"
    assert called_requests[2][1]["collects_id"] == "collect-1"
    assert called_requests[2][1]["count"] == 10
    assert called_requests[3][1]["count"] == 12
    assert account_collection_data["items"] == [{"aweme_id": "account-aweme-1"}]
    assert account_collection_data["has_more"] is True
    assert account_collection_data["max_cursor"] == 7
    assert collects_data["items"] == [{"collects_id_str": "collect-1"}]
    assert collects_data["has_more"] is True
    assert collects_data["max_cursor"] == 9
    assert collect_aweme_data["items"] == [{"aweme_id": "aweme-1"}]
    assert collect_mix_data["items"] == [{"mix_id": "mix-1"}]


@pytest.mark.asyncio
async def test_mix_and_music_endpoints_are_normalized(monkeypatch):
    client = DouyinAPIClient({"msToken": "token-1"})

    async def _fake_request_json(path, _params, **_kwargs):
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


# ---------------------------------------------------------------------------
# Page bridge routing (desktop injects a bridge; CLI never does)
# ---------------------------------------------------------------------------


class _BridgeResult:
    def __init__(self, http_status, body, text=""):
        self.http_status = http_status
        self.body = body
        self.text = text


class _BridgeFailure(Exception):
    def __init__(self, code):
        super().__init__(f"page bridge {code}")
        self.page_bridge_code = code


class _FakeBridge:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    async def fetch(self, path, params, *, method="GET", data=None):
        self.calls.append({"path": path, "params": dict(params), "method": method, "data": data})
        if self.error is not None:
            raise self.error
        return self.result


_GATED_CALLS = [
    ("get_user_like", ("sec-1",), "/aweme/v1/web/aweme/favorite/", "GET"),
    ("get_user_collection", ("self",), "/aweme/v1/web/aweme/listcollection/", "POST"),
    ("get_user_collects", ("self",), "/aweme/v1/web/collects/list/", "GET"),
    ("get_collect_aweme", ("folder-1",), "/aweme/v1/web/collects/video/list/", "GET"),
    ("get_user_collect_mix", ("self",), "/aweme/v1/web/mix/listcollection/", "GET"),
    ("get_mix_aweme", ("mix-1",), "/aweme/v1/web/mix/aweme/", "GET"),
    # 2026-09-14 起作品详情 / 主页作品 / 合集 / 音乐端点也进了 Argus 名单。
    ("get_video_detail", ("aweme-1",), "/aweme/v1/web/aweme/detail/", "GET"),
    ("get_user_post", ("sec-1",), "/aweme/v1/web/aweme/post/", "GET"),
    ("get_user_mix", ("sec-1",), "/aweme/v1/web/mix/list/", "GET"),
    ("get_user_series", ("sec-1",), "/aweme/v1/web/series/list/", "GET"),
    ("get_user_music", ("sec-1",), "/aweme/v1/web/music/list/", "GET"),
    ("get_mix_detail", ("mix-1",), "/aweme/v1/web/mix/detail/", "GET"),
    ("get_music_detail", ("music-1",), "/aweme/v1/web/music/detail/", "GET"),
    ("get_music_aweme", ("music-1",), "/aweme/v1/web/music/aweme/", "GET"),
]


@pytest.mark.parametrize("method_name,args,path,http_method", _GATED_CALLS)
async def test_gated_methods_use_page_bridge_when_present(method_name, args, path, http_method):
    bridge = _FakeBridge(
        _BridgeResult(
            200,
            {"status_code": 0, "aweme_list": [], "has_more": 0, "aweme_detail": {"aweme_id": "1"}},
        )
    )
    client = DouyinAPIClient({"msToken": "t"}, page_bridge=bridge)

    async def _must_not_run(*_a, **_k):
        raise AssertionError("aiohttp path must not be used when a bridge is injected")

    client._request_json = _must_not_run
    await getattr(client, method_name)(*args)
    # 合集第一页会多发一次 series/list（同样必须走 bridge）。
    assert len(bridge.calls) == (2 if method_name == "get_user_mix" else 1)
    assert bridge.calls[0]["path"] == path
    assert bridge.calls[0]["method"] == http_method
    if http_method == "POST":
        assert bridge.calls[0]["data"] == {"count": 20, "cursor": 0}
    await client.close()


async def test_gated_methods_fall_back_to_request_json_without_bridge():
    client = DouyinAPIClient({"msToken": "t"})
    seen = []

    async def _fake_request_json(path, params, **kwargs):
        seen.append((path, kwargs.get("method", "GET")))
        return {"status_code": 0, "aweme_list": []}

    client._request_json = _fake_request_json
    await client.get_user_like("sec-1")
    await client.get_user_collection("self")
    assert seen == [
        ("/aweme/v1/web/aweme/favorite/", "GET"),
        ("/aweme/v1/web/aweme/listcollection/", "POST"),
    ]
    await client.close()


async def test_methods_outside_bridge_whitelist_use_aiohttp():
    """锁的是路由(白名单外的方法走 aiohttp),不是「这些端点永远不被门禁」——
    后者只是 2026-09-14 的实测结论,抖音随时可能扩面(见 docs/spec/gotchas.md)。"""
    bridge = _FakeBridge(_BridgeResult(200, {"status_code": 0}))
    client = DouyinAPIClient({"msToken": "t"}, page_bridge=bridge)

    async def _fake_request_json(path, params, **kwargs):
        return {"status_code": 0, "aweme_list": [], "has_more": 0}

    client._request_json = _fake_request_json
    await client.get_user_info("sec-1")
    await client.get_following_page("sec-1")
    assert bridge.calls == []
    await client.close()


async def test_gated_fallback_keeps_suppress_error_for_video_detail():
    """无 bridge(CLI)时 get_video_detail 仍要把 suppress_error 透传给 aiohttp 路径。"""
    client = DouyinAPIClient({"msToken": "t"})
    seen = []

    async def _fake_request_json(path, params, **kwargs):
        seen.append((params.get("aid"), kwargs.get("suppress_error")))
        return {}

    client._request_json = _fake_request_json
    assert await client.get_video_detail("aweme-1", suppress_error=True) is None
    assert [flag for _aid, flag in seen] == [True, True]
    seen.clear()
    await client.get_video_detail("aweme-1")
    # 只有最后一个 aid 候选的失败才按 error 记。
    assert [flag for _aid, flag in seen] == [True, False]
    await client.close()


async def test_bridge_login_required_body_raises_login_required_error():
    from core.api_client import LoginRequiredError

    bridge = _FakeBridge(_BridgeResult(200, {"status_code": 8, "status_msg": "用户未登录"}))
    client = DouyinAPIClient({"msToken": "t"}, page_bridge=bridge)
    with pytest.raises(LoginRequiredError):
        await client.get_user_like("sec-1")
    await client.close()


async def test_bridge_not_logged_in_maps_to_login_required_error():
    from core.api_client import LoginRequiredError

    bridge = _FakeBridge(error=_BridgeFailure("NOT_LOGGED_IN"))
    client = DouyinAPIClient({"msToken": "t"}, page_bridge=bridge)
    with pytest.raises(LoginRequiredError):
        await client.get_user_like("sec-1")
    await client.close()


async def test_bridge_other_failures_propagate_unchanged():
    failure = _BridgeFailure("TIMEOUT")
    bridge = _FakeBridge(error=failure)
    client = DouyinAPIClient({"msToken": "t"}, page_bridge=bridge)
    with pytest.raises(_BridgeFailure) as info:
        await client.get_user_like("sec-1")
    assert info.value is failure
    await client.close()


async def test_bridge_403_returns_empty_without_retry():
    bridge = _FakeBridge(
        _BridgeResult(403, None, "Blocked by ArgusSecurityPlugin Signature Not Found")
    )
    client = DouyinAPIClient({"msToken": "t"}, page_bridge=bridge)
    page = await client.get_user_like("sec-1")
    assert page["raw"] == {}
    assert page["items"] == []
    assert len(bridge.calls) == 1
    await client.close()


async def test_bridge_non_json_200_logs_warning_and_returns_empty(caplog, monkeypatch):
    # APIClient uses a namespaced logger with propagate=False (see
    # utils/logger.setup_logger); enable propagation temporarily so
    # pytest's caplog can see the warning (same pattern as test_file_manager.py).
    monkeypatch.setattr(logging.getLogger("APIClient"), "propagate", True)
    bridge = _FakeBridge(_BridgeResult(200, None, "<html>challenge</html>"))
    client = DouyinAPIClient({"msToken": "t"}, page_bridge=bridge)
    with caplog.at_level("WARNING", logger="APIClient"):
        page = await client.get_user_like("sec-1")
    assert page["raw"] == {}
    assert page["items"] == []
    assert "Non-JSON 200 response via page bridge" in caplog.text
    await client.close()


# ---------------------------------------------------------------------------
# Page bridge retry policy (复审发现 22)
#
# 门禁端点走 bridge 时原本一次请求就放弃：50 页的「全部收藏」在第 30 页碰到
# 一次 5xx 或反爬空 200,整轮同步就被写成 partial。aiohttp 路径本来就会重试
# 这两种形态并且通常第 2 次就恢复。403/429 例外——Argus 的拒绝是确定性的,
# 重试只会加速触发验证码(docs/spec/common-mistakes.md)。
# ---------------------------------------------------------------------------


class _SequenceBridge:
    """按序返回预置响应；用尽后重复最后一个。"""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    async def fetch(self, path, params, *, method="GET", data=None):
        self.calls.append(path)
        return self.results[min(len(self.calls) - 1, len(self.results) - 1)]


def _no_sleep(monkeypatch):
    """记录退避时长但不真的睡,免得单测慢 3 秒。"""
    from core import api_client as api_client_module

    slept = []

    async def fake_sleep(delay):
        slept.append(delay)

    monkeypatch.setattr(api_client_module.asyncio, "sleep", fake_sleep)
    return slept


_BRIDGE_OK_BODY = {"status_code": 0, "aweme_list": [{"aweme_id": "1"}], "has_more": 0}


async def test_bridge_server_error_is_retried_until_it_clears(monkeypatch):
    bridge = _SequenceBridge(
        [_BridgeResult(500, None, "oops"), _BridgeResult(200, _BRIDGE_OK_BODY)]
    )
    slept = _no_sleep(monkeypatch)
    client = DouyinAPIClient({"msToken": "t"}, page_bridge=bridge)

    page = await client.get_user_like("sec-1")

    assert [item["aweme_id"] for item in page["items"]] == ["1"]
    assert len(bridge.calls) == 2
    assert slept == [1]
    await client.close()


async def test_bridge_empty_200_is_retried_until_it_clears(monkeypatch):
    bridge = _SequenceBridge([_BridgeResult(200, None, ""), _BridgeResult(200, _BRIDGE_OK_BODY)])
    _no_sleep(monkeypatch)
    client = DouyinAPIClient({"msToken": "t"}, page_bridge=bridge)

    page = await client.get_collect_aweme("folder-1")

    assert [item["aweme_id"] for item in page["items"]] == ["1"]
    assert len(bridge.calls) == 2
    await client.close()


async def test_bridge_retry_budget_matches_the_aiohttp_schedule(monkeypatch):
    """与 ``_request_json`` 同一档退避:3 次尝试、1s+2s。放大预算会撞穿
    渲染进程 15s 超时(见 _RETRY_DELAYS_SECONDS 注释)。"""
    bridge = _SequenceBridge([_BridgeResult(500, None, "oops")])
    slept = _no_sleep(monkeypatch)
    client = DouyinAPIClient({"msToken": "t"}, page_bridge=bridge)

    page = await client.get_user_like("sec-1")

    assert page["raw"] == {}
    assert len(bridge.calls) == 3
    assert slept == [1, 2]
    await client.close()


@pytest.mark.parametrize("status", [403, 429])
async def test_bridge_argus_rejection_is_never_retried(monkeypatch, status):
    """Argus 拒绝是确定性的:重试只会更快撞上验证码。"""
    bridge = _SequenceBridge([_BridgeResult(status, None, "Blocked by ArgusSecurityPlugin")])
    slept = _no_sleep(monkeypatch)
    client = DouyinAPIClient({"msToken": "t"}, page_bridge=bridge)

    page = await client.get_user_like("sec-1")

    assert page["raw"] == {}
    assert len(bridge.calls) == 1
    assert slept == []
    await client.close()


@pytest.mark.parametrize("status", [400, 404])
async def test_bridge_other_client_errors_are_never_retried(monkeypatch, status):
    bridge = _SequenceBridge([_BridgeResult(status, None, "nope")])
    _no_sleep(monkeypatch)
    client = DouyinAPIClient({"msToken": "t"}, page_bridge=bridge)

    page = await client.get_user_like("sec-1")

    assert page["raw"] == {}
    assert len(bridge.calls) == 1
    await client.close()


async def test_bridge_challenge_page_200_is_never_retried(monkeypatch):
    """非空但非 JSON 的 200 = 验证码/挑战页,重试只是白烧窗口。"""
    bridge = _SequenceBridge([_BridgeResult(200, None, "<html>challenge</html>")])
    _no_sleep(monkeypatch)
    client = DouyinAPIClient({"msToken": "t"}, page_bridge=bridge)

    page = await client.get_user_like("sec-1")

    assert page["raw"] == {}
    assert len(bridge.calls) == 1
    await client.close()


@pytest.mark.parametrize(
    "method_name",
    ["get_user_collection", "get_user_collects", "get_user_collect_mix"],
)
async def test_non_self_sec_uid_returns_an_explicitly_empty_page_not_a_failure(method_name):
    """非 self 的 sec_uid 是「这个端点不适用」而不是「请求失败」。

    分页走查用空 ``raw`` 判定请求失败(``BaseUserModeStrategy._page_request_failed``),
    所以这里必须给一个非空 ``raw``,否则会被误报成限流。
    """
    client = DouyinAPIClient({"msToken": "t"})

    page = await getattr(client, method_name)("MS4wLjABAAAAother")

    assert page["items"] == []
    assert page["has_more"] is False
    assert page["raw"], "空 raw 是「请求失败」的信号,不能用来表示「不适用」"
    await client.close()


def test_normalize_paged_response_marks_a_null_item_list_as_missing():
    """``{"aweme_list": null}`` 不是「空列表」，两者不能被归一化成同一个形状。

    docs/spec/gotchas.md 记着 0.11.2 把 null 当空文件夹清空了用户的收藏夹；
    分页走查同样只能信任真 ``[]``，null 必须留下可判定的痕迹。
    """
    normalized = DouyinAPIClient._normalize_paged_response(
        {"status_code": 0, "aweme_list": None, "has_more": 1},
        item_keys=["aweme_list"],
    )

    assert normalized["items"] == []
    assert normalized["items_missing"] is True


def test_normalize_paged_response_trusts_a_real_empty_list():
    normalized = DouyinAPIClient._normalize_paged_response(
        {"status_code": 0, "aweme_list": [], "has_more": 0},
        item_keys=["aweme_list"],
    )

    assert normalized["items_missing"] is False


def test_normalize_paged_response_treats_an_absent_item_key_as_a_plain_empty_page():
    """端点不适用（``_unavailable_paged_response``）时压根没有列表键，不算缺失。"""
    normalized = DouyinAPIClient._normalize_paged_response(
        {"status_code": 0, "has_more": 0},
        item_keys=["aweme_list"],
    )

    assert normalized["items_missing"] is False


# ---------------------------------------------------------------------------
# bridge 的确定性拒绝要带出去,分页 walk 才知道「别再整页重试、别叫用户重新登录」
# ---------------------------------------------------------------------------


async def test_bridge_argus_403_is_marked_as_rejection(monkeypatch):
    from core.api_client import FailedPayload

    bridge = _SequenceBridge(
        [_BridgeResult(403, None, "Blocked by ArgusSecurityPlugin Sign Invalid")]
    )
    _no_sleep(monkeypatch)
    client = DouyinAPIClient({"msToken": "t"}, page_bridge=bridge)

    page = await client.get_user_post("sec-1")

    failure = page["raw"]
    assert failure == {} and not failure
    assert isinstance(failure, FailedPayload)
    assert failure.kind == FailedPayload.REJECTED
    assert failure.status == 403
    assert failure.via_bridge is True
    await client.close()


@pytest.mark.parametrize(
    ("status", "text"),
    [
        # 429 是限流,等一等能恢复;页面签过名也不能证明是门禁。
        (429, "Too Many Requests"),
        (429, "Blocked by ArgusSecurityPlugin"),
        # 没有 Argus 标记的 403 可能只是边缘限速(见 test_api_client_risk_control)。
        (403, "Forbidden"),
    ],
)
async def test_bridge_failure_without_argus_evidence_is_not_a_rejection(monkeypatch, status, text):
    from core.api_client import FailedPayload

    bridge = _SequenceBridge([_BridgeResult(status, None, text)])
    _no_sleep(monkeypatch)
    client = DouyinAPIClient({"msToken": "t"}, page_bridge=bridge)

    page = await client.get_user_post("sec-1")

    assert page["raw"] == {}
    assert not isinstance(page["raw"], FailedPayload)
    await client.close()


@pytest.mark.parametrize("status", [404, 500])
async def test_bridge_non_rejection_failure_stays_a_plain_empty_payload(monkeypatch, status):
    from core.api_client import FailedPayload

    bridge = _SequenceBridge([_BridgeResult(status, None, "nope")])
    _no_sleep(monkeypatch)
    client = DouyinAPIClient({"msToken": "t"}, page_bridge=bridge)

    page = await client.get_user_post("sec-1")

    assert page["raw"] == {}
    assert not isinstance(page["raw"], FailedPayload)
    await client.close()


# ---------------------------------------------------------------------------
# 作者主页「合集」= mix/list + series/list 两个来源的并集
# ---------------------------------------------------------------------------


def _series_entry(series_id: str = "7678767724759091209", name: str = "山海小司命"):
    return {
        "series_id": series_id,
        "series_name": name,
        "stats": {"updated_to_episode": 11, "play_vv": 14948320},
        "author": {"nickname": "橙子说漫", "sec_uid": "SEC_AUTHOR"},
        "cover_url": {"url_list": ["https://cover/1.jpeg"]},
        "series_type": 10,
    }


def _route_mix_and_series(mix_payload, series_pages, calls):
    """按 path 分发的 ``_request_json`` 替身；``series_pages`` 按请求顺序返回。"""

    async def _fake_request_json(path, params, suppress_error=False, **_kwargs):
        calls.append((path, dict(params)))
        if path == "/aweme/v1/web/mix/list/":
            return mix_payload
        if path == "/aweme/v1/web/series/list/":
            index = min(len([c for c in calls if c[0] == path]) - 1, len(series_pages) - 1)
            return series_pages[index]
        raise AssertionError(f"unexpected path {path}")

    return _fake_request_json


async def test_get_user_mix_merges_series_list_entries(monkeypatch):
    """mix_infos 为 null、合集全在 series_infos 里的作者（橙子说漫形态）。"""

    client = DouyinAPIClient({"msToken": "token-1"})
    calls = []
    monkeypatch.setattr(
        client,
        "_request_json",
        _route_mix_and_series(
            {"status_code": 0, "mix_infos": None, "has_more": 0, "cursor": 0},
            [{"status_code": 0, "series_infos": [_series_entry()], "has_more": 0, "cursor": 0}],
            calls,
        ),
    )

    data = await client.get_user_mix("SEC_AUTHOR", max_cursor=0, count=20)

    assert [path for path, _ in calls] == [
        "/aweme/v1/web/mix/list/",
        "/aweme/v1/web/series/list/",
    ]
    series_params = calls[1][1]
    assert series_params["sec_user_id"] == "SEC_AUTHOR"
    # 少了 read_new_mix=true，抖音恒回 series_infos=null（2026-09-16 实测）。
    assert series_params["read_new_mix"] == "true"
    assert series_params["cursor"] == 0
    assert series_params["count"] == 20
    assert "max_cursor" not in series_params

    assert data["items"] == [
        {
            "mix_id": "7678767724759091209",
            "mix_name": "山海小司命",
            "statis": {"updated_to_episode": 11, "play_vv": 14948320},
            "author": {"nickname": "橙子说漫", "sec_uid": "SEC_AUTHOR"},
        }
    ]
    assert data["aweme_list"] == data["items"]
    assert data["items_missing"] is False


async def test_get_user_mix_dedupes_series_entries_already_in_mix_list(monkeypatch):
    client = DouyinAPIClient({"msToken": "token-1"})
    calls = []
    monkeypatch.setattr(
        client,
        "_request_json",
        _route_mix_and_series(
            {
                "status_code": 0,
                "mix_infos": [{"mix_info": {"mix_id": "DUP", "mix_name": "已在 mix/list"}}],
                "has_more": 0,
                "cursor": 0,
            },
            [
                {
                    "status_code": 0,
                    "series_infos": [_series_entry("DUP", "重复"), _series_entry("NEW", "新的")],
                    "has_more": 0,
                    "cursor": 0,
                }
            ],
            calls,
        ),
    )

    data = await client.get_user_mix("SEC_AUTHOR", max_cursor=0, count=20)

    assert [item.get("mix_id") or item["mix_info"]["mix_id"] for item in data["items"]] == [
        "DUP",
        "NEW",
    ]


async def test_get_user_mix_skips_series_list_on_later_pages(monkeypatch):
    """series/list 只在第一页取一次：翻页游标属于 mix/list，混用会重复拉取。"""

    client = DouyinAPIClient({"msToken": "token-1"})
    calls = []
    monkeypatch.setattr(
        client,
        "_request_json",
        _route_mix_and_series(
            {"status_code": 0, "mix_infos": [{"mix_id": "M2"}], "has_more": 0, "cursor": 0},
            [{"status_code": 0, "series_infos": [_series_entry()], "has_more": 0, "cursor": 0}],
            calls,
        ),
    )

    data = await client.get_user_mix("SEC_AUTHOR", max_cursor=20, count=20)

    assert [path for path, _ in calls] == ["/aweme/v1/web/mix/list/"]
    assert data["items"] == [{"mix_id": "M2"}]


async def test_get_user_mix_walks_every_series_page(monkeypatch):
    """series/list 的下一页游标是时间戳，必须原样回传（2026-09-16 实测）。"""

    client = DouyinAPIClient({"msToken": "token-1"})
    calls = []
    monkeypatch.setattr(
        client,
        "_request_json",
        _route_mix_and_series(
            {"status_code": 0, "mix_infos": [], "has_more": 0, "cursor": 0},
            [
                {
                    "status_code": 0,
                    "series_infos": [_series_entry("S1", "第一页")],
                    "has_more": 1,
                    "cursor": 1787287757,
                },
                {
                    "status_code": 0,
                    "series_infos": [_series_entry("S2", "第二页")],
                    "has_more": 0,
                    "cursor": 0,
                },
            ],
            calls,
        ),
    )

    data = await client.get_user_mix("SEC_AUTHOR", max_cursor=0, count=20)

    series_calls = [params for path, params in calls if path == "/aweme/v1/web/series/list/"]
    assert [params["cursor"] for params in series_calls] == [0, 1787287757]
    assert [item["mix_id"] for item in data["items"]] == ["S1", "S2"]


async def test_get_user_mix_stops_series_walk_at_page_cap(monkeypatch):
    """has_more 恒为 1 时不能无限翻，页数上限兜住。"""

    from core.api_client import _SERIES_LIST_MAX_PAGES

    client = DouyinAPIClient({"msToken": "token-1"})
    calls = []

    async def _fake_request_json(path, params, suppress_error=False, **_kwargs):
        calls.append((path, dict(params)))
        if path == "/aweme/v1/web/mix/list/":
            return {"status_code": 0, "mix_infos": [], "has_more": 0, "cursor": 0}
        index = len([c for c in calls if c[0] == path])
        return {
            "status_code": 0,
            "series_infos": [_series_entry(f"S{index}", f"第 {index} 页")],
            "has_more": 1,
            "cursor": 1000 + index,
        }

    monkeypatch.setattr(client, "_request_json", _fake_request_json)

    data = await client.get_user_mix("SEC_AUTHOR", max_cursor=0, count=20)

    series_calls = [c for c in calls if c[0] == "/aweme/v1/web/series/list/"]
    assert len(series_calls) == _SERIES_LIST_MAX_PAGES
    assert len(data["items"]) == _SERIES_LIST_MAX_PAGES


async def test_get_user_mix_keeps_series_items_when_mix_list_request_fails(monkeypatch):
    """mix/list 失败但 series/list 有内容时，返回已拿到的部分而不是空页。"""

    client = DouyinAPIClient({"msToken": "token-1"})
    calls = []
    monkeypatch.setattr(
        client,
        "_request_json",
        _route_mix_and_series(
            {},
            [{"status_code": 0, "series_infos": [_series_entry()], "has_more": 0, "cursor": 0}],
            calls,
        ),
    )

    data = await client.get_user_mix("SEC_AUTHOR", max_cursor=0, count=20)

    assert [item["mix_id"] for item in data["items"]] == ["7678767724759091209"]


async def test_get_user_mix_keeps_empty_page_shape_when_both_sources_empty(monkeypatch):
    """两个来源都真空时仍是「翻到底了」，不能伪造失败。"""

    client = DouyinAPIClient({"msToken": "token-1"})
    calls = []
    monkeypatch.setattr(
        client,
        "_request_json",
        _route_mix_and_series(
            {"status_code": 0, "mix_infos": [], "has_more": 0, "cursor": 0},
            [{"status_code": 0, "series_infos": None, "has_more": 0, "cursor": 0}],
            calls,
        ),
    )

    data = await client.get_user_mix("SEC_AUTHOR", max_cursor=0, count=20)

    assert data["items"] == []
    assert data["raw"]
    assert data["status_code"] == 0


async def test_get_user_mix_keeps_mix_items_when_series_list_raises(monkeypatch):
    """series/list 超时不能把已经拿到的 mix/list 合集一起丢掉。"""

    client = DouyinAPIClient({"msToken": "token-1"})

    async def _fake_request_json(path, params, suppress_error=False, **_kwargs):
        if path == "/aweme/v1/web/mix/list/":
            return {"status_code": 0, "mix_infos": [{"mix_id": "M1"}], "has_more": 0, "cursor": 0}
        raise RuntimeError("bridge timeout")

    monkeypatch.setattr(client, "_request_json", _fake_request_json)

    data = await client.get_user_mix("SEC_AUTHOR", max_cursor=0, count=20)

    assert data["items"] == [{"mix_id": "M1"}]


async def test_get_user_mix_propagates_series_failure_when_mix_list_is_empty(monkeypatch):
    """两个来源都没内容时失败必须上抛，不能被当成「没有公开合集」。"""

    client = DouyinAPIClient({"msToken": "token-1"})

    async def _fake_request_json(path, params, suppress_error=False, **_kwargs):
        if path == "/aweme/v1/web/mix/list/":
            return {"status_code": 0, "mix_infos": None, "has_more": 0, "cursor": 0}
        raise RuntimeError("bridge timeout")

    monkeypatch.setattr(client, "_request_json", _fake_request_json)

    with pytest.raises(RuntimeError, match="bridge timeout"):
        await client.get_user_mix("SEC_AUTHOR", max_cursor=0, count=20)


@pytest.mark.parametrize(
    ("series_payload", "label"),
    [
        ({}, "HTTP 失败回空 dict"),
        ({"status_code": 2154, "series_infos": None}, "服务端报错码"),
    ],
)
async def test_get_user_mix_surfaces_series_failure_when_nothing_else_found(
    monkeypatch, series_payload, label
):
    """series/list 的失败大多不是异常而是空 payload，不能被当成「没有合集」。"""

    client = DouyinAPIClient({"msToken": "token-1"})
    calls = []
    monkeypatch.setattr(
        client,
        "_request_json",
        _route_mix_and_series(
            {"status_code": 0, "mix_infos": None, "has_more": 0, "cursor": 0},
            [series_payload],
            calls,
        ),
    )

    data = await client.get_user_mix("SEC_AUTHOR", max_cursor=0, count=20)

    from core.api_client import page_request_failed

    assert data["items"] == [], label
    assert page_request_failed(data), label


async def test_get_user_mix_surfaces_argus_rejection_from_series_list(monkeypatch):
    from core.api_client import FailedPayload, page_request_failed

    client = DouyinAPIClient({"msToken": "token-1"})
    rejected = FailedPayload(FailedPayload.REJECTED, status=403, via_bridge=True)
    calls = []
    monkeypatch.setattr(
        client,
        "_request_json",
        _route_mix_and_series(
            {"status_code": 0, "mix_infos": None, "has_more": 0, "cursor": 0},
            [rejected],
            calls,
        ),
    )

    data = await client.get_user_mix("SEC_AUTHOR", max_cursor=0, count=20)

    assert page_request_failed(data)
    assert data["raw"] is rejected


async def test_get_user_mix_keeps_mix_items_when_series_page_fails(monkeypatch):
    """mix/list 已经有合集时，series/list 的失败只记日志，不能清空结果。"""

    client = DouyinAPIClient({"msToken": "token-1"})
    calls = []
    monkeypatch.setattr(
        client,
        "_request_json",
        _route_mix_and_series(
            {"status_code": 0, "mix_infos": [{"mix_id": "M1"}], "has_more": 0, "cursor": 0},
            [{}],
            calls,
        ),
    )

    data = await client.get_user_mix("SEC_AUTHOR", max_cursor=0, count=20)

    assert data["items"] == [{"mix_id": "M1"}]


async def test_get_user_mix_series_cursor_is_not_shadowed_by_max_cursor(monkeypatch):
    """series/list 真出现 max_cursor=0 时，翻页必须继续用 cursor。"""

    client = DouyinAPIClient({"msToken": "token-1"})
    calls = []
    monkeypatch.setattr(
        client,
        "_request_json",
        _route_mix_and_series(
            {"status_code": 0, "mix_infos": [], "has_more": 0, "cursor": 0},
            [
                {
                    "status_code": 0,
                    "series_infos": [_series_entry("S1", "第一页")],
                    "has_more": 1,
                    "cursor": 1787287757,
                    "max_cursor": 0,
                },
                {
                    "status_code": 0,
                    "series_infos": [_series_entry("S2", "第二页")],
                    "has_more": 0,
                    "cursor": 0,
                },
            ],
            calls,
        ),
    )

    data = await client.get_user_mix("SEC_AUTHOR", max_cursor=0, count=20)

    series_calls = [params for path, params in calls if path == "/aweme/v1/web/series/list/"]
    assert [params["cursor"] for params in series_calls] == [0, 1787287757]
    assert [item["mix_id"] for item in data["items"]] == ["S1", "S2"]
