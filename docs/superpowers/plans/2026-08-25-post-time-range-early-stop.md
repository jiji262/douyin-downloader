# Post Time-Range Early Stop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Codex must not use `superpowers:subagent-driven-development` for write operations in this repository.

Before editing, invoke `superpowers:using-git-worktrees`; while coding, apply the four `andrej-karpathy-skills:karpathy-guidelines` rules (think first, keep it simple, make surgical changes, and verify against the goal).

**Goal:** Stop normal Douyin author-homepage `post` pagination shortly after it safely passes `start_time`, while preserving full-result correctness and disk-based incremental behavior.

**Architecture:** Keep date parsing and final filtering in `BaseDownloader`, and add a small pure state machine for page-order validation and boundary confirmation. `PostUserModeStrategy` remains the only mode that consumes the state machine; all other modes keep their current traversal behavior. Implement identical shared Python files in paired Desktop/CLI worktrees and extend the Desktop-owned sync manifest for the new files.

**Tech Stack:** Python 3.8+, asyncio, pytest/pytest-asyncio, ruff, existing strategy and progress-reporter patterns.

## Global Constraints

- Implement only author-homepage `post`; do not change `like`, `mix`, `music`, `collect`, or `collectmix`.
- Do not add UI controls, YAML fields, HTTP fields, dependencies, schema, or persistence.
- Interpret dates in the runtime local timezone as `[start day 00:00:00, day after end 00:00:00)`.
- Enable time early-stop only when a valid `start_time` exists; `end_time` alone never stops pagination.
- Ignore pinned works for ordering evidence even when pinned downloading is enabled.
- Missing/invalid timestamps, non-monotonic order, time-range re-entry, or an empty confirmation page disable the optimization and continue full traversal.
- Cursor stalls, timeouts, and suspicious empty pages retain the current browser-recovery behavior; deliberate time stop is never pagination restriction.
- SQLite history must not decide traversal or skipping; within-range incremental decisions remain disk-based.
- Keep functions at most 50 lines, new files at most 300 lines, nesting at most 3, and Python 3.8 syntax.
- Do not modify or append `docs/log.md`; do not push or merge without a separate explicit request.

---

### Task 1: Normalize inclusive date-range filtering

**Files:**
- Modify in both repos: `core/downloader_base.py:5,318-339`
- Create in both repos: `tests/test_time_range_filter.py`

**Interfaces:**
- Produces: `BaseDownloader._time_range_bounds() -> Tuple[Optional[int], Optional[int]]`
- Return contract: `(start_ts_inclusive, end_ts_exclusive)` in the runtime local timezone.
- Consumed by: `_filter_by_time()` in this task and `PostUserModeStrategy` in Task 3.

- [ ] **Step 1: Prepare the paired Desktop worktree**

Use `superpowers:using-git-worktrees` for this step, then run:

```bash
git -C /Users/crimson/codes/douyin/douyin-downloader-desktop worktree list --porcelain
git -C /Users/crimson/codes/douyin/douyin-downloader-desktop branch --list codex/post-time-range-early-stop
git -C /Users/crimson/codes/douyin/douyin-downloader-desktop worktree add /Users/crimson/codes/douyin/.worktrees/douyin-downloader-desktop-post-time-range-early-stop -b codex/post-time-range-early-stop main
```

Expected: the new Desktop worktree is clean and does not include unrelated uncommitted Telegram changes from the main checkout. If the branch/worktree already exists, reuse the listed path instead of running the add command again.

- [ ] **Step 2: Write the failing date-boundary tests in both repos**

Create the same `tests/test_time_range_filter.py` in the Desktop and CLI worktrees:

