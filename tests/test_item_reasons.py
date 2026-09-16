"""条目跳过 / 失败原因：记录点、结算优先级与任务级汇总。

事件流里原先只有「跳过 · 7552196871758843145」，后端明明知道是「下载目录里
已有」还是「视频线路全挂」，却只把 bool 往上传。这里钉住三件事：
1. 每个已知的跳过 / 失败出口都记下人话原因；
2. 结算时内层记下的原因优先于调用点的笼统原因，成功会清掉残留原因；
3. 任务结束后能按原因分组计数，供任务中心显示汇总。
"""

import asyncio
from unittest.mock import AsyncMock

from auth import CookieManager
from config import ConfigLoader
from control import QueueManager, RateLimiter, RetryHandler
from core import downloader_base, item_reasons
from core.api_client import DouyinAPIClient
from core.video_downloader import VideoDownloader
from storage import FileManager

AWEME_ID = "7552196871758843145"


class _Reporter:
    def __init__(self):
        self.items = []

    def update_step(self, step, detail=""):
        pass

    def set_item_total(self, total, detail=""):
        pass

    def advance_item(self, status, detail="", reason=""):
        self.items.append((status, detail, reason))


class _HistoryDatabase:
    async def is_downloaded(self, _aweme_id):
        return True


def _build(tmp_path, *, reporter=None, max_retries=1):
    config = ConfigLoader()
    config.update(path=str(tmp_path))
    retry_handler = RetryHandler(max_retries=max_retries)
    retry_handler.retry_delays = [0]
    return VideoDownloader(
        config,
        DouyinAPIClient({}),
        FileManager(str(tmp_path)),
        CookieManager(str(tmp_path / ".cookies.json")),
        database=None,
        rate_limiter=RateLimiter(max_per_second=100),
        retry_handler=retry_handler,
        queue_manager=QueueManager(max_workers=1),
        progress_reporter=reporter,
    )


def _only_sidecars_off(downloader):
    downloader.config.update(music=False, cover=False, avatar=False, json=False, folderstyle=True)
    downloader.api_client.get_session = AsyncMock(return_value=None)


# ---------------------------------------------------------------------------
# 结算：原因优先级与汇总
# ---------------------------------------------------------------------------


def test_recorded_reason_wins_over_call_site_reason(tmp_path):
    reporter = _Reporter()
    downloader = _build(tmp_path, reporter=reporter)

    downloader._note_item_reason(AWEME_ID, item_reasons.FAIL_VIDEO_DEADLINE)
    downloader._progress_advance_item("failed", AWEME_ID, item_reasons.FAIL_UNEXPECTED)

    assert reporter.items == [("failed", AWEME_ID, item_reasons.FAIL_VIDEO_DEADLINE)]


def test_call_site_reason_used_when_nothing_recorded(tmp_path):
    reporter = _Reporter()
    downloader = _build(tmp_path, reporter=reporter)

    downloader._progress_advance_item("skipped", AWEME_ID, item_reasons.SKIP_LIVE_NOT_STREAMING)

    assert reporter.items == [("skipped", AWEME_ID, item_reasons.SKIP_LIVE_NOT_STREAMING)]


def test_unexplained_failure_and_skip_still_carry_a_reason(tmp_path):
    reporter = _Reporter()
    downloader = _build(tmp_path, reporter=reporter)

    downloader._progress_advance_item("failed", "a")
    downloader._progress_advance_item("skipped", "b")

    assert reporter.items == [
        ("failed", "a", item_reasons.FAIL_UNSPECIFIED),
        ("skipped", "b", item_reasons.SKIP_UNSPECIFIED),
    ]


def test_success_carries_no_reason_and_clears_stale_one(tmp_path):
    reporter = _Reporter()
    downloader = _build(tmp_path, reporter=reporter)

    downloader._note_item_reason(AWEME_ID, item_reasons.SKIP_LOCAL_FILE_EXISTS)
    downloader._progress_advance_item("success", AWEME_ID)
    downloader._progress_advance_item("failed", AWEME_ID, item_reasons.FAIL_UNEXPECTED)

    assert reporter.items == [
        ("success", AWEME_ID, ""),
        ("failed", AWEME_ID, item_reasons.FAIL_UNEXPECTED),
    ]


