import asyncio
from datetime import datetime

from core.homepage_screenshot import next_homepage_screenshot_path, request_homepage_screenshot

_NOW = datetime(2026, 11, 28, 9, 30, 15)
# desktop/src/main/homepage-screenshot.test.ts 钉的是同一个文件名：
# 两边格式一旦漂移，Electron 会拒收请求，真实 App 里一张都存不下来。
_EXPECTED_NAME = "主页截图_2026-11-28_0930.png"


class _FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 28, 9, 30, 15)


class _RecordingAPI:
    def __init__(self):
        self.calls = []

    async def save_user_homepage_screenshot(self, sec_uid, save_path, *, profile=None):
        self.calls.append((sec_uid, save_path, profile))
        return True


def test_first_screenshot_of_the_day_is_named_with_date_and_time(tmp_path):
    assert next_homepage_screenshot_path(tmp_path, _NOW) == tmp_path / _EXPECTED_NAME


def test_same_day_screenshot_is_not_taken_again(tmp_path):
    (tmp_path / "主页截图_2026-11-28_0815.png").write_bytes(b"png")

    assert next_homepage_screenshot_path(tmp_path, _NOW) is None


def test_earlier_days_and_legacy_file_do_not_block_today(tmp_path):
    (tmp_path / "主页截图_2026-11-27_2359.png").write_bytes(b"yesterday")
    (tmp_path / "主页截图.png").write_bytes(b"legacy")

    assert next_homepage_screenshot_path(tmp_path, _NOW) == tmp_path / _EXPECTED_NAME


def test_unfinished_temp_files_do_not_count_as_taken(tmp_path):
    # Electron 与 Playwright 都先写点开头的 .tmp 再改名；没改名成功的不算今天已截过。
    (tmp_path / ".主页截图_2026-11-28_0815.png.4242.tmp").write_bytes(b"partial")
    (tmp_path / ".主页截图_2026-11-28_0815.png.tmp").write_bytes(b"partial")

    assert next_homepage_screenshot_path(tmp_path, _NOW) == tmp_path / _EXPECTED_NAME


def test_request_asks_for_todays_stamped_screenshot(tmp_path, monkeypatch):
    monkeypatch.setattr("core.homepage_screenshot.datetime", _FixedClock)
    api = _RecordingAPI()

    asyncio.run(
        request_homepage_screenshot(api, tmp_path, "sec_uid_x", profile={"nickname": "tester"})
    )

    assert api.calls == [
        ("sec_uid_x", (tmp_path / _EXPECTED_NAME).resolve(), {"nickname": "tester"})
    ]


def test_request_is_skipped_when_today_already_has_one(tmp_path, monkeypatch):
    monkeypatch.setattr("core.homepage_screenshot.datetime", _FixedClock)
    (tmp_path / "主页截图_2026-11-28_0815.png").write_bytes(b"png")
    api = _RecordingAPI()

    asyncio.run(request_homepage_screenshot(api, tmp_path, "sec_uid_x"))

    assert api.calls == []