```python
from datetime import datetime
from typing import Any, Dict

from core.downloader_base import BaseDownloader, DownloadResult


class _Config:
    def __init__(self, data: Dict[str, Any]):
        self.data = data

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


class _Harness(BaseDownloader):
    async def download(self, _parsed_url: Dict[str, Any]) -> DownloadResult:
        return DownloadResult()


def _downloader(**config: Any) -> _Harness:
    downloader = object.__new__(_Harness)
    downloader.config = _Config(config)
    return downloader


def _ts(value: str) -> int:
    return int(datetime.strptime(value, "%Y-%m-%d %H:%M:%S").timestamp())


def test_time_range_bounds_use_exclusive_day_after_end():
    downloader = _downloader(start_time="2026-08-19", end_time="2026-08-20")
    assert downloader._time_range_bounds() == (
        _ts("2026-08-19 00:00:00"),
        _ts("2026-08-21 00:00:00"),
    )


def test_filter_by_time_includes_full_end_day():
    downloader = _downloader(start_time="2026-08-19", end_time="2026-08-20")
    items = [
        {"aweme_id": "before", "create_time": _ts("2026-08-18 23:59:59")},
        {"aweme_id": "start", "create_time": _ts("2026-08-19 00:00:00")},
        {"aweme_id": "end", "create_time": _ts("2026-08-20 23:59:59")},
        {"aweme_id": "after", "create_time": _ts("2026-08-21 00:00:00")},
    ]
    assert [item["aweme_id"] for item in downloader._filter_by_time(items)] == ["start", "end"]


def test_filter_by_time_with_only_end_time_keeps_end_day():
    downloader = _downloader(start_time="", end_time="2026-08-20")
    items = [
        {"aweme_id": "end", "create_time": _ts("2026-08-20 23:59:59")},
        {"aweme_id": "after", "create_time": _ts("2026-08-21 00:00:00")},
    ]
    assert [item["aweme_id"] for item in downloader._filter_by_time(items)] == ["end"]
```

- [ ] **Step 3: Run the tests and confirm the current behavior fails**

Run in each worktree:

```bash
python -m pytest -q tests/test_time_range_filter.py
```

Expected: FAIL because `_time_range_bounds` does not exist and the current end bound is midnight at the start of the selected end day.

- [ ] **Step 4: Implement the shared time-bound interface**

In both copies of `core/downloader_base.py`, import `timedelta` and replace the inline parsing in `_filter_by_time()` with:

```python
def _time_range_bounds(self) -> Tuple[Optional[int], Optional[int]]:
    start_time = self.config.get("start_time")
    end_time = self.config.get("end_time")
    start_ts = (
        int(datetime.strptime(start_time, "%Y-%m-%d").timestamp()) if start_time else None
    )
    end_ts = None
    if end_time:
        end_date = datetime.strptime(end_time, "%Y-%m-%d") + timedelta(days=1)
        end_ts = int(end_date.timestamp())
    return start_ts, end_ts

def _filter_by_time(self, aweme_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    start_ts, end_ts = self._time_range_bounds()
    if start_ts is None and end_ts is None:
        return aweme_list

    filtered: List[Dict[str, Any]] = []
    for aweme in aweme_list:
        create_time = aweme.get("create_time", 0)
        if start_ts is not None and create_time < start_ts:
            continue
        if end_ts is not None and create_time >= end_ts:
            continue
        filtered.append(aweme)
    return filtered
```

- [ ] **Step 5: Run targeted tests and commit both repos**

Run in each worktree:

```bash
python -m pytest -q tests/test_time_range_filter.py tests/test_video_downloader.py
ruff check core/downloader_base.py tests/test_time_range_filter.py
```

Expected: PASS. Then commit separately in each repository:

```bash
git add core/downloader_base.py tests/test_time_range_filter.py
git commit -m "fix(download): 结束日期包含完整当天"
```

### Task 2: Build the conservative page-boundary state machine

**Files:**
- Create in both repos: `core/user_modes/post_time_boundary.py`
- Create/extend in both repos: `tests/test_post_time_pagination.py`

