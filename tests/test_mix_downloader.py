import errno

import pytest

from auth import CookieManager
from config import ConfigLoader
from control import QueueManager, RateLimiter, RetryHandler
from core import item_reasons
from core.mix_downloader import MixDownloader, derive_mix_collection_dir
from storage import FileManager


def test_derive_mix_collection_dir():
    # mix_name preferred; title is the secondary source; both get stripped.
    assert derive_mix_collection_dir({"mix_name": "我的合集"}, "123") == "我的合集"
    assert derive_mix_collection_dir({"title": "标题合集"}, "123") == "标题合集"
    assert derive_mix_collection_dir({"mix_name": "  spaced  "}, "123") == "spaced"
    # Empty / missing / non-dict → fall back to the mix_id (as str).
    assert derive_mix_collection_dir({"mix_name": ""}, 456) == "456"
    assert derive_mix_collection_dir({}, "123") == "123"
    assert derive_mix_collection_dir(None, "123") == "123"


class _FakeAPIClient:
    async def get_mix_aweme(self, _mix_id: str, cursor: int = 0, count: int = 20):
        if cursor > 0:
            return {"items": [], "has_more": False, "max_cursor": cursor, "status_code": 0}
        return {
            "items": [
                {
                    "aweme_id": "7600224486650121888",
                    "desc": "mix-item",
                    "author": {"nickname": "mix-author"},
                    "video": {"play_addr": {"url_list": ["https://example.com/video.mp4"]}},
                }
            ],
            "has_more": False,
            "max_cursor": 0,
            "status_code": 0,
        }

    async def get_mix_detail(self, _mix_id: str):
        return {"author": {"nickname": "mix-author"}}


@pytest.mark.asyncio
async def test_mix_downloader_downloads_mix_items(tmp_path, monkeypatch):
    config = ConfigLoader()
    config.update(path=str(tmp_path), number={"mix": 0})
    file_manager = FileManager(str(tmp_path))
    downloader = MixDownloader(
        config=config,
        api_client=_FakeAPIClient(),
        file_manager=file_manager,
        cookie_manager=CookieManager(str(tmp_path / ".cookies.json")),
        database=None,
        rate_limiter=RateLimiter(max_per_second=10),
        retry_handler=RetryHandler(max_retries=1),
        queue_manager=QueueManager(max_workers=1),
    )

    async def _always_true(*_args, **_kwargs):
        return True

    monkeypatch.setattr(downloader, "_should_download", _always_true)
    monkeypatch.setattr(downloader, "_download_aweme_assets", _always_true)

    result = await downloader.download({"mix_id": "123"})

    assert result.total == 1
    assert result.success == 1
    assert result.failed == 0


class _NamedMixAPIClient(_FakeAPIClient):
    """Mix detail carries a real ``mix_name`` so the collection folder can be
    derived from it."""

    def __init__(self, detail):
        self._detail = detail

    async def get_mix_detail(self, _mix_id: str):
        return self._detail


def _make_mix_downloader(tmp_path, api_client):
    config = ConfigLoader()
    config.update(path=str(tmp_path), number={"mix": 0})
    return MixDownloader(
        config=config,
        api_client=api_client,
        file_manager=FileManager(str(tmp_path)),
        cookie_manager=CookieManager(str(tmp_path / ".cookies.json")),
        database=None,
        rate_limiter=RateLimiter(max_per_second=10),
        retry_handler=RetryHandler(max_retries=1),
        queue_manager=QueueManager(max_workers=1),
    )


@pytest.mark.asyncio
async def test_mix_downloader_passes_mix_name_as_collection_dir(tmp_path, monkeypatch):
    """Each 合集 lands in its own folder: MixDownloader must derive the mix
    name and thread it to ``_download_aweme_assets`` as ``collection_dir``."""
    api = _NamedMixAPIClient({"author": {"nickname": "mix-author"}, "mix_name": "我的合集"})
    downloader = _make_mix_downloader(tmp_path, api)

    captured = {}

    async def _capture(_item, _author, *, mode=None, collection_dir=None, **_kw):
        captured["mode"] = mode
        captured["collection_dir"] = collection_dir
        return True

    async def _always_true(*_a, **_k):
        return True

    monkeypatch.setattr(downloader, "_should_download", _always_true)
    monkeypatch.setattr(downloader, "_download_aweme_assets", _capture)

    await downloader.download({"mix_id": "123"})

    assert captured["mode"] == "mix"
    assert captured["collection_dir"] == "我的合集"


