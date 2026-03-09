import asyncio

from core.user_modes.collect_mix_strategy import CollectMixUserModeStrategy
from core.user_modes.collect_strategy import CollectUserModeStrategy
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


def test_post_strategy_calls_browser_recover_when_cursor_stalls():
    class _API:
        async def get_user_post(self, _sec_uid, max_cursor=0, count=20):
            return {
                "items": [_make_aweme("333")],
                "has_more": True,
                "max_cursor": max_cursor,
                "status_code": 0,
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
            aweme_list.append(_make_aweme("444"))

    downloader = _Downloader()
    strategy = PostUserModeStrategy(downloader)
    items = asyncio.run(strategy.collect_items("sec_uid_x", {"uid": "uid-1"}))

    assert downloader.recovered_called is True
    assert [item["aweme_id"] for item in items] == ["333", "444"]


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


def test_mix_strategy_expansion_does_not_apply_number_limit_early():
    class _API:
        async def get_user_mix(self, _sec_uid, max_cursor=0, count=20):
            return {
                "items": [{"mix_info": {"mix_id": "mix-1"}}],
                "has_more": False,
                "max_cursor": 0,
            }

        async def get_mix_aweme(self, _mix_id, cursor=0, count=20):
            return {
                "items": [{"aweme_id": "m-1"}, {"aweme_id": "m-2"}],
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
                        "number": {"mix": 1},
                        "increase": {"mix": False},
                    }.get(key, default)
                },
            )()
            self._filter_by_time = lambda items: items
            self._limit_count = lambda items, _mode: items

    strategy = MixUserModeStrategy(_Downloader())
    items = asyncio.run(strategy.collect_items("sec_uid_x", {"uid": "uid-1"}))
    assert [item["aweme_id"] for item in items] == ["m-1", "m-2"]


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


def test_music_strategy_expansion_does_not_apply_number_limit_early():
    class _API:
        async def get_user_music(self, _sec_uid, max_cursor=0, count=20):
            return {
                "items": [{"music_info": {"id": "music-1"}}],
                "has_more": False,
                "max_cursor": 0,
            }

        async def get_music_aweme(self, _music_id, cursor=0, count=20):
            return {
                "items": [{"aweme_id": "mu-1"}, {"aweme_id": "mu-2"}],
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
                        "number": {"music": 1},
                        "increase": {"music": False},
                    }.get(key, default)
                },
            )()
            self._filter_by_time = lambda items: items
            self._limit_count = lambda items, _mode: items

    strategy = MusicUserModeStrategy(_Downloader())
    items = asyncio.run(strategy.collect_items("sec_uid_x", {"uid": "uid-1"}))
    assert [item["aweme_id"] for item in items] == ["mu-1", "mu-2"]


def test_collect_strategy_expands_collect_folders_and_deduplicates_aweme():
    class _API:
        async def get_user_collects(self, _sec_uid, max_cursor=0, count=20):
            if max_cursor > 0:
                return {"items": [], "has_more": False, "max_cursor": max_cursor}
            return {
                "items": [
                    {"collects_id_str": "collect-1"},
                    {"collects_id_str": "collect-2"},
                ],
                "has_more": False,
                "max_cursor": 0,
            }

        async def get_collect_aweme(self, collects_id, max_cursor=0, count=20):
            assert max_cursor == 0
            if collects_id == "collect-1":
                return {
                    "items": [{"aweme_id": "c-1"}, {"aweme_id": "dup"}],
                    "has_more": False,
                    "max_cursor": 0,
                }
            return {
                "items": [{"aweme_id": "dup"}, {"aweme_id": "c-2"}],
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
                        "number": {"collect": 0},
                        "increase": {"collect": False},
                    }.get(key, default)
                },
            )()
            self._filter_by_time = lambda items: items
            self._limit_count = lambda items, _mode: items

    strategy = CollectUserModeStrategy(_Downloader())
    items = asyncio.run(strategy.collect_items("self", {"uid": "self"}))
    assert [item["aweme_id"] for item in items] == ["c-1", "dup", "c-2"]