**Interfaces:**
- Produces: `TimeBoundaryDecision(should_stop: bool, degraded_reason: Optional[str])`
- Produces: `PostTimeBoundary(start_ts: Optional[int])`
- Produces: `PostTimeBoundary.observe_page(items, *, is_pinned=None) -> TimeBoundaryDecision`
- Consumed by: `PostUserModeStrategy._collect_api_items()` in Task 3.

- [ ] **Step 1: Write focused failing state-machine tests**

Add these tests to both copies of `tests/test_post_time_pagination.py`:

```python
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
    assert boundary.observe_page([_item("in", 220)]).should_stop is False


def test_missing_time_disables_boundary_once():
    boundary = PostTimeBoundary(200)
    decision = boundary.observe_page([_item("missing")])
    assert decision.degraded_reason == "missing_or_invalid_create_time"
    assert boundary.observe_page([_item("old", 100)]).degraded_reason is None


def test_in_page_or_cross_page_time_increase_disables_boundary():
    in_page = PostTimeBoundary(200)
    assert in_page.observe_page([_item("a", 300), _item("b", 310)]).degraded_reason == "time_order_increased"

    cross_page = PostTimeBoundary(200)
    cross_page.observe_page([_item("a", 300), _item("b", 250)])
    assert cross_page.observe_page([_item("c", 260)]).degraded_reason == "time_order_increased"


def test_confirmation_page_without_regular_items_disables_boundary():
    boundary = PostTimeBoundary(200)
    boundary.observe_page([_item("in", 220), _item("old", 190)])
    decision = boundary.observe_page([_item("pinned", 100, is_top=True)], is_pinned=lambda _: True)
    assert decision.degraded_reason == "confirmation_page_without_regular_items"
```

- [ ] **Step 2: Run the tests and verify the module is missing**

Run in each worktree:

```bash
python -m pytest -q tests/test_post_time_pagination.py
```

Expected: collection error with `ModuleNotFoundError: core.user_modes.post_time_boundary`.

- [ ] **Step 3: Implement the pure state machine in both repos**

Create the same `core/user_modes/post_time_boundary.py` in both worktrees:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


@dataclass(frozen=True)
class TimeBoundaryDecision:
    should_stop: bool = False
    degraded_reason: Optional[str] = None


class PostTimeBoundary:
    def __init__(self, start_ts: Optional[int]):
        self._start_ts = start_ts
        self._enabled = start_ts is not None
        self._last_timestamp: Optional[int] = None
        self._boundary_seen = False

    def observe_page(
        self,
        items: List[Dict[str, Any]],
        *,
        is_pinned: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ) -> TimeBoundaryDecision:
        if not self._enabled:
            return TimeBoundaryDecision()
        regular = [item for item in items if not self._is_pinned(item, is_pinned)]
        if not regular:
            reason = "confirmation_page_without_regular_items" if self._boundary_seen else None
            return self._degrade(reason) if reason else TimeBoundaryDecision()
        timestamps = self._timestamps(regular)
        if timestamps is None:
            return self._degrade("missing_or_invalid_create_time")
        if self._order_increased(timestamps):
            return self._degrade("time_order_increased")
        self._last_timestamp = timestamps[-1]
        if self._boundary_seen:
            if all(value < self._start_ts for value in timestamps):
                return TimeBoundaryDecision(should_stop=True)
            return self._degrade("time_range_reentry")
        self._boundary_seen = any(value < self._start_ts for value in timestamps)
        return TimeBoundaryDecision()

    @staticmethod
    def _is_pinned(item, checker) -> bool:
        if checker is not None:
            return checker(item)
        value = item.get("is_top")
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @staticmethod
    def _timestamps(items: List[Dict[str, Any]]) -> Optional[List[int]]:
        timestamps: List[int] = []
        for item in items:
            try:
                value = int(item.get("create_time"))
            except (TypeError, ValueError):
                return None
            if value <= 0:
                return None
            timestamps.append(value)
        return timestamps

    def _order_increased(self, timestamps: List[int]) -> bool:
        if any(current > previous for previous, current in zip(timestamps, timestamps[1:])):
            return True
        return self._last_timestamp is not None and timestamps[0] > self._last_timestamp

    def _degrade(self, reason: str) -> TimeBoundaryDecision:
        self._enabled = False
        return TimeBoundaryDecision(degraded_reason=reason)