@pytest.mark.asyncio
async def test_mix_downloader_collection_dir_falls_back_to_mix_id(tmp_path, monkeypatch):
    """When the mix detail has no name/title, the collection folder falls back
    to the mix_id so downloads never dump into a bare ``mix`` dir."""
    api = _NamedMixAPIClient({"author": {"nickname": "mix-author"}})  # no name/title
    downloader = _make_mix_downloader(tmp_path, api)

    captured = {}

    async def _capture(_item, _author, *, mode=None, collection_dir=None, **_kw):
        captured["collection_dir"] = collection_dir
        return True

    async def _always_true(*_a, **_k):
        return True

    monkeypatch.setattr(downloader, "_should_download", _always_true)
    monkeypatch.setattr(downloader, "_download_aweme_assets", _capture)

    await downloader.download({"mix_id": "123"})

    assert captured["collection_dir"] == "123"


@pytest.mark.asyncio
async def test_mix_downloader_does_not_apply_redundant_limit_count(tmp_path, monkeypatch):
    config = ConfigLoader()
    config.update(path=str(tmp_path), number={"mix": 0})
    file_manager = FileManager(str(tmp_path))
    downloader = MixDownloader(
        config=config,
        api_client=_FakeAPIClient(),
        file_manager=file_manager,
        cookie_manager=CookieManager(str(tmp_path / ".cookies.json")),
        database=None,
        rate_limiter=RateLimiter(max_per_second=10),
        retry_handler=RetryHandler(max_retries=1),
        queue_manager=QueueManager(max_workers=1),
    )

    async def _always_true(*_args, **_kwargs):
        return True

    call_count = {"limit": 0}

    def _track_limit(items, _mode):
        call_count["limit"] += 1
        return items

    monkeypatch.setattr(downloader, "_should_download", _always_true)
    monkeypatch.setattr(downloader, "_download_aweme_assets", _always_true)
    monkeypatch.setattr(downloader, "_limit_count", _track_limit)

    result = await downloader.download({"mix_id": "123"})

    assert result.total == 1
    assert call_count["limit"] == 0


def _page(items, *, has_more=False, max_cursor=0, raw=None, **extra):
    """Shape of ``DouyinAPIClient._normalize_paged_response``: a real client
    always carries ``raw``; an empty ``raw`` means the request itself failed
    (HTTP 403 / retries exhausted / bridge non-200), not "no more pages"."""
    page = {
        "items": items,
        "has_more": has_more,
        "max_cursor": max_cursor,
        "status_code": 0,
        "raw": {"aweme_list": items, "status_code": 0} if raw is None else raw,
    }
    page.update(extra)
    return page


_ITEM = {
    "aweme_id": "7600224486650121888",
    "desc": "mix-item",
    "author": {"nickname": "mix-author"},
    "video": {"play_addr": {"url_list": ["https://example.com/video.mp4"]}},
}


class _ScriptedMixAPIClient:
    """Serve pre-scripted ``get_mix_aweme`` pages keyed by cursor."""

    def __init__(self, pages):
        self._pages = pages
        self.cursors = []

    async def get_mix_aweme(self, _mix_id: str, cursor: int = 0, count: int = 20):
        self.cursors.append(cursor)
        return self._pages[cursor]

    async def get_mix_detail(self, _mix_id: str):
        return {"author": {"nickname": "mix-author"}, "mix_name": "合集"}


def _stub_downloads(downloader, monkeypatch):
    async def _always_true(*_a, **_k):
        return True

    monkeypatch.setattr(downloader, "_should_download", _always_true)
    monkeypatch.setattr(downloader, "_download_aweme_assets", _always_true)


@pytest.mark.parametrize(
    "first_page",
    [
        pytest.param(_page([], raw={}), id="request-failed"),
        pytest.param(
            _page([], status_code=2154, raw={"status_code": 2154, "aweme_list": []}),
            id="server-error",
        ),
    ],
)
async def test_mix_downloader_fails_loudly_when_first_page_request_fails(
    tmp_path, monkeypatch, first_page
):
    """``mix/aweme/`` 被 Argus 403 时 api_client 回空 ``raw``。以前这里被当成
    「合集是空的」→ 任务 success / 0 项,用户看不到任何原因(2026-09-10 日志)。
    第一页就失败 = 没有任何成果可保,必须按失败结案并带上原因。"""
    from core.user_modes.base_strategy import PageRequestFailedError

    api = _ScriptedMixAPIClient({0: first_page})
    downloader = _make_mix_downloader(tmp_path, api)
    _stub_downloads(downloader, monkeypatch)

    with pytest.raises(PageRequestFailedError, match="合集.*第 1 页"):
        await downloader.download({"mix_id": "123"})


