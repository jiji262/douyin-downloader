import asyncio
from datetime import datetime

from config import ConfigLoader
from core.user_downloader import UserDownloader
from core.user_modes.post_strategy import PostUserModeStrategy
from core.user_modes.post_time_boundary import PostTimeBoundary


def _item(aweme_id: str, create_time=None, *, is_top=False):
    item = {"aweme_id": aweme_id, "is_top": is_top}
    if create_time is not None:
        item["create_time"] = create_time
    return item


def test_boundary_requires_one_all_old_confirmation_page():
    boundary = PostTimeBoundary(200)
    assert boundary.observe_page([_item("new", 300)]).should_stop is False
    assert boundary.observe_page([_item("in", 220), _item("old", 190)]).should_stop is False
    assert boundary.observe_page([_item("older", 180)]).should_stop is True


def test_old_pinned_item_does_not_start_boundary():
    boundary = PostTimeBoundary(200)
    decision = boundary.observe_page(
        [_item("pinned", 100, is_top=True), _item("new", 300)],
        is_pinned=lambda item: bool(item.get("is_top")),
    )
    assert decision.should_stop is False
    assert boundary.observe_page([_item("in", 220), _item("old", 190)]).should_stop is False
    assert boundary.observe_page([_item("older", 180)]).should_stop is True


def test_missing_time_disables_boundary_once():
    boundary = PostTimeBoundary(200)
    decision = boundary.observe_page([_item("missing")])
    assert decision.degraded_reason == "missing_or_invalid_create_time"
    assert boundary.observe_page([_item("old", 100)]).degraded_reason is None


def test_in_page_or_cross_page_time_increase_disables_boundary():
    in_page = PostTimeBoundary(200)
    assert (
        in_page.observe_page([_item("a", 300), _item("b", 310)]).degraded_reason
        == "time_order_increased"
    )

    cross_page = PostTimeBoundary(200)
    cross_page.observe_page([_item("a", 300), _item("b", 250)])
    assert cross_page.observe_page([_item("c", 260)]).degraded_reason == ("time_order_increased")


def test_confirmation_page_without_regular_items_disables_boundary():
    boundary = PostTimeBoundary(200)
    boundary.observe_page([_item("in", 220), _item("old", 190)])
    decision = boundary.observe_page(
        [_item("pinned", 100, is_top=True)],
        is_pinned=lambda _: True,
    )
    assert decision.degraded_reason == "confirmation_page_without_regular_items"


def test_confirmation_page_reentry_disables_boundary_with_specific_reason():
    boundary = PostTimeBoundary(200)
    boundary.observe_page([_item("in", 220), _item("old", 190)])
    decision = boundary.observe_page([_item("reentry", 210), _item("older", 180)])
    assert decision.degraded_reason == "time_range_reentry"
    assert boundary.observe_page([_item("oldest", 170)]).degraded_reason is None


class _NoopRateLimiter:
    async def acquire(self):
        return None


class _PagedAPI:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    async def get_user_post(self, _sec_uid, max_cursor=0, count=20):
        self.calls.append(max_cursor)
        return self.pages[max_cursor]


class _Reporter:
    def __init__(self):
        self.updates = []

    def update_step(self, step, detail=""):
        self.updates.append((step, detail))


def _page(items, *, next_cursor, has_more=True):
    return {
        "items": items,
        "has_more": has_more,
        "max_cursor": next_cursor,
        "status_code": 0,
    }


def _ts(value):
    return int(datetime.strptime(value, "%Y-%m-%d %H:%M:%S").timestamp())


def _strategy(
    pages,
    *,
    start="2026-08-23",
    end="2026-08-25",
    number=0,
    media_types=None,
):
    config = ConfigLoader()
    config.update(
        start_time=start,
        end_time=end,
        number={"post": number},
        media_types=media_types or ["video", "gallery"],
        download_pinned=False,
        browser_fallback={"enabled": True},
    )
    downloader = object.__new__(UserDownloader)
    downloader.config = config
    downloader.api_client = _PagedAPI(pages)
    downloader.rate_limiter = _NoopRateLimiter()
    downloader.progress_reporter = _Reporter()

    async def unexpected_browser(*_args, **_kwargs):
        raise AssertionError("normal time boundary must not trigger browser recovery")

    downloader._recover_user_post_with_browser = unexpected_browser
    return PostUserModeStrategy(downloader), downloader