```

- [ ] **Step 4: Run focused tests and commit both repos**

Run in each worktree:

```bash
python -m pytest -q tests/test_post_time_pagination.py
ruff check core/user_modes/post_time_boundary.py tests/test_post_time_pagination.py
```

Expected: PASS. Then commit separately in each repository:

```bash
git add core/user_modes/post_time_boundary.py tests/test_post_time_pagination.py
git commit -m "feat(download): 增加主页时间边界判断"
```

### Task 3: Integrate early stop into post pagination

**Files:**
- Modify in both repos: `core/user_modes/post_strategy.py:1-260`
- Extend in both repos: `tests/test_post_time_pagination.py`
- Regression only: `tests/test_user_mode_strategies.py`, `tests/test_user_downloader.py`, `tests/test_video_downloader.py`

**Interfaces:**
- Consumes: `BaseDownloader._time_range_bounds()` from Task 1.
- Consumes: `PostTimeBoundary.observe_page()` from Task 2.
- Preserves: `_PostPageResult = Tuple[List[Dict[str, Any]], bool]`; the boolean remains `pagination_restricted` only.
- Produces: progress detail `已到达起始日期，提前结束翻页（检查 {pages} 页，共 {items} 条）`.

- [ ] **Step 1: Add failing integration tests to both repos**

Extend `tests/test_post_time_pagination.py` with a real `UserDownloader` harness. Use `ConfigLoader`, assign only the required fields on an `object.__new__(UserDownloader)` instance, and use this exact API/progress scaffold:

```python
import asyncio
from datetime import datetime

from config import ConfigLoader
from core.user_downloader import UserDownloader
from core.user_modes.post_strategy import PostUserModeStrategy


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
    return {"items": items, "has_more": has_more, "max_cursor": next_cursor, "status_code": 0}


def _ts(value):
    return int(datetime.strptime(value, "%Y-%m-%d %H:%M:%S").timestamp())


def _strategy(pages, *, start="2026-08-23", end="2026-08-25", number=0, media_types=None):
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
```

Add these exact behavioral tests:

```python
def test_post_stops_after_old_confirmation_page_without_browser_recovery():
    pages = {
        0: _page([_item("too-new", _ts("2026-08-26 12:00:00")), _item("in-1", _ts("2026-08-25 12:00:00"))], next_cursor=1),
        1: _page([_item("in-2", _ts("2026-08-23 12:00:00")), _item("old-1", _ts("2026-08-22 12:00:00"))], next_cursor=2),
        2: _page([_item("old-2", _ts("2026-08-21 12:00:00"))], next_cursor=3),
        3: _page([_item("never", _ts("2026-08-20 12:00:00"))], next_cursor=3, has_more=False),
    }
    strategy, downloader = _strategy(pages)
    items = asyncio.run(strategy.collect_items("sec", {"aweme_count": 3000}))
    assert [item["aweme_id"] for item in strategy.apply_filters(items)] == ["in-1", "in-2"]
    assert downloader.api_client.calls == [0, 1, 2]
    assert any("提前结束翻页" in detail for _, detail in downloader.progress_reporter.updates)


def test_missing_confirmation_time_degrades_and_scans_to_natural_end():
    pages = {
        0: _page([_item("in", _ts("2026-08-23 12:00:00")), _item("old", _ts("2026-08-22 12:00:00"))], next_cursor=1),
        1: _page([_item("missing")], next_cursor=2),
        2: _page([_item("older", _ts("2026-08-21 12:00:00"))], next_cursor=2, has_more=False),
    }
    strategy, downloader = _strategy(pages)
    asyncio.run(strategy.collect_items("sec", {"aweme_count": 4}))
    assert downloader.api_client.calls == [0, 1, 2]