async def test_mix_downloader_keeps_collected_items_when_a_later_page_fails(tmp_path, monkeypatch):
    """第 2 页失败时保住第 1 页的成果(与 UserDownloader 语义一致):照常下载
    已拿到的条目,并通过 ``incomplete_reason`` 告诉用户没取全。"""
    api = _ScriptedMixAPIClient(
        {0: _page([_ITEM], has_more=True, max_cursor=1), 1: _page([], raw={})}
    )
    downloader = _make_mix_downloader(tmp_path, api)
    _stub_downloads(downloader, monkeypatch)

    result = await downloader.download({"mix_id": "123"})

    assert api.cursors == [0, 1]
    assert result.total == 1
    assert result.success == 1
    assert result.incomplete_reason is not None
    assert "第 2 页" in result.incomplete_reason


async def test_mix_downloader_treats_null_list_as_soft_stop(tmp_path, monkeypatch):
    """``"aweme_list": null`` 是 untrusted 而不是失败(docs/spec/gotchas.md):
    不抛、不当成翻到底,只记原因。"""
    api = _ScriptedMixAPIClient(
        {0: _page([], items_missing=True, raw={"aweme_list": None, "status_code": 0})}
    )
    downloader = _make_mix_downloader(tmp_path, api)
    _stub_downloads(downloader, monkeypatch)

    result = await downloader.download({"mix_id": "123"})

    assert result.total == 0
    assert result.incomplete_reason is not None
    assert "接口未返回列表" in result.incomplete_reason


async def test_mix_downloader_genuine_empty_collection_is_a_clean_success(tmp_path, monkeypatch):
    """真 ``[]`` + status_code 0 才是「这个合集确实没有作品」。"""
    api = _ScriptedMixAPIClient({0: _page([])})
    downloader = _make_mix_downloader(tmp_path, api)
    _stub_downloads(downloader, monkeypatch)

    result = await downloader.download({"mix_id": "123"})

    assert result.total == 0
    assert result.incomplete_reason is None


# ---------------------------------------------------------------------------
# Through the REAL DouyinAPIClient normalizer + a duck-typed page bridge.
# (No ``import core.page_bridge`` here: this file syncs to the CLI repo, which
# has no bridge module — the client only requires ``await fetch(...)``.)
# ---------------------------------------------------------------------------


class _BridgeAnswer:
    def __init__(self, http_status, body, text=""):
        self.http_status = http_status
        self.body = body
        self.text = text


class _ScriptedBridge:
    def __init__(self, answers):
        self._answers = list(answers)
        self.calls = []

    async def fetch(self, path, params, *, method="GET", data=None):
        self.calls.append({"path": path, "params": dict(params), "method": method})
        return self._answers.pop(0)


async def _real_client_downloader(tmp_path, monkeypatch, bridge):
    from core.api_client import DouyinAPIClient

    client = DouyinAPIClient({"msToken": "t"}, page_bridge=bridge)

    async def _detail(_mix_id):
        return {"author": {"nickname": "mix-author"}, "mix_name": "合集"}

    monkeypatch.setattr(client, "get_mix_detail", _detail)
    downloader = _make_mix_downloader(tmp_path, client)
    _stub_downloads(downloader, monkeypatch)
    return client, downloader


async def test_mix_downloader_argus_403_via_bridge_fails_loudly(tmp_path, monkeypatch):
    """用户日志里的真实形态：``mix/aweme/`` 经 bridge 回 403(Argus 拒绝，不重试)
    → ``_request_json_gated`` 回空 payload → 归一化成空 ``raw`` → 必须按失败结案，
    而不是「全部成功 0 项」;文案要说「被拒绝」,不能叫用户稍后重试 / 重新登录。"""
    from core.user_modes.base_strategy import PageRequestFailedError

    bridge = _ScriptedBridge(
        [_BridgeAnswer(403, None, "Blocked by ArgusSecurityPlugin Signature Not Found")]
    )
    client, downloader = await _real_client_downloader(tmp_path, monkeypatch, bridge)
    try:
        with pytest.raises(PageRequestFailedError, match="合集 第 1 页被抖音拒绝") as info:
            await downloader.download({"mix_id": "123"})
        assert "可能被限流" not in str(info.value)
        assert [c["path"] for c in bridge.calls] == ["/aweme/v1/web/mix/aweme/"]
    finally:
        await client.close()