def test_success_is_emitted_without_reason_argument(tmp_path):
    """成功路径的调用形状不变：只收 (status, detail) 的旧 reporter 照常工作。"""

    class _LegacyReporter:
        def __init__(self):
            self.items = []

        def advance_item(self, status, detail=""):
            self.items.append((status, detail))

    reporter = _LegacyReporter()
    downloader = _build(tmp_path, reporter=reporter)

    downloader._progress_advance_item("success", AWEME_ID)

    assert reporter.items == [("success", AWEME_ID)]


def test_recorded_skip_reason_never_lands_on_failed_row(tmp_path):
    """判定「已有」后继续补评论、却在取详情时失败：失败行不能写「下载目录里已有」。"""
    reporter = _Reporter()
    downloader = _build(tmp_path, reporter=reporter)

    downloader._note_item_reason(AWEME_ID, item_reasons.SKIP_LOCAL_FILE_EXISTS)
    downloader._progress_advance_item("failed", AWEME_ID, item_reasons.FAIL_DETAIL_UNAVAILABLE)
    downloader._note_item_reason("b", item_reasons.FAIL_NO_VIDEO_URL)
    downloader._progress_advance_item("skipped", "b", item_reasons.SKIP_COMMENTS_EXIST)

    assert reporter.items == [
        ("failed", AWEME_ID, item_reasons.FAIL_DETAIL_UNAVAILABLE),
        ("skipped", "b", item_reasons.SKIP_COMMENTS_EXIST),
    ]


def test_pop_item_reason_clears_and_checks_outcome_kind(tmp_path):
    downloader = _build(tmp_path)

    downloader._note_item_reason("a", item_reasons.FAIL_NO_VIDEO_URL)
    assert downloader.pop_item_reason("a", "failed") == item_reasons.FAIL_NO_VIDEO_URL
    assert downloader.pop_item_reason("a", "failed") == ""

    downloader._note_item_reason("b", item_reasons.SKIP_LOCAL_FILE_EXISTS)
    assert downloader.pop_item_reason("b", "failed") == ""
    # 不同类也要清掉，否则会漏到下一次结算。
    assert downloader.pop_item_reason("b", "skipped") == ""


def test_first_recorded_reason_is_kept_unless_replaced(tmp_path):
    downloader = _build(tmp_path)

    assert downloader._note_item_reason(AWEME_ID, item_reasons.FAIL_VIDEO_DEADLINE) is False
    downloader._note_item_reason(AWEME_ID, item_reasons.FAIL_VIDEO_ALL_SOURCES)
    downloader._progress_advance_item("failed", AWEME_ID)
    downloader._note_item_reason("b", item_reasons.SKIP_LOCAL_FILE_EXISTS)
    downloader._note_item_reason("b", item_reasons.SKIP_COMMENTS_EXIST, replace=True)
    downloader._progress_advance_item("skipped", "b")

    assert downloader.item_reason_summary() == {
        "failed": [{"reason": item_reasons.FAIL_VIDEO_DEADLINE, "count": 1}],
        "skipped": [{"reason": item_reasons.SKIP_COMMENTS_EXIST, "count": 1}],
    }


def test_reason_summary_groups_and_sorts_by_count(tmp_path):
    downloader = _build(tmp_path)

    downloader._progress_advance_item("failed", "1", item_reasons.FAIL_NO_VIDEO_URL)
    for aweme_id in ("2", "3"):
        downloader._progress_advance_item("failed", aweme_id, item_reasons.FAIL_VIDEO_ALL_SOURCES)
    for aweme_id in ("4", "5", "6"):
        downloader._progress_advance_item("skipped", aweme_id, item_reasons.SKIP_LOCAL_FILE_EXISTS)
    downloader._progress_advance_item("success", "7")

    assert downloader.item_reason_summary() == {
        "failed": [
            {"reason": item_reasons.FAIL_VIDEO_ALL_SOURCES, "count": 2},
            {"reason": item_reasons.FAIL_NO_VIDEO_URL, "count": 1},
        ],
        "skipped": [{"reason": item_reasons.SKIP_LOCAL_FILE_EXISTS, "count": 3}],
    }


