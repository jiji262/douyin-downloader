"""主页作品分页完整性回归。

覆盖「主页视频不能获取全部视频」的三条路径：瞬时失败页被当成列表结束、
硬截断被报成干净成功、置顶作品被无声丢弃且无处解释。
"""

import asyncio
from typing import Any, Dict, List, Optional

from core.api_client import DouyinAPIClient
from core.user_modes import post_strategy as post_strategy_module
from core.user_modes.post_strategy import PostUserModeStrategy


class _NoopRateLimiter:
    async def acquire(self):
        return


def _make_aweme(aweme_id: str, *, is_top: int = 0) -> Dict[str, Any]:
    return {
        "aweme_id": aweme_id,
        "create_time": 1700000000,
        "is_top": is_top,
        "video": {"play_addr": {"url_list": ["https://example.com/video.mp4"]}},
    }


def _api_page(
    items: List[Dict[str, Any]],
    *,
    has_more: bool,
    max_cursor: int,
) -> Dict[str, Any]:
    """复刻 ``DouyinAPIClient._normalize_paged_response`` 的成功页形态。"""
    raw = {
        "aweme_list": items,
        "has_more": 1 if has_more else 0,
        "max_cursor": max_cursor,
        "status_code": 0,
    }
    return {
        "items": items,
        "aweme_list": items,
        "has_more": has_more,
        "max_cursor": max_cursor,
        "status_code": 0,
        "source": "api",
        "risk_flags": {"login_tip": False, "verify_page": False},
        "raw": raw,
    }


def _failed_page() -> Dict[str, Any]:
    """复刻请求失败页：``_request_json`` 重试耗尽返回 ``{}``，raw 为空。"""
    return {
        "items": [],
        "aweme_list": [],
        "has_more": False,
        "max_cursor": 0,
        "status_code": 0,
        "source": "api",
        "risk_flags": {"login_tip": False, "verify_page": False},
        "raw": {},
    }


def _normalized_page(raw: Dict[str, Any]) -> Dict[str, Any]:
    """用生产归一化器造页面，保证测的是真实形状而不是手搓的假页。"""
    return DouyinAPIClient._normalize_paged_response(raw, item_keys=["aweme_list"])


class _Cfg:
    def __init__(self, data: Dict[str, Any]):
        self._data = data

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


class _ScriptedAPI:
    """按 (cursor -> 页面序列) 回放，序列耗尽后重复最后一页。"""

    def __init__(self, pages_by_cursor: Dict[int, List[Dict[str, Any]]]):
        self._pages = {cursor: list(pages) for cursor, pages in pages_by_cursor.items()}
        self.calls: List[int] = []

    async def get_user_post(self, _sec_uid, max_cursor=0, count=20):
        self.calls.append(max_cursor)
        pages = self._pages.get(max_cursor) or [_failed_page()]
        return pages.pop(0) if len(pages) > 1 else pages[0]


class _FakeDownloader:
    def __init__(
        self,
        api_client: Any,
        *,
        download_pinned: bool = False,
        browser_reason: Optional[str] = None,
        recovered_items: Optional[List[Dict[str, Any]]] = None,
    ):
        self.api_client = api_client
        self.rate_limiter = _NoopRateLimiter()
        self.database = None
        self.steps: List[tuple[str, str]] = []
        self.recover_calls = 0
        self._download_pinned = download_pinned
        self._browser_reason = browser_reason
        self._recovered_items = recovered_items or []
        self.config = _Cfg(
            {
                "number": {"post": 0},
                "increase": {"post": False},
                "browser_fallback": {"enabled": True},
            }
        )

    def _progress_update_step(self, step: str, detail: str = "") -> None:
        self.steps.append((step, detail))

    def _filter_pinned_items(self, items):
        if self._download_pinned:
            return items
        return [item for item in items if not item.get("is_top")]

    def _filter_by_time(self, items):
        return items

    def _limit_count(self, items, _mode):
        return items

    def _browser_recovery_unavailable_reason(self) -> Optional[str]:
        return self._browser_reason

    async def _recover_user_post_with_browser(
        self, _sec_uid, _user_info, aweme_list, *, item_filter=None
    ):
        self.recover_calls += 1
        aweme_list.extend(self._recovered_items)

    def details(self) -> str:
        return "\n".join(detail for _step, detail in self.steps)