def test_post_stops_after_old_confirmation_page_without_browser_recovery():
    pages = {
        0: _page(
            [
                _item("too-new", _ts("2026-08-26 12:00:00")),
                _item("in-1", _ts("2026-08-25 12:00:00")),
            ],
            next_cursor=1,
        ),
        1: _page(
            [
                _item("in-2", _ts("2026-08-23 12:00:00")),
                _item("old-1", _ts("2026-08-22 12:00:00")),
            ],
            next_cursor=2,
        ),
        2: _page([_item("old-2", _ts("2026-08-21 12:00:00"))], next_cursor=3),
        3: _page(
            [_item("never", _ts("2026-08-20 12:00:00"))],
            next_cursor=3,
            has_more=False,
        ),
    }
    strategy, downloader = _strategy(pages)
    items = asyncio.run(strategy.collect_items("sec", {"aweme_count": 3000}))
    assert [item["aweme_id"] for item in strategy.apply_filters(items)] == ["in-1", "in-2"]
    assert downloader.api_client.calls == [0, 1, 2]
    assert any("提前结束翻页" in detail for _, detail in downloader.progress_reporter.updates)


def test_boundary_on_last_page_ends_naturally_without_browser_recovery():
    pages = {
        0: _page(
            [
                _item("in", _ts("2026-08-23 12:00:00")),
                _item("old", _ts("2026-08-22 12:00:00")),
            ],
            next_cursor=0,
            has_more=False,
        )
    }
    strategy, downloader = _strategy(pages)
    items = asyncio.run(strategy.collect_items("sec", {"aweme_count": 2}))
    assert [item["aweme_id"] for item in strategy.apply_filters(items)] == ["in"]
    assert downloader.api_client.calls == [0]


def test_full_sized_last_boundary_page_ends_without_browser_recovery():
    in_range = [_item(f"in-{index}", _ts("2026-08-23 12:00:00")) for index in range(19)]
    pages = {
        0: _page(
            [*in_range, _item("old", _ts("2026-08-22 12:00:00"))],
            next_cursor=0,
            has_more=False,
        )
    }
    strategy, downloader = _strategy(pages)
    items = asyncio.run(strategy.collect_items("sec", {"aweme_count": 3000}))
    assert len(strategy.apply_filters(items)) == 19
    assert downloader.api_client.calls == [0]


def test_missing_confirmation_time_degrades_and_scans_to_natural_end():
    pages = {
        0: _page(
            [
                _item("in", _ts("2026-08-23 12:00:00")),
                _item("old", _ts("2026-08-22 12:00:00")),
            ],
            next_cursor=1,
        ),
        1: _page([_item("missing")], next_cursor=2),
        2: _page(
            [_item("older", _ts("2026-08-21 12:00:00"))],
            next_cursor=2,
            has_more=False,
        ),
    }
    strategy, downloader = _strategy(pages)
    asyncio.run(strategy.collect_items("sec", {"aweme_count": 4}))
    assert downloader.api_client.calls == [0, 1, 2]


def test_number_limit_counts_only_time_and_media_candidates():
    gallery = {"image_post_info": {"images": [{}]}}
    pages = {
        0: _page(
            [
                {**_item("too-new", _ts("2026-08-26 12:00:00")), **gallery},
                {**_item("video", _ts("2026-08-25 12:00:00")), "video": {}},
            ],
            next_cursor=1,
        ),
        1: _page(
            [{**_item("gallery-1", _ts("2026-08-25 11:00:00")), **gallery}],
            next_cursor=2,
        ),
        2: _page(
            [{**_item("gallery-2", _ts("2026-08-24 11:00:00")), **gallery}],
            next_cursor=3,
        ),
        3: _page(
            [{**_item("never", _ts("2026-08-24 10:00:00")), **gallery}],
            next_cursor=3,
            has_more=False,
        ),
    }
    strategy, downloader = _strategy(pages, number=2, media_types=["gallery"])
    items = asyncio.run(strategy.collect_items("sec", {"aweme_count": 6}))
    assert [item["aweme_id"] for item in strategy.apply_filters(items)] == [
        "gallery-1",
        "gallery-2",
    ]
    assert downloader.api_client.calls == [0, 1, 2]


def test_only_end_time_keeps_full_pagination():
    pages = {
        0: _page([_item("too-new", _ts("2026-08-26 12:00:00"))], next_cursor=1),
        1: _page(
            [_item("in", _ts("2026-08-25 12:00:00"))],
            next_cursor=1,
            has_more=False,
        ),
    }
    strategy, downloader = _strategy(pages, start="", end="2026-08-25")
    items = asyncio.run(strategy.collect_items("sec", {"aweme_count": 2}))
    assert [item["aweme_id"] for item in strategy.apply_filters(items)] == ["in"]
    assert downloader.api_client.calls == [0, 1]
