"""hot / search / user：网络层全部用假 client，不发任何请求。"""

import json

import pytest

from agent_mode import discovery_search, entry, state
from core.api_client import LoginRequiredError

FAKE_SESSION = "fake-session-value-for-discovery-test"
SEC_UID = "MS4wLjABAAAA_fake-sec-uid_0123456789"
PROFILE_URL = "https://www.douyin.com/user/%s?from_tab_name=main" % SEC_UID
SHORT_URL = "https://v.douyin.com/AbCdEfG/"
CDN_URL = "https://v26-web.douyinvod.com/fake/video.mp4?a=1"
SEARCH_ITEM_KEYS = "aweme_id desc author stats create_time duration_s media_type share_url".split()
USER_COUNTS = dict(follower_count=1000, following_count="20", aweme_count=33, total_favorited=4444)
USER_KEYS = "sec_uid uid unique_id nickname signature".split() + list(USER_COUNTS)
DEVICE = {"ttwid": "device-only"}
LINKS = {"url_list": [CDN_URL]}
STATS = {"digg_count": 12, "comment_count": "3", "share_count": 4, "collect_count": 5}
LEAK_MARKERS = ("play_addr", "douyinvod", "url_list", "download_addr", "douyinpic", "raw")


def _aweme(aweme_id, **extra):
    """接近真实搜索结果的一条：带直链子树，用来验证投影不外泄。"""
    item = {
        "aweme_id": str(aweme_id),
        "desc": "标题 %s\n第二行\x00" % aweme_id,
        "create_time": 1700000000,
        "aweme_type": 0,
        "author": {"nickname": "作者\t甲", "sec_uid": SEC_UID, "avatar": LINKS},
        "statistics": dict(STATS, play_count=99),
        "video": {"duration": 15340, "play_addr": LINKS, "download_addr": LINKS},
    }
    item.update(extra)
    return item


def _page(items, *, has_more=False, cursor=0, status_code=0):
    raw = {"status_code": status_code, "data": [{"aweme_info": item} for item in items]}
    page = {"items": items, "has_more": has_more, "max_cursor": cursor, "raw": raw}
    return dict(page, status_code=status_code)


FAILED_PAGE = dict(_page([]), raw={})