def _run_collect(strategy, user_info=None):
    return asyncio.run(strategy.collect_items("sec_uid_x", user_info or {"uid": "uid-1"}))


def test_transient_page_failure_is_retried_then_succeeds(monkeypatch):
    monkeypatch.setattr(post_strategy_module, "_POST_PAGE_RETRY_BACKOFF_SECONDS", (0.0, 0.0))
    api = _ScriptedAPI(
        {
            0: [_api_page([_make_aweme("a1")], has_more=True, max_cursor=100)],
            100: [
                _failed_page(),
                _api_page([_make_aweme("a2")], has_more=False, max_cursor=0),
            ],
        }
    )
    downloader = _FakeDownloader(api)
    strategy = PostUserModeStrategy(downloader)

    items = _run_collect(strategy)

    assert [item["aweme_id"] for item in items] == ["a1", "a2"]
    assert api.calls == [0, 100, 100]
    assert strategy.incomplete_reason is None
    assert downloader.recover_calls == 0


def test_hard_truncation_marks_walk_incomplete(monkeypatch):
    monkeypatch.setattr(post_strategy_module, "_POST_PAGE_RETRY_BACKOFF_SECONDS", (0.0, 0.0))
    api = _ScriptedAPI(
        {
            0: [_api_page([_make_aweme("a1")], has_more=True, max_cursor=100)],
            100: [_failed_page()],
        }
    )
    downloader = _FakeDownloader(api)
    strategy = PostUserModeStrategy(downloader)

    items = _run_collect(strategy)

    assert [item["aweme_id"] for item in items] == ["a1"]
    # 重试用尽后才认输，浏览器回补仍会尝试一次。
    assert api.calls == [0, 100, 100, 100]
    assert downloader.recover_calls == 1
    assert strategy.incomplete_reason
    assert "不完整" in strategy.incomplete_reason
    assert strategy.incomplete_reason in downloader.details()


def test_profile_count_mismatch_alone_does_not_mark_incomplete():
    api = _ScriptedAPI({0: [_api_page([_make_aweme("a1")], has_more=False, max_cursor=0)]})
    downloader = _FakeDownloader(api)
    strategy = PostUserModeStrategy(downloader)

    items = _run_collect(strategy, {"uid": "uid-1", "aweme_count": 164})

    assert [item["aweme_id"] for item in items] == ["a1"]
    # aweme_count 不可靠（经常只返回 20），只能提示不能定性。
    assert strategy.incomplete_reason is None
    assert "164" in downloader.details()


def test_pinned_exclusion_count_is_reported():
    page = _api_page(
        [_make_aweme("a1"), _make_aweme("top-1", is_top=1), _make_aweme("a2")],
        has_more=False,
        max_cursor=0,
    )
    api = _ScriptedAPI({0: [page]})
    downloader = _FakeDownloader(api)
    strategy = PostUserModeStrategy(downloader)

    items = _run_collect(strategy, {"uid": "uid-1", "aweme_count": 3})

    assert [item["aweme_id"] for item in items] == ["a1", "a2"]
    assert strategy.pinned_excluded == 1
    details = downloader.details()
    assert "置顶" in details and "1" in details


def test_pinned_setting_enabled_keeps_every_item_and_reports_zero():
    page = _api_page(
        [_make_aweme("a1"), _make_aweme("top-1", is_top=1)],
        has_more=False,
        max_cursor=0,
    )
    api = _ScriptedAPI({0: [page]})
    downloader = _FakeDownloader(api, download_pinned=True)
    strategy = PostUserModeStrategy(downloader)

    items = _run_collect(strategy, {"uid": "uid-1", "aweme_count": 2})

    assert [item["aweme_id"] for item in items] == ["a1", "top-1"]
    assert strategy.pinned_excluded == 0
    assert "置顶" not in downloader.details()


