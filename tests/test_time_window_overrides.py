"""min_create_time / max_create_time per-job window (spec §10).

Shared-file change: mirrored from the desktop sibling repo.
"""

from __future__ import annotations

from typing import Any, Dict


def _bounds(config_dict: Dict[str, Any]):
    from core.downloader_base import BaseDownloader

    class _Stub(BaseDownloader):  # pragma: no cover - thin harness
        def __init__(self):
            self.config = type(
                "Cfg", (), {"get": lambda _s, key, default=None: config_dict.get(key, default)}
            )()

        async def download(self, parsed_url):
            raise NotImplementedError

    return _Stub()._time_range_bounds()


def test_min_create_time_maps_to_exclusive_start():
    start_ts, end_ts = _bounds({"min_create_time": 1700000000})
    assert start_ts == 1700000001
    assert end_ts is None


def test_max_create_time_maps_to_inclusive_end():
    start_ts, end_ts = _bounds({"max_create_time": 1700000500})
    assert start_ts is None
    assert end_ts == 1700000501


def test_window_combines_with_start_time_string():
    # start_time 字符串与 min_create_time 取更晚者
    import datetime

    day = datetime.datetime(2024, 1, 1)
    day_ts = int(day.timestamp())
    start_ts, _ = _bounds({"start_time": "2024-01-01", "min_create_time": day_ts + 100})
    assert start_ts == day_ts + 101
    start_ts2, _ = _bounds({"start_time": "2024-01-01", "min_create_time": day_ts - 100})
    assert start_ts2 == day_ts