async def test_mix_downloader_null_list_via_real_normalizer_is_soft_stop(tmp_path, monkeypatch):
    """``{"aweme_list": null}`` 走真实归一化层必须带出 ``items_missing``，
    否则软停分支在生产路径上是死代码（只在手工构造的页上生效）。"""
    bridge = _ScriptedBridge(
        [_BridgeAnswer(200, {"status_code": 0, "aweme_list": None, "has_more": 0})]
    )
    client, downloader = await _real_client_downloader(tmp_path, monkeypatch, bridge)
    try:
        result = await downloader.download({"mix_id": "123"})
        assert result.total == 0
        assert result.incomplete_reason is not None
        assert "接口未返回列表" in result.incomplete_reason
    finally:
        await client.close()


async def test_mix_downloader_number_mix_limit_stops_pagination(tmp_path, monkeypatch):
    """``number.mix`` 达到后截断并停止翻页，不再请求下一页。"""
    second = dict(_ITEM, aweme_id="7600224486650121889")
    api = _ScriptedMixAPIClient(
        {0: _page([_ITEM, second], has_more=True, max_cursor=1), 1: _page([], raw={})}
    )
    downloader = _make_mix_downloader(tmp_path, api)
    downloader.config.update(number={"mix": 1})
    _stub_downloads(downloader, monkeypatch)

    result = await downloader.download({"mix_id": "123"})

    assert api.cursors == [0]
    assert result.total == 1
    assert result.incomplete_reason is None


class _BridgeTransportError(Exception):
    def __init__(self, code):
        super().__init__(f"page bridge {code}")
        self.page_bridge_code = code


class _RaisingMixAPIClient(_ScriptedMixAPIClient):
    def __init__(self, pages, *, raise_at, error):
        super().__init__(pages)
        self._raise_at = raise_at
        self._error = error

    async def get_mix_aweme(self, _mix_id: str, cursor: int = 0, count: int = 20):
        if cursor == self._raise_at:
            self.cursors.append(cursor)
            raise self._error
        return await super().get_mix_aweme(_mix_id, cursor, count)


async def test_mix_downloader_keeps_collected_items_when_bridge_fails_on_a_later_page(
    tmp_path, monkeypatch
):
    """bridge 传输失败(TIMEOUT 等)与空 raw 同档:第 2 页失败保住第 1 页的成果。"""
    api = _RaisingMixAPIClient(
        {0: _page([_ITEM], has_more=True, max_cursor=1)},
        raise_at=1,
        error=_BridgeTransportError("TIMEOUT"),
    )
    downloader = _make_mix_downloader(tmp_path, api)
    _stub_downloads(downloader, monkeypatch)

    result = await downloader.download({"mix_id": "123"})

    assert api.cursors == [0, 1]
    assert result.success == 1
    assert result.incomplete_reason is not None
    assert "第 2 页" in result.incomplete_reason


async def test_mix_downloader_bridge_failure_on_first_page_fails_loudly(tmp_path, monkeypatch):
    from core.user_modes.base_strategy import PageRequestFailedError

    api = _RaisingMixAPIClient({}, raise_at=0, error=_BridgeTransportError("PAGE_LOAD_FAILED"))
    downloader = _make_mix_downloader(tmp_path, api)
    _stub_downloads(downloader, monkeypatch)

    with pytest.raises(PageRequestFailedError, match="合集.*第 1 页"):
        await downloader.download({"mix_id": "123"})


async def test_mix_downloader_login_required_still_propagates(tmp_path, monkeypatch):
    from core.api_client import LoginRequiredError

    api = _RaisingMixAPIClient(
        {}, raise_at=0, error=LoginRequiredError(0, "page bridge: not logged in", "/mix/aweme/")
    )
    downloader = _make_mix_downloader(tmp_path, api)
    _stub_downloads(downloader, monkeypatch)

    with pytest.raises(LoginRequiredError):
        await downloader.download({"mix_id": "123"})