def test_number_limit_counts_only_time_and_media_candidates():
    gallery = {"image_post_info": {"images": [{}]}}
    pages = {
        0: _page([_item("too-new", _ts("2026-08-26 12:00:00")), {**_item("video", _ts("2026-08-25 12:00:00")), "video": {}}], next_cursor=1),
        1: _page([{**_item("gallery-1", _ts("2026-08-25 11:00:00")), **gallery}], next_cursor=2),
        2: _page([{**_item("gallery-2", _ts("2026-08-24 11:00:00")), **gallery}], next_cursor=3),
        3: _page([{**_item("never", _ts("2026-08-24 10:00:00")), **gallery}], next_cursor=3, has_more=False),
    }
    strategy, downloader = _strategy(pages, number=2, media_types=["gallery"])
    items = asyncio.run(strategy.collect_items("sec", {"aweme_count": 6}))
    assert [item["aweme_id"] for item in strategy.apply_filters(items)] == ["gallery-1", "gallery-2"]
    assert downloader.api_client.calls == [0, 1, 2]


def test_only_end_time_keeps_full_pagination():
    pages = {
        0: _page([_item("too-new", _ts("2026-08-26 12:00:00"))], next_cursor=1),
        1: _page([_item("in", _ts("2026-08-25 12:00:00"))], next_cursor=1, has_more=False),
    }
    strategy, downloader = _strategy(pages, start="", end="2026-08-25")
    items = asyncio.run(strategy.collect_items("sec", {"aweme_count": 2}))
    assert [item["aweme_id"] for item in strategy.apply_filters(items)] == ["in"]
    assert downloader.api_client.calls == [0, 1]
```

- [ ] **Step 2: Run integration tests and verify they fail before wiring**

Run in each worktree:

```bash
python -m pytest -q tests/test_post_time_pagination.py
```

Expected: the pure state-machine tests pass, while integration tests fail because post pagination still walks to natural completion, reports pagination restriction, or counts raw/unfiltered items.

- [ ] **Step 3: Wire the state machine into `PostUserModeStrategy`**

Import `PostTimeBoundary` and `TimeBoundaryDecision`. In `_collect_api_items()`:

1. Read `start_ts` through `getattr(self.downloader, "_time_range_bounds", None)` so existing lightweight test doubles without the new private method keep early-stop disabled.
2. Keep `candidate_count`, and add only `_count_page_candidates(page_items)` per page.
3. Observe raw `page_items` with `is_pinned=getattr(self.downloader, "_is_pinned_aweme", None)`; never observe the already-filtered `aweme_list`.
4. Pass `time_boundary_reached=decision.should_stop` into `_page_stop_decision()` after its cursor-stall check.
5. Log `decision.degraded_reason` once, and on deliberate stop report the approved progress detail and return `(aweme_list, False)`.
6. Remove the pre-filter `aweme_list[:number_limit]` slice; `apply_filters()` remains the final precise limiter.

Use these exact helper bodies/signature changes:

```python
def _time_boundary_for_config(self) -> PostTimeBoundary:
    bounds_getter = getattr(self.downloader, "_time_range_bounds", None)
    start_ts = bounds_getter()[0] if callable(bounds_getter) else None
    return PostTimeBoundary(start_ts)

def _observe_time_boundary(
    self,
    boundary: PostTimeBoundary,
    page_items: List[Dict[str, Any]],
    page_number: int,
) -> TimeBoundaryDecision:
    decision = boundary.observe_page(
        page_items,
        is_pinned=getattr(self.downloader, "_is_pinned_aweme", None),
    )
    if decision.degraded_reason:
        logger.warning(
            "Post time early-stop disabled: page=%s reason=%s",
            page_number,
            decision.degraded_reason,
        )
    return decision