def test_reason_summary_is_recorded_without_reporter(tmp_path):
    """CLI / 库模式不传 reporter 时也要能汇总。"""
    downloader = _build(tmp_path, reporter=None)

    downloader._progress_advance_item("failed", "1", item_reasons.FAIL_NO_VIDEO_URL)

    assert downloader.item_reason_summary() == {
        "failed": [{"reason": item_reasons.FAIL_NO_VIDEO_URL, "count": 1}]
    }


def test_reason_summary_empty_when_nothing_skipped_or_failed(tmp_path):
    downloader = _build(tmp_path)
    downloader._progress_advance_item("success", "1")

    assert downloader.item_reason_summary() == {}


def test_reason_for_exception_distinguishes_disk_errors():
    assert item_reasons.reason_for_exception(OSError(28, "No space left")) == (
        item_reasons.FAIL_WRITE_ERROR
    )
    assert item_reasons.reason_for_exception(PermissionError(13, "denied")) == (
        item_reasons.FAIL_WRITE_ERROR
    )
    assert item_reasons.reason_for_exception(ValueError("boom")) == item_reasons.FAIL_UNEXPECTED
    # TimeoutError / ConnectionError 也是 OSError 子类，不能被说成磁盘问题。
    assert item_reasons.reason_for_exception(TimeoutError()) == item_reasons.FAIL_UNEXPECTED
    assert item_reasons.reason_for_exception(ConnectionResetError(54, "reset")) == (
        item_reasons.FAIL_UNEXPECTED
    )


def test_skip_reason_set_covers_every_skip_constant_and_no_failure():
    """结算按 SKIP_REASONS 分辨跳过 / 失败；漏收一个跳过文案，跳过行就会丢掉原因。"""
    constants = {
        name: value
        for name, value in vars(item_reasons).items()
        if name.isupper() and isinstance(value, str)
    }
    skip_texts = {value for name, value in constants.items() if name.startswith("SKIP_")}
    fail_texts = {value for name, value in constants.items() if name.startswith("FAIL_")}

    assert item_reasons.SKIP_REASONS == skip_texts
    assert not skip_texts & fail_texts


def test_reasons_are_fixed_text_without_placeholders():
    """汇总按原文分组；带 {} 占位的文案会被拆成一堆各计 1 次的组。"""
    texts = [
        value
        for name, value in vars(item_reasons).items()
        if name.isupper() and isinstance(value, str)
    ]
    assert texts, "item_reasons 应导出原因常量"
    for text in texts:
        assert text.strip() == text and text
        assert "{" not in text and "}" not in text


# ---------------------------------------------------------------------------
# _should_download 的跳过原因
# ---------------------------------------------------------------------------


async def test_local_file_skip_records_reason(tmp_path):
    (tmp_path / f"2026-02-18_demo_{AWEME_ID}.mp4").write_bytes(b"1")
    reporter = _Reporter()
    downloader = _build(tmp_path, reporter=reporter)

    assert await downloader._should_download(AWEME_ID) is False
    downloader._progress_advance_item("skipped", AWEME_ID)

    assert reporter.items == [("skipped", AWEME_ID, item_reasons.SKIP_LOCAL_FILE_EXISTS)]


async def test_history_skip_records_reason(tmp_path):
    reporter = _Reporter()
    downloader = _build(tmp_path, reporter=reporter)
    downloader.config.update(redownload_missing_files=False)
    downloader.database = _HistoryDatabase()

    assert await downloader._should_download(AWEME_ID) is False
    downloader._progress_advance_item("skipped", AWEME_ID)

    assert reporter.items == [("skipped", AWEME_ID, item_reasons.SKIP_HISTORY_EXISTS)]