class _RecordingReporter:
    def __init__(self):
        self.outcomes = []

    def update_step(self, step, detail=""):
        return None

    def set_item_total(self, total, detail=""):
        return None

    def advance_item(self, status, detail="", reason=""):
        self.outcomes.append((status, detail, reason))


def _make_reporting_mix_downloader(tmp_path, items):
    downloader = _make_mix_downloader(tmp_path, _ScriptedMixAPIClient({0: _page(items)}))
    downloader.progress_reporter = _RecordingReporter()
    return downloader


async def test_mix_downloader_missing_id_reports_reason(tmp_path, monkeypatch):
    downloader = _make_reporting_mix_downloader(tmp_path, [])

    async def _items_without_id(_mix_id):
        return [{"desc": "no-id"}], None

    monkeypatch.setattr(downloader, "_collect_mix_aweme_list", _items_without_id)

    result = await downloader.download({"mix_id": "123"})

    assert result.failed == 1
    assert downloader.progress_reporter.outcomes == [
        ("failed", "missing_aweme_id", item_reasons.FAIL_MISSING_ID)
    ]


async def test_mix_downloader_failed_asset_download_reports_recorded_reason(tmp_path, monkeypatch):
    downloader = _make_reporting_mix_downloader(tmp_path, [_ITEM])

    async def _always_true(*_a, **_k):
        return True

    async def _fail_with_reason(item, *_a, **_k):
        return downloader._note_item_reason(item["aweme_id"], item_reasons.FAIL_VIDEO_DEADLINE)

    monkeypatch.setattr(downloader, "_should_download", _always_true)
    monkeypatch.setattr(downloader, "_download_aweme_assets", _fail_with_reason)

    result = await downloader.download({"mix_id": "123"})

    assert result.failed == 1
    assert downloader.progress_reporter.outcomes == [
        ("failed", _ITEM["aweme_id"], item_reasons.FAIL_VIDEO_DEADLINE)
    ]


async def test_mix_downloader_exception_emits_item_complete_with_reason(tmp_path, monkeypatch):
    """gather 回异常时以前只计失败、不发 item-complete，事件流里这一条凭空消失。"""
    downloader = _make_reporting_mix_downloader(tmp_path, [_ITEM])

    async def _always_true(*_a, **_k):
        return True

    async def _raise(*_a, **_k):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(downloader, "_should_download", _always_true)
    monkeypatch.setattr(downloader, "_download_aweme_assets", _raise)

    result = await downloader.download({"mix_id": "123"})

    assert result.failed == 1
    assert downloader.progress_reporter.outcomes == [
        ("failed", _ITEM["aweme_id"], item_reasons.FAIL_WRITE_ERROR)
    ]
    assert downloader.item_reason_summary() == {
        "failed": [{"reason": item_reasons.FAIL_WRITE_ERROR, "count": 1}]
    }


async def test_mix_downloader_rejection_on_a_later_page_names_the_rejection(tmp_path, monkeypatch):
    """第 2 页被拒绝:保住第 1 页的成果,原因写「被抖音拒绝」而不是「请稍后重试」。"""
    bridge = _ScriptedBridge(
        [
            _BridgeAnswer(
                200,
                {"status_code": 0, "aweme_list": [_ITEM], "has_more": 1, "cursor": 1},
            ),
            _BridgeAnswer(403, None, "Blocked by ArgusSecurityPlugin Sign Invalid"),
        ]
    )
    client, downloader = await _real_client_downloader(tmp_path, monkeypatch, bridge)
    try:
        result = await downloader.download({"mix_id": "123"})
        assert result.success == 1
        reason = result.incomplete_reason or ""
        assert "第 2 页被抖音拒绝" in reason and "不完整" in reason
        assert "请稍后重试" not in reason
    finally:
        await client.close()


async def test_mix_downloader_bridge_failure_message_names_the_bridge_code(tmp_path, monkeypatch):
    from core.user_modes.base_strategy import PageRequestFailedError

    api = _RaisingMixAPIClient({}, raise_at=0, error=_BridgeTransportError("PAGE_LOAD_FAILED"))
    downloader = _make_mix_downloader(tmp_path, api)
    _stub_downloads(downloader, monkeypatch)

    with pytest.raises(PageRequestFailedError) as info:
        await downloader.download({"mix_id": "123"})

    message = str(info.value)
    assert "PAGE_LOAD_FAILED" in message
    assert "可能被限流" not in message and "重新登录" not in message