def test_browser_recovery_step_is_skipped_when_backend_unavailable(monkeypatch):
    monkeypatch.setattr(post_strategy_module, "_POST_PAGE_RETRY_BACKOFF_SECONDS", (0.0, 0.0))
    api = _ScriptedAPI(
        {
            0: [_api_page([_make_aweme("a1")], has_more=True, max_cursor=100)],
            100: [_failed_page()],
        }
    )
    downloader = _FakeDownloader(api, browser_reason="当前版本未内置浏览器组件")
    strategy = PostUserModeStrategy(downloader)

    _run_collect(strategy)

    assert "尝试浏览器回补" not in downloader.details()


def test_error_status_page_is_not_treated_as_end_of_list(monkeypatch):
    """HTTP 200 + ``status_code=2154`` + 空 ``aweme_list``：raw 非空，旧逻辑当成翻到底。"""
    monkeypatch.setattr(post_strategy_module, "_POST_PAGE_RETRY_BACKOFF_SECONDS", (0.0, 0.0))
    api = _ScriptedAPI(
        {
            0: [_api_page([_make_aweme("a1")], has_more=True, max_cursor=100)],
            100: [_normalized_page({"status_code": 2154, "aweme_list": [], "has_more": 1})],
        }
    )
    downloader = _FakeDownloader(api)
    strategy = PostUserModeStrategy(downloader)

    items = _run_collect(strategy)

    assert [item["aweme_id"] for item in items] == ["a1"]
    assert api.calls == [0, 100, 100, 100], "接口报错页要按瞬时失败重试，而不是直接收工"
    assert downloader.recover_calls == 1
    assert strategy.incomplete_reason and "不完整" in strategy.incomplete_reason


def test_null_aweme_list_page_is_not_treated_as_end_of_list(monkeypatch):
    """``{"aweme_list": null}`` 经归一化后 items 也是 []，但它不是可信的空列表。"""
    monkeypatch.setattr(post_strategy_module, "_POST_PAGE_RETRY_BACKOFF_SECONDS", (0.0, 0.0))
    api = _ScriptedAPI(
        {
            0: [_api_page([_make_aweme("a1")], has_more=True, max_cursor=100)],
            100: [
                _normalized_page(
                    {"status_code": 0, "aweme_list": None, "has_more": 1, "max_cursor": 200}
                )
            ],
        }
    )
    downloader = _FakeDownloader(api)
    strategy = PostUserModeStrategy(downloader)

    items = _run_collect(strategy)

    assert [item["aweme_id"] for item in items] == ["a1"]
    assert api.calls == [0, 100, 100, 100]
    assert strategy.incomplete_reason and "不完整" in strategy.incomplete_reason


def test_genuinely_empty_last_page_still_ends_the_walk_cleanly():
    """真 ``[]`` + status_code=0 仍是「翻到底了」，不能被新判据误报成失败。"""
    api = _ScriptedAPI(
        {
            0: [_api_page([_make_aweme("a1")], has_more=True, max_cursor=100)],
            100: [_normalized_page({"status_code": 0, "aweme_list": [], "has_more": 0})],
        }
    )
    downloader = _FakeDownloader(api)
    strategy = PostUserModeStrategy(downloader)

    items = _run_collect(strategy)

    assert [item["aweme_id"] for item in items] == ["a1"]
    assert api.calls == [0, 100], "可信的空页不该重试"
    assert strategy.incomplete_reason is None