async def test_comments_already_saved_replaces_local_file_reason(tmp_path):
    reporter = _Reporter()
    downloader = _build(tmp_path, reporter=reporter)
    downloader.config.update(comments={"enabled": True}, folderstyle=False)
    aweme = {"aweme_id": AWEME_ID, "desc": "demo", "create_time": 1747353600}
    context = downloader._build_aweme_file_context(aweme, "作者", "post")
    comments_path = context["save_dir"] / f"{context['file_stem']}_comments.json"
    comments_path.write_text("[]", encoding="utf-8")
    downloader._note_item_reason(AWEME_ID, item_reasons.SKIP_LOCAL_FILE_EXISTS)

    saved = await downloader._collect_comments_for_existing_aweme(aweme, "作者", "post")
    downloader._progress_advance_item("skipped", AWEME_ID)

    assert saved is False
    assert reporter.items == [("skipped", AWEME_ID, item_reasons.SKIP_COMMENTS_EXIST)]


async def test_comment_backfill_failure_records_reason(tmp_path, monkeypatch):
    reporter = _Reporter()
    downloader = _build(tmp_path, reporter=reporter)
    downloader.config.update(comments={"enabled": True})
    monkeypatch.setattr(downloader, "_save_comments", AsyncMock(return_value=False))
    downloader._note_item_reason(AWEME_ID, item_reasons.SKIP_LOCAL_FILE_EXISTS)

    saved = await downloader._collect_comments_for_existing_aweme(
        {"aweme_id": AWEME_ID, "desc": "demo"}, "作者", "post"
    )
    downloader._progress_advance_item("skipped", AWEME_ID)

    assert saved is False
    assert reporter.items == [("skipped", AWEME_ID, item_reasons.SKIP_COMMENTS_FAILED)]


# ---------------------------------------------------------------------------
# _download_aweme_assets 的失败原因
# ---------------------------------------------------------------------------


async def _assets_reason(downloader, aweme):
    ok = await downloader._download_aweme_assets(aweme, "作者", "post")
    assert ok is False
    return downloader._item_reasons.get(str(aweme["aweme_id"]))


async def test_no_video_url_reason(tmp_path):
    downloader = _build(tmp_path)
    _only_sidecars_off(downloader)

    reason = await _assets_reason(downloader, {"aweme_id": AWEME_ID, "video": {}})

    assert reason == item_reasons.FAIL_NO_VIDEO_URL


async def test_paid_content_without_video_url_reason(tmp_path):
    downloader = _build(tmp_path)
    _only_sidecars_off(downloader)
    aweme = {
        "aweme_id": AWEME_ID,
        "charge_info": {"is_charge_content": True, "has_paid": False},
        "video": {},
    }

    reason = await _assets_reason(downloader, aweme)

    assert reason == item_reasons.FAIL_PAID_NO_VIDEO_URL


async def test_all_video_sources_failed_reason(tmp_path, monkeypatch):
    downloader = _build(tmp_path)
    _only_sidecars_off(downloader)
    monkeypatch.setattr(downloader, "_download_with_retry", AsyncMock(return_value=False))
    aweme = {
        "aweme_id": AWEME_ID,
        "video": {"play_addr": {"url_list": ["https://v3-web.douyinvod.com/a.mp4"]}},
    }

    reason = await _assets_reason(downloader, aweme)

    assert reason == item_reasons.FAIL_VIDEO_ALL_SOURCES


async def test_item_deadline_reason_is_not_overwritten(tmp_path, monkeypatch):
    downloader = _build(tmp_path, max_retries=3)
    _only_sidecars_off(downloader)
    monkeypatch.setattr(downloader_base, "_VIDEO_ITEM_DEADLINE_S", 0.1)

    async def _hang(*_args, **_kwargs):
        await asyncio.sleep(30)
        return True

    monkeypatch.setattr(downloader.file_manager, "download_file", _hang)
    aweme = {
        "aweme_id": AWEME_ID,
        "video": {"play_addr": {"url_list": ["https://v3-web.douyinvod.com/a.mp4"]}},
    }

    reason = await asyncio.wait_for(_assets_reason(downloader, aweme), timeout=5)

    assert reason == item_reasons.FAIL_VIDEO_DEADLINE


