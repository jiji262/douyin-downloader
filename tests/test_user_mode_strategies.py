import asyncio

from core.user_modes.like_strategy import LikeUserModeStrategy
from core.user_modes.mix_strategy import MixUserModeStrategy
from core.user_modes.music_strategy import MusicUserModeStrategy
from core.user_modes.post_strategy import PostUserModeStrategy


class _NoopRateLimiter:
    async def acquire(self):
        return


def _make_aweme(aweme_id: str):
    return {
        "aweme_id": aweme_id,
        "create_time": 1700000000,
        "video": {"play_addr": {"url_list": ["https://example.com/video.mp4"]}},
    }


def test_like_strategy_collects_items_from_api():
    class _API:
        async def get_user_like(self, _sec_uid, max_cursor=0, count=20):
            if max_cursor > 0:
                return {"items": [], "has_more": False, "max_cursor": max_cursor}
            return {"items": [_make_aweme("111")], "has_more": False, "max_cursor": 0}

    class _Downloader:
        def __init__(self):
            self.api_client = _API()
            self.rate_limiter = _NoopRateLimiter()
            self.config = type("Cfg", (), {"get": lambda _self, key, default=None: {"number": {"like": 0}, "increase": {"like": False}}.get(key, default)})()
            self.database = None
            self._filter_by_time = lambda items: items
            self._limit_count = lambda items, _mode: items

    strategy = LikeUserModeStrategy(_Downloader())
    items = asyncio.run(strategy.collect_items("sec_uid_x", {"uid": "uid-1"}))
    assert [item["aweme_id"] for item in items] == ["111"]


def test_post_strategy_calls_browser_recover_when_pagination_restricted():
    class _API:
        async def get_user_post(self, _sec_uid, max_cursor=0, count=20):
            if max_cursor == 0:
                return {
                    "items": [_make_aweme("111")],
                    "has_more": True,
                    "max_cursor": 123,
                    "status_code": 0,
                }
            return {"items": [], "has_more": False, "max_cursor": max_cursor, "status_code": 0}

    class _Downloader:
        def __init__(self):
            self.api_client = _API()
            self.rate_limiter = _NoopRateLimiter()
            self.database = None
            self.config = type(
                "Cfg",
                (),
                {
                    "get": lambda _self, key, default=None: {
                        "number": {"post": 0},
                        "increase": {"post": False},
                        "browser_fallback": {"enabled": True},
                    }.get(key, default)
                },
            )()
            self.recovered_called = False
            self._progress_update_step = lambda *_args, **_kwargs: None
            self._filter_by_time = lambda items: items
            self._limit_count = lambda items, _mode: items

        async def _recover_user_post_with_browser(self, sec_uid, user_info, aweme_list):
            self.recovered_called = True
            aweme_list.append(_make_aweme("222"))

    downloader = _Downloader()
    strategy = PostUserModeStrategy(downloader)
    items = asyncio.run(strategy.collect_items("sec_uid_x", {"uid": "uid-1"}))

    assert downloader.recovered_called is True
    assert [item["aweme_id"] for item in items] == ["111", "222"]


def test_mix_strategy_filters_partial_aweme_items_without_metadata_inflation():
    class _API:
        async def get_user_mix(self, _sec_uid, max_cursor=0, count=20):
            return {
                "items": [
                    {"aweme_id": "111"},
                    {"mix_info": {"mix_id": "mix-only-meta"}},
                ],
                "has_more": False,
                "max_cursor": 0,
            }

    class _Downloader:
        def __init__(self):
            self.api_client = _API()
            self.rate_limiter = _NoopRateLimiter()
            self.database = None
            self.config = type(
                "Cfg",
                (),
                {
                    "get": lambda _self, key, default=None: {
                        "number": {"mix": 0},
                        "increase": {"mix": False},
                    }.get(key, default)
                },
            )()
            self._filter_by_time = lambda items: items
            self._limit_count = lambda items, _mode: items

    strategy = MixUserModeStrategy(_Downloader())
    items = asyncio.run(strategy.collect_items("sec_uid_x", {"uid": "uid-1"}))
    assert items == [{"aweme_id": "111"}]


def test_music_strategy_filters_partial_aweme_items_without_metadata_inflation():
    class _API:
        async def get_user_music(self, _sec_uid, max_cursor=0, count=20):
            return {
                "items": [
                    {"aweme_id": "222"},
                    {"music_info": {"id": "music-only-meta"}},
                ],
                "has_more": False,
                "max_cursor": 0,
            }

    class _Downloader:
        def __init__(self):
            self.api_client = _API()
            self.rate_limiter = _NoopRateLimiter()
            self.database = None
            self.config = type(
                "Cfg",
                (),
                {
                    "get": lambda _self, key, default=None: {
                        "number": {"music": 0},
                        "increase": {"music": False},
                    }.get(key, default)
                },
            )()
            self._filter_by_time = lambda items: items
            self._limit_count = lambda items, _mode: items

    strategy = MusicUserModeStrategy(_Downloader())
    items = asyncio.run(strategy.collect_items("sec_uid_x", {"uid": "uid-1"}))
    assert items == [{"aweme_id": "222"}]