class _FakeClient:
    """按测试设置的类属性应答；pages 里的异常实例会被抛出。"""

    hot_items = pages = user = resolved = error = None
    calls = []

    def __init__(self, cookies, proxy=None):
        type(self).calls.append(("init", sorted(cookies), proxy))

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def _record(self, *call):
        type(self).calls.append(call)
        if type(self).error is not None:
            raise type(self).error

    async def get_hot_search_board(self):
        self._record("hot")
        items = type(self).hot_items or []
        return {"items": items, "has_more": False, "status_code": 0, "raw": {"word_list": items}}

    async def search_aweme(self, keyword, **kwargs):
        self._record("search", keyword, kwargs)
        page = type(self).pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page

    async def get_user_info(self, sec_uid):
        self._record("user", sec_uid)
        return type(self).user

    async def resolve_short_url(self, url):
        self._record("resolve_short_url", url)
        return type(self).resolved


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DOUYIN_DL_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("DOUYIN_PROXY", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(discovery_search, "SEARCH_PAGE_INTERVAL_S", 0)
    fake = type("FakeClient", (_FakeClient,), {"calls": []})
    monkeypatch.setattr("core.api_client.DouyinAPIClient", fake)
    return fake


def _run(capsys, argv):
    code = entry.main(argv)
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    for marker in LEAK_MARKERS:
        assert marker not in out
    assert FAKE_SESSION not in out
    return code, json.loads(out)


def _fail(capsys, argv, expected):
    code, result = _run(capsys, argv)
    assert (code, result["error"]["code"], result["ok"]) == expected + (False,)
    return result


def _login():
    state.write_cookies({"sessionid": FAKE_SESSION, "ttwid": "fake-ttwid"})


def _calls(client, name):
    return [call for call in client.calls if call[0] == name]


def test_hot_projects_fields_and_works_anonymously(client, capsys):
    client.hot_items = [
        dict(word="第一\n条", hot_value=12345678, label=3, video_count=7, word_cover=LINKS),
        {"word": "second word", "hot_value": "500", "label": 0, "sentence_id": "999"},
        {"word": ""},
        "not-a-dict",
    ]
    code, result = _run(capsys, ["hot"])
    assert code == 0 and result["ok"] is True and result["verb"] == "hot"
    first = dict(rank=1, word="第一 条", hot_value=12345678, label=3, video_count=7)
    first["search_url"] = "https://www.douyin.com/search/%E7%AC%AC%E4%B8%80%20%E6%9D%A1"
    second = dict(rank=2, word="second word", hot_value=500)
    second["search_url"] = "https://www.douyin.com/search/second%20word"
    assert result["data"] == {"items": [first, second], "count": 2}
    assert client.calls == [("init", [], ""), ("hot",)]


def test_hot_truncates_to_max_and_sends_saved_cookies(client, capsys):
    _login()
    client.hot_items = [{"word": "w%d" % index, "hot_value": index} for index in range(10)]
    code, result = _run(capsys, ["hot", "--max", "3"])
    assert code == 0 and result["data"]["count"] == 3
    assert [item["rank"] for item in result["data"]["items"]] == [1, 2, 3]
    assert client.calls[0] == ("init", ["sessionid", "ttwid"], "")


def test_hot_empty_is_upstream_empty_with_retry_action(client, capsys):
    result = _fail(capsys, ["hot"], (5, "UPSTREAM_EMPTY"))
    action = result["error"]["action"]
    assert result["error"]["retryable"] is True and action["kind"] == "retry_later"
    assert action["retry_after_s"] > 0 and action["hint"]
    assert result["data"] == {"items": [], "count": 0}


@pytest.mark.parametrize("argv", ["hot --max 0", "hot --max 101", "search kw --max 51"])
def test_max_out_of_range_is_usage(client, capsys, argv):
    _login()
    _fail(capsys, argv.split(), (2, "USAGE"))
    assert _calls(client, "init") == []


@pytest.mark.parametrize("case", [("search", None), ("search", DEVICE), ("user", None)])
def test_missing_credentials_is_login_required_and_sends_nothing(client, capsys, case):
    if case[1]:
        state.write_cookies(case[1])
    argv = [case[0], SHORT_URL]
    result = _fail(capsys, argv, (3, "LOGIN_REQUIRED"))
    assert result["error"]["action"] == {"kind": "run_command", "command": "douyin-dl agent login"}
    assert client.calls == []


@pytest.mark.parametrize("argv", [["search", "kw"], ["user", SEC_UID], ["hot"]])
def test_server_side_login_rejection_is_login_required(client, capsys, argv):
    _login()
    client.error = LoginRequiredError(8, "用户未登录", "/aweme/v1/web/fake/")
    result = _fail(capsys, argv, (3, "LOGIN_REQUIRED"))
    assert result["error"]["action"]["command"] == "douyin-dl agent login"
    assert result["data"] == {}


def test_search_projects_items_without_media_links(client, capsys):
    _login()
    gallery = _aweme(7300000000000000002, aweme_type=68, images=[LINKS])
    client.pages = [_page([_aweme(7300000000000000001, desc="长" * 500), gallery, {"desc": "x"}])]
    code, result = _run(capsys, ["search", "猫 咪"])
    data = result["data"]
    assert (code, data["keyword"], data["count"], data["has_more"]) == (0, "猫 咪", 2, False)
    video, note = data["items"]
    assert list(video) == SEARCH_ITEM_KEYS
    assert video["aweme_id"] == "7300000000000000001" and video["desc"] == "长" * 200 + "…"
    assert video["author"] == {"nickname": "作者 甲", "sec_uid": SEC_UID}
    assert video["stats"] == {"digg": 12, "comment": 3, "share": 4, "collect": 5}
    assert (video["create_time"], video["duration_s"]) == ("2023-11-14T22:13:20Z", 15.3)
    assert video["share_url"] == "https://www.douyin.com/video/7300000000000000001"
    assert note["share_url"] == "https://www.douyin.com/note/7300000000000000002"
    assert (video["media_type"], note["media_type"]) == ("video", "gallery")
    assert note["desc"] == "标题 7300000000000000002 第二行"


def test_search_pages_until_max_and_passes_filters(client, capsys):
    _login()
    client.pages = [
        _page([_aweme(index) for index in range(1, 11)], has_more=True, cursor=10),
        # 第二页带一条重复，去重后仍要凑够 12 条
        _page([_aweme(index) for index in range(10, 21)], has_more=True, cursor=20),
    ]
    argv = ["search", "kw", "--max", "12", "--sort", "latest", "--publish-days", "7"]
    code, result = _run(capsys, argv)
    assert code == 0
    assert [item["aweme_id"] for item in result["data"]["items"]] == [str(i) for i in range(1, 13)]
    assert result["data"]["count"] == 12 and result["data"]["has_more"] is True
    expected = {"count": discovery_search.SEARCH_PAGE_SIZE, "sort_type": 2, "publish_time": 7}
    assert _calls(client, "search") == [
        ("search", "kw", dict(expected, offset=0)),
        ("search", "kw", dict(expected, offset=10)),
    ]


@pytest.mark.parametrize("cursor", [0, 10])
def test_search_stops_at_natural_end_or_stuck_cursor(client, capsys, cursor):
    _login()
    # cursor=10 是自然到底（has_more=False）；cursor=0 是游标不前进，不能死循环
    client.pages = [_page([_aweme(1), _aweme(2)], has_more=not cursor, cursor=cursor)]
    code, result = _run(capsys, ["search", "kw"])
    assert code == 0
    assert result["data"]["count"] == 2 and result["data"]["has_more"] is False
    assert len(_calls(client, "search")) == 1


@pytest.mark.parametrize("page", [FAILED_PAGE, _page([], status_code=2154)])
def test_search_failed_first_page_is_upstream_empty(client, capsys, page):
    _login()
    client.pages = [page]
    result = _fail(capsys, ["search", "kw"], (5, "UPSTREAM_EMPTY"))
    assert result["error"]["action"]["kind"] == "retry_later"
    assert result["data"] == {"keyword": "kw", "items": [], "count": 0, "has_more": False}


@pytest.mark.parametrize("second", [FAILED_PAGE, RuntimeError("boom " + FAKE_SESSION)])
def test_search_failure_after_first_page_is_partial_with_items(client, capsys, second):
    _login()
    client.pages = [_page([_aweme(1), _aweme(2)], has_more=True, cursor=10), second]
    result = _fail(capsys, ["search", "kw", "--max", "30"], (6, "PARTIAL"))
    assert result["data"]["count"] == 2 and result["data"]["has_more"] is True
    assert [item["aweme_id"] for item in result["data"]["items"]] == ["1", "2"]


USER_RAW = dict(USER_COUNTS, sec_uid=SEC_UID, uid="1234567890", unique_id="", short_id="88886666")
USER_RAW.update(nickname="某\u2028用户", signature="简介\n" + "很长" * 200, ip_location="北京")
USER_RAW["avatar_larger"] = {"url_list": ["https://p3.douyinpic.com/fake.jpeg"]}


@pytest.mark.parametrize("target", [SEC_UID, PROFILE_URL, "看看这个 %s 主页" % PROFILE_URL])
def test_user_accepts_sec_uid_and_urls(client, capsys, target):
    state.write_cookies(DEVICE)
    client.user = dict(USER_RAW)
    code, result = _run(capsys, ["user", target])
    data = result["data"]
    assert code == 0 and list(data) == USER_KEYS + ["ip_location", "note"]
    assert (data["sec_uid"], data["uid"], data["unique_id"]) == (SEC_UID, "1234567890", "88886666")
    assert (data["nickname"], data["ip_location"]) == ("某 用户", "北京")
    assert len(data["signature"]) == 201 and "\n" not in data["signature"]
    assert [data[field] for field in USER_COUNTS] == [1000, 20, 33, 4444]
    assert "Argus-gated" in data["note"]
    assert client.calls == [("init", ["ttwid"], ""), ("user", SEC_UID)]


def test_user_resolves_short_link_once(client, capsys):
    _login()
    client.resolved = PROFILE_URL
    client.user = {"nickname": "n"}
    code, result = _run(capsys, ["user", "http://v.douyin.com/AbCdEfG/"])
    assert code == 0 and result["data"]["sec_uid"] == SEC_UID
    # 短链用不带 cookie 的 client 解析，且强制 https
    assert client.calls[:2] == [("init", [], ""), ("resolve_short_url", SHORT_URL)]
    assert "ip_location" not in result["data"] and result["data"]["follower_count"] is None
    assert _calls(client, "resolve_short_url") == [("resolve_short_url", SHORT_URL)]
    assert _calls(client, "user") == [("user", SEC_UID)]


@pytest.mark.parametrize("target", ["https://www.douyin.com/video/7300", "https://e.com/a", "a b"])
def test_user_rejects_non_profile_targets(client, capsys, target):
    _login()
    _fail(capsys, ["user", target], (2, "USAGE"))
    assert _calls(client, "user") == []


def test_user_empty_profile_and_unresolved_link_are_upstream_empty(client, capsys):
    _login()
    result = _fail(capsys, ["user", SEC_UID], (5, "UPSTREAM_EMPTY"))
    assert result["error"]["action"]["kind"] == "retry_later"
    assert result["data"] == {"sec_uid": SEC_UID}
    assert _fail(capsys, ["user", SHORT_URL], (5, "UPSTREAM_EMPTY"))["data"] == {"sec_uid": None}