def test_retry_budget_expiry_stops_further_attempts(monkeypatch):
    """墙钟预算用尽后不再发起新尝试，但仍要把这一页记成硬截断。"""
    monkeypatch.setattr(post_strategy_module, "_POST_PAGE_RETRY_BACKOFF_SECONDS", (0.0, 0.0))
    monkeypatch.setattr(post_strategy_module, "_POST_PAGE_RETRY_BUDGET_SECONDS", 0.0)
    api = _ScriptedAPI(
        {
            0: [_api_page([_make_aweme("a1")], has_more=True, max_cursor=100)],
            100: [_failed_page()],
        }
    )
    downloader = _FakeDownloader(api)
    strategy = PostUserModeStrategy(downloader)

    items = _run_collect(strategy)

    assert [item["aweme_id"] for item in items] == ["a1"]
    assert api.calls == [0, 100], "预算耗尽后不应再重试"
    assert strategy.incomplete_reason and "不完整" in strategy.incomplete_reason


def test_retry_backoff_index_never_runs_off_the_table():
    """尝试次数放宽到超过退避表长度时取最后一档，别让下标越界。"""
    backoffs = [
        PostUserModeStrategy._retry_backoff_seconds(attempt)
        for attempt in range(1, post_strategy_module._POST_PAGE_MAX_ATTEMPTS + 2)
    ]

    assert backoffs[:2] == [2.0, 5.0]
    assert backoffs[-1] == 5.0


class _BridgeTransportError(Exception):
    """鸭子类型的 page bridge 传输失败(真实类型 ``core.page_bridge.PageBridgeError``)。"""

    def __init__(self, code: str):
        super().__init__(f"page bridge {code}")
        self.page_bridge_code = code


class _RaisingOnceAPI(_ScriptedAPI):
    """指定 cursor 的第一次请求抛 ``error``,之后回放脚本。"""

    def __init__(self, pages_by_cursor, *, raise_at: int, error: Exception):
        super().__init__(pages_by_cursor)
        self._raise_at = raise_at
        self._error: Optional[Exception] = error

    async def get_user_post(self, _sec_uid, max_cursor=0, count=20):
        if max_cursor == self._raise_at and self._error is not None:
            self.calls.append(max_cursor)
            error, self._error = self._error, None
            raise error
        return await super().get_user_post(_sec_uid, max_cursor, count)


def test_bridge_transport_failure_is_retried_like_a_failed_page(monkeypatch):
    """aweme/post 经 page bridge(2026-09-14 Argus 扩面)后,瞬时的 bridge TIMEOUT
    以异常形式冒出来。它必须与 aiohttp 403 的空 raw 同档走整页重试,而不是直接
    打断整个主页走查、让任务 0 计数失败。"""
    monkeypatch.setattr(post_strategy_module, "_POST_PAGE_RETRY_BACKOFF_SECONDS", (0.0, 0.0))
    api = _RaisingOnceAPI(
        {
            0: [_api_page([_make_aweme("a1")], has_more=True, max_cursor=100)],
            100: [_api_page([_make_aweme("a2")], has_more=False, max_cursor=0)],
        },
        raise_at=100,
        error=_BridgeTransportError("TIMEOUT"),
    )
    strategy = PostUserModeStrategy(_FakeDownloader(api))

    items = _run_collect(strategy)

    assert [item["aweme_id"] for item in items] == ["a1", "a2"]
    assert api.calls == [0, 100, 100]
    assert strategy.incomplete_reason is None


def test_login_required_during_post_walk_still_propagates(monkeypatch):
    from core.api_client import LoginRequiredError

    monkeypatch.setattr(post_strategy_module, "_POST_PAGE_RETRY_BACKOFF_SECONDS", (0.0, 0.0))
    api = _RaisingOnceAPI(
        {0: [_api_page([_make_aweme("a1")], has_more=False, max_cursor=0)]},
        raise_at=0,
        error=LoginRequiredError(0, "page bridge: not logged in", "/aweme/v1/web/aweme/post/"),
    )
    strategy = PostUserModeStrategy(_FakeDownloader(api))

    try:
        _run_collect(strategy)
    except LoginRequiredError:
        return
    raise AssertionError("LoginRequiredError must not be swallowed by the page retry")
