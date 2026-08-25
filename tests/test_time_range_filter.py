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
    assert [item["aweme_id"] for item in downloader._filter_by_time(items)] == [
        "start",
        "end",
    ]


def test_filter_by_time_with_only_end_time_keeps_end_day():
    downloader = _downloader(start_time="", end_time="2026-08-20")
    items = [
        {"aweme_id": "end", "create_time": _ts("2026-08-20 23:59:59")},
        {"aweme_id": "after", "create_time": _ts("2026-08-21 00:00:00")},
    ]
    assert [item["aweme_id"] for item in downloader._filter_by_time(items)] == ["end"]