async def test_encrypted_video_reason(tmp_path, monkeypatch):
    downloader = _build(tmp_path)
    _only_sidecars_off(downloader)

    async def _fake_download(_candidates, save_path, _session, **_kwargs):
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_bytes(b"x")
        return True

    monkeypatch.setattr(downloader, "_download_video_with_fallback", _fake_download)
    monkeypatch.setattr(downloader_base, "detect_mp4_encryption", lambda _path: "cenc")
    aweme = {
        "aweme_id": AWEME_ID,
        "video": {"play_addr": {"url_list": ["https://v3-web.douyinvod.com/a.mp4"]}},
    }

    reason = await _assets_reason(downloader, aweme)

    assert reason == item_reasons.FAIL_ENCRYPTED


async def test_gallery_without_assets_reason(tmp_path):
    downloader = _build(tmp_path)
    _only_sidecars_off(downloader)

    reason = await _assets_reason(downloader, {"aweme_id": AWEME_ID, "aweme_type": 68})

    assert reason == item_reasons.FAIL_GALLERY_NO_ASSETS


async def test_gallery_image_failure_reason(tmp_path, monkeypatch):
    downloader = _build(tmp_path)
    _only_sidecars_off(downloader)
    monkeypatch.setattr(downloader, "_download_with_retry", AsyncMock(return_value=False))
    aweme = {
        "aweme_id": AWEME_ID,
        "aweme_type": 68,
        "images": [{"url_list": ["https://example.com/a.jpg"]}],
    }

    reason = await _assets_reason(downloader, aweme)

    assert reason == item_reasons.FAIL_GALLERY_IMAGE


async def test_gallery_live_photo_failure_reason(tmp_path, monkeypatch):
    downloader = _build(tmp_path)
    _only_sidecars_off(downloader)

    async def _fake_download_with_retry(_url, save_path, _session, **_kwargs):
        return not save_path.name.endswith("_live_1.mp4")

    monkeypatch.setattr(downloader, "_download_with_retry", _fake_download_with_retry)
    aweme = {
        "aweme_id": AWEME_ID,
        "image_post_info": {
            "images": [{"video": {"play_addr": {"url_list": ["https://example.com/l.mp4"]}}}]
        },
    }

    reason = await _assets_reason(downloader, aweme)

    assert reason == item_reasons.FAIL_LIVE_PHOTO


async def test_nothing_selected_reason(tmp_path):
    downloader = _build(tmp_path)
    _only_sidecars_off(downloader)
    downloader.config.update(video=False)

    reason = await _assets_reason(downloader, {"aweme_id": AWEME_ID, "video": {}})

    assert reason == item_reasons.FAIL_NOTHING_SELECTED


async def test_selected_sidecar_without_url_reason(tmp_path):
    """勾了音乐、但作品没有音乐地址：不能说成「没勾选任何附件」。"""
    downloader = _build(tmp_path)
    _only_sidecars_off(downloader)
    downloader.config.update(video=False, music=True)

    reason = await _assets_reason(downloader, {"aweme_id": AWEME_ID, "video": {}, "music": {}})

    assert reason == item_reasons.FAIL_SELECTED_ASSETS_MISSING


async def test_selected_sidecars_all_failed_reason(tmp_path, monkeypatch):
    downloader = _build(tmp_path)
    _only_sidecars_off(downloader)
    downloader.config.update(video=False, cover=True)
    monkeypatch.setattr(downloader, "_download_first_available", AsyncMock(return_value=False))
    aweme = {
        "aweme_id": AWEME_ID,
        "video": {"cover": {"url_list": ["https://example.com/c.jpg"]}},
    }

    reason = await _assets_reason(downloader, aweme)

    assert reason == item_reasons.FAIL_OPTIONAL_ASSETS