def _count_page_candidates(self, items: List[Dict[str, Any]]) -> int:
    filtered = self._filter_pinned_items(items)
    filtered = self.downloader._filter_by_time(filtered)
    return len(self._filter_by_media_type(filtered))

def _report_time_boundary_stop(self, page_number: int, raw_items_seen: int) -> None:
    detail = (
        f"已到达起始日期，提前结束翻页（检查 {page_number} 页，"
        f"共 {raw_items_seen} 条）"
    )
    self.downloader._progress_update_step("拉取作品列表", detail)
    logger.info(
        "User post pagination stopped at time boundary: pages=%s raw_items=%s",
        page_number,
        raw_items_seen,
    )

def _page_stop_decision(
    self,
    *,
    has_more: bool,
    next_cursor: int,
    request_cursor: int,
    limit_reached: bool,
    time_boundary_reached: bool,
    raw_page_count: int,
    raw_items_seen: int,
    user_info: Dict[str, Any],
) -> Tuple[bool, bool]:
    if self._cursor_stalled(has_more, next_cursor, request_cursor):
        return True, True
    if time_boundary_reached:
        return True, False
    if has_more:
        return limit_reached, False
    ended_early = raw_page_count >= _POST_PAGE_SIZE or self._profile_reports_more(
        user_info, raw_items_seen
    )
    if ended_early and not limit_reached:
        logger.warning(
            "User post pagination may have ended early: fetched=%s, profile_count=%s",
            raw_items_seen,
            user_info.get("aweme_count"),
        )
    return True, ended_early and not limit_reached
```

Initialize and consume those helpers in `_collect_api_items()` with this exact flow; remove the old `media_filter_enabled` local, the `_number_limit_reached()` call/method, and the pre-filter list slice:

```python
time_boundary = self._time_boundary_for_config()
candidate_count = 0

# Inside the loop, immediately after page normalization:
page_items = self.select_items(page)
raw_page_count = self._append_page_items(page, aweme_list)
if raw_page_count == 0:
    return aweme_list, self._empty_page_is_restricted(page, request_cursor)
raw_items_seen += raw_page_count
candidate_count += self._count_page_candidates(page_items)
has_more = bool(page.get("has_more", False))
max_cursor = int(page.get("max_cursor", 0) or 0)
time_decision = self._observe_time_boundary(
    time_boundary,
    page_items,
    page_number,
)
limit_reached = number_limit > 0 and candidate_count >= number_limit
should_stop, pagination_restricted = self._page_stop_decision(
    has_more=has_more,
    next_cursor=max_cursor,
    request_cursor=request_cursor,
    limit_reached=limit_reached,
    time_boundary_reached=time_decision.should_stop,
    raw_page_count=raw_page_count,
    raw_items_seen=raw_items_seen,
    user_info=user_info,
)
if should_stop:
    if time_decision.should_stop and not pagination_restricted:
        self._report_time_boundary_stop(page_number, raw_items_seen)
    return aweme_list, pagination_restricted
