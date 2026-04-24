"""回归测试：视频与实况图的最高清源选择逻辑。"""

from core.downloader_base import BaseDownloader


class TestPickHighestQualityPlayAddr:
    def test_empty_input_returns_none(self):
        assert BaseDownloader._pick_highest_quality_play_addr({}) is None
        assert BaseDownloader._pick_highest_quality_play_addr({"bit_rate": []}) is None
        assert (
            BaseDownloader._pick_highest_quality_play_addr({"bit_rate": None}) is None
        )

    def test_selects_highest_bit_rate(self):
        video = {
            "bit_rate": [
                {
                    "bit_rate": 500000,
                    "play_addr": {"url_list": ["https://low.example/video"]},
                },
                {
                    "bit_rate": 1500000,
                    "play_addr": {"url_list": ["https://high.example/video"]},
                },
                {
                    "bit_rate": 900000,
                    "play_addr": {"url_list": ["https://mid.example/video"]},
                },
            ]
        }
        best = BaseDownloader._pick_highest_quality_play_addr(video)
        assert best is not None
        assert best["url_list"] == ["https://high.example/video"]

    def test_tie_breaks_by_width(self):
        video = {
            "bit_rate": [
                {
                    "bit_rate": 1000000,
                    "play_addr": {
                        "url_list": ["https://narrow.example/video"],
                        "width": 1080,
                    },
                },
                {
                    "bit_rate": 1000000,
                    "play_addr": {
                        "url_list": ["https://wider.example/video"],
                        "width": 1440,
                    },
                },
            ]
        }
        best = BaseDownloader._pick_highest_quality_play_addr(video)
        assert best is not None
        assert best["url_list"] == ["https://wider.example/video"]

    def test_ignores_malformed_entries(self):
        video = {
            "bit_rate": [
                "not a dict",
                {"bit_rate": "invalid"},
                {"bit_rate": 800000},  # no play_addr
                {
                    "bit_rate": 600000,
                    "play_addr": {"url_list": ["https://valid.example/v"]},
                },
            ]
        }
        best = BaseDownloader._pick_highest_quality_play_addr(video)
        assert best is not None
        assert best["url_list"] == ["https://valid.example/v"]


def test_collect_image_live_urls_prefers_high_bitrate(tmp_path):
    """实况图解析应优先采用 bit_rate 最高的 play_addr。"""
    # 用真实的 VideoDownloader 实例化来验证（_collect_image_live_urls 是实例方法）。
    from auth import CookieManager
    from config import ConfigLoader
    from control import QueueManager, RateLimiter, RetryHandler
    from core.api_client import DouyinAPIClient
    from core.video_downloader import VideoDownloader
    from storage import FileManager

    config = ConfigLoader()
    config.update(path=str(tmp_path))
    downloader = VideoDownloader(
        config,
        DouyinAPIClient({}),
        FileManager(str(tmp_path)),
        CookieManager(str(tmp_path / ".cookies.json")),
        database=None,
        rate_limiter=RateLimiter(max_per_second=5),
        retry_handler=RetryHandler(max_retries=1),
        queue_manager=QueueManager(max_workers=1),
    )

    aweme_data = {
        "image_post_info": {
            "images": [
                {
                    "video": {
                        "bit_rate": [
                            {
                                "bit_rate": 500000,
                                "play_addr": {"url_list": ["https://low.example/live"]},
                            },
                            {
                                "bit_rate": 2_000_000,
                                "play_addr": {
                                    "url_list": ["https://high.example/live"]
                                },
                            },
                        ],
                        "play_addr": {"url_list": ["https://fallback.example/live"]},
                    }
                }
            ]
        }
    }

    urls = downloader._collect_image_live_urls(aweme_data)
    assert urls == ["https://high.example/live"]