def test_collect_strategy_expansion_does_not_apply_number_limit_or_increase_early():
    class _Database:
        async def get_latest_aweme_time(self, _author_id):
            return 1700000000

    class _API:
        async def get_user_collects(self, _sec_uid, max_cursor=0, count=20):
            return {
                "items": [
                    {"collects_id_str": "collect-1"},
                    {"collects_id_str": "collect-2"},
                ],
                "has_more": False,
                "max_cursor": 0,
            }

        async def get_collect_aweme(self, collects_id, max_cursor=0, count=20):
            if collects_id == "collect-1":
                return {
                    "items": [{"aweme_id": "c-1", "create_time": 1700000001}],
                    "has_more": False,
                    "max_cursor": 0,
                }
            return {
                "items": [{"aweme_id": "c-2", "create_time": 1700000002}],
                "has_more": False,
                "max_cursor": 0,
            }

    class _Downloader:
        def __init__(self):
            self.api_client = _API()
            self.rate_limiter = _NoopRateLimiter()
            self.database = _Database()
            self.config = type(
                "Cfg",
                (),
                {
                    "get": lambda _self, key, default=None: {
                        "number": {"collect": 1},
                        "increase": {"collect": True},
                    }.get(key, default)
                },
            )()
            self._filter_by_time = lambda items: items
            self._limit_count = lambda items, _mode: items

    strategy = CollectUserModeStrategy(_Downloader())
    items = asyncio.run(strategy.collect_items("self", {"uid": "self"}))
    assert [item["aweme_id"] for item in items] == ["c-1", "c-2"]


def test_collect_mix_strategy_expands_collected_mix_items():
    class _API:
        async def get_user_collect_mix(self, _sec_uid, max_cursor=0, count=20):
            return {
                "items": [
                    {"mix_info": {"mix_id": "mix-1"}},
                    {"mix_id": "mix-2"},
                ],
                "has_more": False,
                "max_cursor": 0,
            }

        async def get_mix_aweme(self, mix_id, cursor=0, count=20):
            if mix_id == "mix-1":
                return {
                    "items": [{"aweme_id": "mix-aweme-1"}],
                    "has_more": False,
                    "max_cursor": 0,
                }
            return {
                "items": [{"aweme_id": "mix-aweme-2"}],
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
                        "number": {"collectmix": 0},
                        "increase": {"collectmix": False},
                    }.get(key, default)
                },
            )()
            self._filter_by_time = lambda items: items
            self._limit_count = lambda items, _mode: items

    strategy = CollectMixUserModeStrategy(_Downloader())
    items = asyncio.run(strategy.collect_items("self", {"uid": "self"}))
    assert [item["aweme_id"] for item in items] == ["mix-aweme-1", "mix-aweme-2"]


def test_collect_mix_strategy_keeps_direct_aweme_items_and_expands_remaining_metadata():
    class _API:
        async def get_user_collect_mix(self, _sec_uid, max_cursor=0, count=20):
            return {
                "items": [
                    {"aweme_id": "mix-preview-1"},
                    {"mix_info": {"mix_id": "mix-1"}},
                ],
                "has_more": False,
                "max_cursor": 0,
            }

        async def get_mix_aweme(self, mix_id, cursor=0, count=20):
            assert mix_id == "mix-1"
            return {
                "items": [{"aweme_id": "mix-aweme-1"}],
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
                        "number": {"collectmix": 0},
                        "increase": {"collectmix": False},
                    }.get(key, default)
                },
            )()
            self._filter_by_time = lambda items: items
            self._limit_count = lambda items, _mode: items

    strategy = CollectMixUserModeStrategy(_Downloader())
    items = asyncio.run(strategy.collect_items("self", {"uid": "self"}))
    assert [item["aweme_id"] for item in items] == ["mix-preview-1", "mix-aweme-1"]


def test_collect_mix_strategy_expansion_does_not_apply_number_limit_or_increase_early():
    class _Database:
        async def get_latest_aweme_time(self, _author_id):
            return 1700000000

    class _API:
        async def get_user_collect_mix(self, _sec_uid, max_cursor=0, count=20):
            return {
                "items": [
                    {"mix_info": {"mix_id": "mix-1"}},
                    {"mix_info": {"mix_id": "mix-2"}},
                ],
                "has_more": False,
                "max_cursor": 0,
            }

        async def get_mix_aweme(self, mix_id, cursor=0, count=20):
            if mix_id == "mix-1":
                return {
                    "items": [{"aweme_id": "mix-aweme-1", "create_time": 1700000001}],
                    "has_more": False,
                    "max_cursor": 0,
                }
            return {
                "items": [{"aweme_id": "mix-aweme-2", "create_time": 1700000002}],
                "has_more": False,
                "max_cursor": 0,
            }

    class _Downloader:
        def __init__(self):
            self.api_client = _API()
            self.rate_limiter = _NoopRateLimiter()
            self.database = _Database()
            self.config = type(
                "Cfg",
                (),
                {
                    "get": lambda _self, key, default=None: {
                        "number": {"collectmix": 1},
                        "increase": {"collectmix": True},
                    }.get(key, default)
                },
            )()
            self._filter_by_time = lambda items: items
            self._limit_count = lambda items, _mode: items

    strategy = CollectMixUserModeStrategy(_Downloader())
    items = asyncio.run(strategy.collect_items("self", {"uid": "self"}))
    assert [item["aweme_id"] for item in items] == ["mix-aweme-1", "mix-aweme-2"]