```

Keep `_collect_api_items()` and every new helper at 50 lines or fewer. The state machine stays in `post_time_boundary.py`, keeping `post_strategy.py` at or below 300 lines.

- [ ] **Step 4: Run focused and existing regression suites**

Run in each worktree:

```bash
ruff format core/downloader_base.py core/user_modes/post_strategy.py core/user_modes/post_time_boundary.py tests/test_time_range_filter.py tests/test_post_time_pagination.py
python -m pytest -q tests/test_post_time_pagination.py tests/test_user_mode_strategies.py tests/test_user_downloader.py tests/test_video_downloader.py
ruff check core/downloader_base.py core/user_modes/post_strategy.py core/user_modes/post_time_boundary.py tests/test_time_range_filter.py tests/test_post_time_pagination.py
```

Expected: PASS, including existing cursor-stall/browser-recovery and disk-based missing-file tests.

- [ ] **Step 5: Commit both repos**

Run separately in each repository:

```bash
git add core/user_modes/post_strategy.py tests/test_post_time_pagination.py
git commit -m "feat(download): 主页按时间提前停止翻页"
```

### Task 4: Register shared files and pass the delivery gates

**Files:**
- Modify Desktop only: `scripts/sync-to-cli.sh:118-190`
- Verify identical across repos: `core/downloader_base.py`, `core/user_modes/post_strategy.py`, `core/user_modes/post_time_boundary.py`, `tests/test_time_range_filter.py`, `tests/test_post_time_pagination.py`
- Verify unchanged behavior: `tests/test_user_mode_strategies.py`, `tests/test_user_downloader.py`, `tests/test_video_downloader.py`

**Interfaces:**
- Extends Desktop `SHARED_FILES` with the new production helper and two focused test modules.
- Produces byte-identical touched shared files in the paired worktrees.

- [ ] **Step 1: Add new files to the Desktop sync manifest**

Add these exact entries to the appropriate `core` and `tests` sections of `scripts/sync-to-cli.sh`:

```bash
core/user_modes/post_time_boundary.py
tests/test_time_range_filter.py
tests/test_post_time_pagination.py
```

- [ ] **Step 2: Validate the sync script and byte parity**

Run from `/Users/crimson/codes/douyin/.worktrees/douyin-downloader-desktop-post-time-range-early-stop`:

```bash
bash -n scripts/sync-to-cli.sh
python -m pytest -q tests/test_sync_to_cli_script.py
cmp core/downloader_base.py /Users/crimson/codes/douyin/.worktrees/douyin-downloader-post-time-range-early-stop/core/downloader_base.py
cmp core/user_modes/post_strategy.py /Users/crimson/codes/douyin/.worktrees/douyin-downloader-post-time-range-early-stop/core/user_modes/post_strategy.py
cmp core/user_modes/post_time_boundary.py /Users/crimson/codes/douyin/.worktrees/douyin-downloader-post-time-range-early-stop/core/user_modes/post_time_boundary.py
cmp tests/test_time_range_filter.py /Users/crimson/codes/douyin/.worktrees/douyin-downloader-post-time-range-early-stop/tests/test_time_range_filter.py
cmp tests/test_post_time_pagination.py /Users/crimson/codes/douyin/.worktrees/douyin-downloader-post-time-range-early-stop/tests/test_post_time_pagination.py
```

Expected: all commands exit 0. Do not run the hard-coded `--check` against the dirty main CLI checkout; paired-worktree `cmp` is the approved equivalent for this branch.

- [ ] **Step 3: Commit the Desktop sync-manifest change**

```bash
git add scripts/sync-to-cli.sh
git commit -m "chore(sync): 纳入主页时间停页共享文件"
```

- [ ] **Step 4: Run fresh full verification in both worktrees**

Run in Desktop:

```bash
python -m pytest tests/
ruff check .
ruff format --check core/downloader_base.py core/user_modes/post_strategy.py core/user_modes/post_time_boundary.py tests/test_time_range_filter.py tests/test_post_time_pagination.py
```

Run in CLI:

```bash
python -m pytest tests/
ruff check .
ruff format --check core/downloader_base.py core/user_modes/post_strategy.py core/user_modes/post_time_boundary.py tests/test_time_range_filter.py tests/test_post_time_pagination.py
```

Expected: both full pytest suites, both ruff checks, and targeted format checks pass. Record exact counts and exit codes; do not claim completion if either repository is unverified.

- [ ] **Step 5: Run independent review and final re-verification**

Invoke `superpowers:requesting-code-review` in a separate local reviewer context over each branch's diff from its base. Fix every validated Important or Critical finding using `superpowers:receiving-code-review`, rerun the affected targeted tests, then rerun Step 4 and the five `cmp` checks.

Expected: reviewer reports no remaining actionable findings, both worktrees are clean, and no push or merge has occurred.
