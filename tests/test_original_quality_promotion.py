"""回归测试:original 画质下探测 ratio=default 原画并按实际大小置顶。

Web detail API 的 ``video.bit_rate`` 阶梯不含原画档(实测最高档可比原画
小 8 倍),原画只能通过 ``/aweme/v1/play/?ratio=default`` 的 302 获取;
但也存在超分重编码档大于原画的反例,因此必须按探测到的真实大小比较,
不能盲目置顶。
"""

import asyncio
from unittest.mock import Mock

import pytest


def _build_video_downloader(tmp_path, video_quality="original"):
    from auth import CookieManager
    from config import ConfigLoader
    from control import QueueManager, RateLimiter, RetryHandler
    from core.api_client import DouyinAPIClient
    from core.video_downloader import VideoDownloader
    from storage import FileManager

    config = ConfigLoader()
    config.update(path=str(tmp_path), video_quality=video_quality)
    return VideoDownloader(
        config,
        DouyinAPIClient({}),
        FileManager(str(tmp_path)),
        CookieManager(str(tmp_path / ".cookies.json")),
        database=None,
        rate_limiter=RateLimiter(max_per_second=5),
        retry_handler=RetryHandler(max_retries=1),
        queue_manager=QueueManager(max_workers=1),
    )


class _FakeResponse:
    def __init__(
        self,
        status=206,
        total_size=70_000_000,
        url="https://v99-coldx.douyinvod.com/original/video.mp4",
        content_type="video/mp4",
        headers=None,
    ):
        self.status = status
        self.url = url
        self.content_type = content_type
        self.headers = (
            headers if headers is not None else {"Content-Range": f"bytes 0-0/{total_size}"}
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error is not None:
            raise self.error
        return self.response


def _aweme(gear_size=8_000_000):
    return {
        "aweme_id": "7561990225096166698",
        "video": {
            "play_addr": {
                "uri": "v02f52g10003xxxx",
                "url_list": ["https://v26-web.douyinvod.com/gear.mp4"],
                "width": 1920,
                "height": 1080,
                "data_size": gear_size,
            },
            "bit_rate": [
                {
                    "bit_rate": 1054108,
                    "play_addr": {
                        "uri": "v02f52g10003xxxx",
                        "url_list": ["https://v26-web.douyinvod.com/gear.mp4"],
                        "width": 1920,
                        "height": 1080,
                        "data_size": gear_size,
                    },
                }
            ],
        },
    }


def _prepare(downloader, session_response=None, session_error=None):
    downloader.api_client = Mock(wraps=downloader.api_client)
    downloader.api_client.build_signed_path = Mock(
        return_value=("https://www.douyin.com/aweme/v1/play/?signed=1", "UA-Probe")
    )
    downloader.api_client.headers = {"User-Agent": "UA-Default"}
    downloader.api_client.BASE_URL = "https://www.douyin.com"
    return _FakeSession(response=session_response, error=session_error)


class TestOriginalPromotion:
    def test_promotes_original_when_larger(self, tmp_path):
        downloader = _build_video_downloader(tmp_path)
        aweme = _aweme(gear_size=8_000_000)
        base = downloader._build_video_url_candidates(aweme)
        session = _prepare(downloader, session_response=_FakeResponse(total_size=70_000_000))

        result = asyncio.run(
            downloader._maybe_promote_original_candidate(aweme, list(base), session)
        )

        assert result[0][0] == "https://v99-coldx.douyinvod.com/original/video.mp4"
        assert result[1:] == base
        # 探测请求必须只取 1 字节且带签名 UA
        probe_url, kwargs = session.calls[0]
        assert probe_url == "https://www.douyin.com/aweme/v1/play/?signed=1"
        assert kwargs["headers"]["Range"] == "bytes=0-0"
        assert kwargs["headers"]["User-Agent"] == "UA-Probe"
        # ratio=default 是原画开关
        params = downloader.api_client.build_signed_path.call_args[0][1]
        assert params["ratio"] == "default"

    def test_keeps_gears_when_original_smaller(self, tmp_path):
        downloader = _build_video_downloader(tmp_path)
        aweme = _aweme(gear_size=47_000_000)
        base = downloader._build_video_url_candidates(aweme)
        session = _prepare(downloader, session_response=_FakeResponse(total_size=31_000_000))

        result = asyncio.run(
            downloader._maybe_promote_original_candidate(aweme, list(base), session)
        )

        assert result == base

    def test_promotes_when_gear_size_unknown(self, tmp_path):
        downloader = _build_video_downloader(tmp_path)
        aweme = _aweme(gear_size=None)
        base = downloader._build_video_url_candidates(aweme)
        session = _prepare(downloader, session_response=_FakeResponse(total_size=70_000_000))

        result = asyncio.run(
            downloader._maybe_promote_original_candidate(aweme, list(base), session)
        )

        assert result[0][0] == "https://v99-coldx.douyinvod.com/original/video.mp4"

    def test_no_probe_for_explicit_quality(self, tmp_path):
        downloader = _build_video_downloader(tmp_path, video_quality="1080p")
        aweme = _aweme()
        base = downloader._build_video_url_candidates(aweme)
        session = _prepare(downloader, session_response=_FakeResponse(total_size=70_000_000))

        result = asyncio.run(
            downloader._maybe_promote_original_candidate(aweme, list(base), session)
        )

        assert result == base
        assert session.calls == []

    def test_probe_error_keeps_candidates(self, tmp_path):
        downloader = _build_video_downloader(tmp_path)
        aweme = _aweme()
        base = downloader._build_video_url_candidates(aweme)
        session = _prepare(downloader, session_error=OSError("connect timeout"))

        result = asyncio.run(
            downloader._maybe_promote_original_candidate(aweme, list(base), session)
        )

        assert result == base

    def test_probe_swallows_any_exception(self, tmp_path):
        """探测是 best-effort:假 session 等任意异常不得中断主下载流程。"""
        downloader = _build_video_downloader(tmp_path)
        aweme = _aweme()
        base = downloader._build_video_url_candidates(aweme)
        _prepare(downloader)

        result = asyncio.run(
            downloader._maybe_promote_original_candidate(aweme, list(base), object())
        )

        assert result == base

    def test_restricted_video_sends_set_cookie_flag(self, tmp_path):
        """is_need_set_cookie 的受限作品需带 ss_is_p_v_ss=1(官方 App 行为)。"""
        downloader = _build_video_downloader(tmp_path)
        aweme = _aweme()
        aweme["video"]["is_need_set_cookie"] = True
        base = downloader._build_video_url_candidates(aweme)
        session = _prepare(downloader, session_response=_FakeResponse(total_size=70_000_000))

        asyncio.run(downloader._maybe_promote_original_candidate(aweme, list(base), session))

        params = downloader.api_client.build_signed_path.call_args[0][1]
        assert params["ss_is_p_v_ss"] == "1"

    def test_probe_bad_status_keeps_candidates(self, tmp_path):
        downloader = _build_video_downloader(tmp_path)
        aweme = _aweme()
        base = downloader._build_video_url_candidates(aweme)
        session = _prepare(downloader, session_response=_FakeResponse(status=403))

        result = asyncio.run(
            downloader._maybe_promote_original_candidate(aweme, list(base), session)
        )

        assert result == base

    def test_non_video_content_type_keeps_candidates(self, tmp_path):
        """WAF 拦截页等 200+HTML 响应不得被误判为原画。"""
        downloader = _build_video_downloader(tmp_path)
        aweme = _aweme()
        base = downloader._build_video_url_candidates(aweme)
        session = _prepare(
            downloader,
            session_response=_FakeResponse(
                status=200, total_size=70_000_000, content_type="text/html"
            ),
        )

        result = asyncio.run(
            downloader._maybe_promote_original_candidate(aweme, list(base), session)
        )

        assert result == base

    def test_equal_size_keeps_gears(self, tmp_path):
        """大小相同没有收益,不置顶(避免无谓换源)。"""
        downloader = _build_video_downloader(tmp_path)
        aweme = _aweme(gear_size=8_000_000)
        base = downloader._build_video_url_candidates(aweme)
        session = _prepare(downloader, session_response=_FakeResponse(total_size=8_000_000))

        result = asyncio.run(
            downloader._maybe_promote_original_candidate(aweme, list(base), session)
        )

        assert result == base

    def test_normal_video_omits_set_cookie_flag(self, tmp_path):
        downloader = _build_video_downloader(tmp_path)
        aweme = _aweme()
        base = downloader._build_video_url_candidates(aweme)
        session = _prepare(downloader, session_response=_FakeResponse(total_size=70_000_000))

        asyncio.run(downloader._maybe_promote_original_candidate(aweme, list(base), session))

        params = downloader.api_client.build_signed_path.call_args[0][1]
        assert "ss_is_p_v_ss" not in params

    def test_content_length_fallback(self, tmp_path):
        """无 Content-Range 的 200 响应按 Content-Length 判定总大小。"""
        downloader = _build_video_downloader(tmp_path)
        aweme = _aweme(gear_size=8_000_000)
        base = downloader._build_video_url_candidates(aweme)
        session = _prepare(
            downloader,
            session_response=_FakeResponse(status=200, headers={"Content-Length": "70000000"}),
        )

        result = asyncio.run(
            downloader._maybe_promote_original_candidate(aweme, list(base), session)
        )

        assert result[0][0] == "https://v99-coldx.douyinvod.com/original/video.mp4"

    def test_missing_total_size_keeps_candidates(self, tmp_path):
        downloader = _build_video_downloader(tmp_path)
        aweme = _aweme()
        base = downloader._build_video_url_candidates(aweme)
        session = _prepare(downloader, session_response=_FakeResponse(headers={}))

        result = asyncio.run(
            downloader._maybe_promote_original_candidate(aweme, list(base), session)
        )

        assert result == base

    def test_missing_uri_keeps_candidates(self, tmp_path):
        downloader = _build_video_downloader(tmp_path)
        aweme = _aweme()
        aweme["video"]["play_addr"].pop("uri")
        aweme["video"]["bit_rate"][0]["play_addr"].pop("uri")
        base = downloader._build_video_url_candidates(aweme)
        session = _prepare(downloader, session_response=_FakeResponse(total_size=70_000_000))

        result = asyncio.run(
            downloader._maybe_promote_original_candidate(aweme, list(base), session)
        )

        assert result == base
        assert session.calls == []

    def test_highest_no_longer_probes(self, tmp_path):
        """highest 只取最高转码档,一次探测请求都不发。

        探测的体积(原片可达转码档 8 倍)与耗时(每条多一次请求,超时上限
        10s)代价已迁到显式的 original 档;highest 若仍探测,用户就没有关掉
        探测的办法了。
        """
        downloader = _build_video_downloader(tmp_path, video_quality="highest")
        aweme = _aweme(gear_size=8_000_000)
        base = downloader._build_video_url_candidates(aweme)
        session = _prepare(
            downloader, session_response=_FakeResponse(total_size=70_000_000)
        )

        result = asyncio.run(
            downloader._maybe_promote_original_candidate(aweme, list(base), session)
        )

        assert result == base
        assert session.calls == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
