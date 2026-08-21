"""下载链路性能修复的行为回归测试。

覆盖三个针对「批量下载慢」的修复：

1. ``_download_first_available`` 多镜像时每个镜像只尝试一次——镜像列表
   本身就是重试机制，不再对每个死镜像做多轮退避重试（旧行为单个封面
   最多空等 20+ 秒）。单一 URL 时保留退避重试。
2. 本地作品索引在单个 downloader 实例内只扫描一次；新 job 使用新实例
   重新扫描磁盘，因此能发现用户在任务之间删除或补回的文件。
3. ``_download_aweme_assets`` 中封面/音乐/头像并行下载且互不阻塞，
   任一可选资产失败不影响主视频成功。
"""

import asyncio
import threading
import time

import pytest

from auth import CookieManager
from config import ConfigLoader
from control import QueueManager, RateLimiter, RetryHandler
from core.api_client import DouyinAPIClient
from core.video_downloader import VideoDownloader
from storage import FileManager


def _build_downloader(tmp_path, max_retries: int = 3):
    config = ConfigLoader()
    config.update(path=str(tmp_path))

    file_manager = FileManager(str(tmp_path))
    cookie_manager = CookieManager(str(tmp_path / ".cookies.json"))
    api_client = DouyinAPIClient({})

    retry_handler = RetryHandler(max_retries=max_retries)
    # 测试里不需要真实退避等待。
    retry_handler.retry_delays = [0]

    downloader = VideoDownloader(
        config,
        api_client,
        file_manager,
        cookie_manager,
        database=None,
        rate_limiter=RateLimiter(max_per_second=100),
        retry_handler=retry_handler,
        queue_manager=QueueManager(max_workers=1),
    )
    return downloader, api_client


# ---------------------------------------------------------------------------
# 1. 镜像回退不再逐镜像多轮重试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_available_tries_each_mirror_once(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path, max_retries=3)

    attempts = []

    async def _fake_download_file(url, save_path, session, **_kwargs):
        attempts.append(url)
        return False

    monkeypatch.setattr(downloader.file_manager, "download_file", _fake_download_file)

    mirrors = {
        "url_list": [
            "https://p3-sign.douyinpic.com/cover.jpg",
            "https://p9-sign.douyinpic.com/cover.jpg",
            "https://p6-sign.douyinpic.com/cover.jpg",
        ]
    }
    result = await downloader._download_first_available(
        mirrors,
        tmp_path / "cover.jpg",
        session=object(),
        optional=True,
    )

    assert result is False
    # 3 个镜像 × 1 次尝试；旧行为是 3 × (max_retries+1)=12 次。
    assert attempts == mirrors["url_list"]

    await api_client.close()


@pytest.mark.asyncio
async def test_first_available_keeps_backoff_for_single_url(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path, max_retries=2)

    attempts = []

    async def _fake_download_file(url, save_path, session, **_kwargs):
        attempts.append(url)
        return False

    monkeypatch.setattr(downloader.file_manager, "download_file", _fake_download_file)

    result = await downloader._download_first_available(
        {"url_list": ["https://p3-sign.douyinpic.com/only.jpg"]},
        tmp_path / "cover.jpg",
        session=object(),
        optional=True,
    )

    assert result is False
    # 单一 URL 没有镜像可替补，保留退避重试：max_retries+1 次尝试。
    assert len(attempts) == 3

    await api_client.close()


@pytest.mark.asyncio
async def test_first_available_stops_at_first_success(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path)

    attempts = []

    async def _fake_download_file(url, save_path, session, **_kwargs):
        attempts.append(url)
        return len(attempts) == 2  # 第一个镜像失败，第二个成功

    monkeypatch.setattr(downloader.file_manager, "download_file", _fake_download_file)

    result = await downloader._download_first_available(
        {"url_list": ["https://p3/a.jpg", "https://p9/a.jpg", "https://p6/a.jpg"]},
        tmp_path / "cover.jpg",
        session=object(),
        optional=True,
    )

    assert result is True
    assert len(attempts) == 2

    await api_client.close()


# ---------------------------------------------------------------------------
# 2. 本地索引单任务缓存、跨任务重扫
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_instance_rescans_after_disk_change(tmp_path):
    downloader_a, api_a = _build_downloader(tmp_path)
    aweme_id = "7346971177114611826"
    assert downloader_a._is_locally_downloaded(aweme_id) is False

    (tmp_path / f"2026-08-21_title_{aweme_id}.mp4").write_bytes(b"media")
    downloader_b, api_b = _build_downloader(tmp_path)

    # 当前 job 保持自己的快照；下一个 job 必须重扫并看到外部磁盘变化。
    assert downloader_a._is_locally_downloaded(aweme_id) is False
    assert downloader_b._is_locally_downloaded(aweme_id) is True

    await api_a.close()
    await api_b.close()


@pytest.mark.asyncio
async def test_local_index_scans_once_per_downloader_instance(tmp_path, monkeypatch):
    media = tmp_path / "author" / "post"
    media.mkdir(parents=True)
    (media / "2026-01-01_title_7346971177114611001.mp4").write_bytes(b"x")

    downloader_a, api_a = _build_downloader(tmp_path)
    downloader_b, api_b = _build_downloader(tmp_path)
    scan_count = 0
    path_type = type(tmp_path)
    original_rglob = path_type.rglob

    def _counting_rglob(path, pattern):
        nonlocal scan_count
        scan_count += 1
        return original_rglob(path, pattern)

    monkeypatch.setattr(path_type, "rglob", _counting_rglob)
    assert downloader_a._is_locally_downloaded("7346971177114611001") is True
    assert downloader_a._is_locally_downloaded("7346971177114611001") is True
    assert downloader_b._is_locally_downloaded("7346971177114611001") is True

    assert scan_count == 2

    await api_a.close()
    await api_b.close()


@pytest.mark.asyncio
async def test_local_index_not_shared_across_base_paths(tmp_path):
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    downloader_a, api_a = _build_downloader(root_a)
    downloader_b, api_b = _build_downloader(root_b)

    # 两个下载根目录始终拥有独立索引。
    assert downloader_a._is_locally_downloaded("7346971177114611002") is False
    downloader_a._mark_local_aweme_downloaded("7346971177114611002")
    assert downloader_a._is_locally_downloaded("7346971177114611002") is True
    assert downloader_b._is_locally_downloaded("7346971177114611002") is False

    await api_a.close()
    await api_b.close()


@pytest.mark.asyncio
async def test_mark_before_index_build_stays_within_current_job(tmp_path):
    """retry_executor 直接调 _download_aweme_assets（不经过 _should_download），
    实例 mark 时索引还未建。标记应更新当前 job，后续 job 仍以磁盘为准。"""
    downloader_a, api_a = _build_downloader(tmp_path)
    downloader_b, api_b = _build_downloader(tmp_path)

    assert downloader_a._local_aweme_ids is None
    downloader_a._mark_local_aweme_downloaded("7346971177114611005")

    assert downloader_a._is_locally_downloaded("7346971177114611005") is True
    assert downloader_b._is_locally_downloaded("7346971177114611005") is False

    await api_a.close()
    await api_b.close()


# ---------------------------------------------------------------------------
# 2b. 本地索引扫描不得阻塞事件循环
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_should_download_builds_index_off_event_loop(tmp_path):
    """首次建索引是同步 rglob + stat 重活；在大库/慢盘上会把事件循环
    冻住几十秒，桌面端看门狗（连续两次 /health 失联）会直接强杀后台
    服务、丢掉运行中的 job。判重必须把建索引放进工作线程。"""
    downloader, api = _build_downloader(tmp_path)
    scan_threads: list[threading.Thread] = []
    original_build = downloader._build_local_aweme_index

    def _tracking_build():
        scan_threads.append(threading.current_thread())
        original_build()

    downloader._build_local_aweme_index = _tracking_build

    assert await downloader._should_download("7346971177114611003") is True

    assert scan_threads, "dedupe check must build the local index"
    assert all(t is not threading.main_thread() for t in scan_threads)

    await api.close()


@pytest.mark.asyncio
async def test_download_assets_builds_index_off_event_loop(tmp_path):
    """retry_executor 直接调 _download_aweme_assets（绕过 _should_download），
    成功后的 _mark_local_aweme_downloaded 也会同步建索引——同样会冻住
    事件循环，入口处必须先在线程里把索引建好。"""
    downloader, api = _build_downloader(tmp_path)
    scan_threads: list[threading.Thread] = []
    original_build = downloader._build_local_aweme_index

    def _tracking_build():
        scan_threads.append(threading.current_thread())
        original_build()

    downloader._build_local_aweme_index = _tracking_build
    # 文件上下文返回 None，让流程在建索引后立即退出，不触发真实下载。
    downloader._build_aweme_file_context = lambda *args, **kwargs: None

    assert await downloader._download_aweme_assets({"aweme_id": "1"}, "作者") is False

    assert scan_threads, "asset path must pre-build the local index"
    assert all(t is not threading.main_thread() for t in scan_threads)

    await api.close()


@pytest.mark.asyncio
async def test_concurrent_dedupe_checks_scan_once(tmp_path):
    """download_batch 并发跑多个 _process_aweme；建索引挪进线程后出现
    真正的并发窗口，必须靠锁保证同一实例全库只扫一次。"""
    downloader, api = _build_downloader(tmp_path)
    scan_count = 0
    original_build = downloader._build_local_aweme_index

    def _counting_build():
        nonlocal scan_count
        scan_count += 1
        time.sleep(0.05)  # 放大并发窗口
        original_build()

    downloader._build_local_aweme_index = _counting_build

    await asyncio.gather(
        *(downloader._should_download(f"73469711771146110{i:02d}") for i in range(5))
    )

    assert scan_count == 1

    await api.close()


# ---------------------------------------------------------------------------
# 3. 可选资产并行且失败不影响主媒体
# ---------------------------------------------------------------------------


def _video_aweme(aweme_id: str) -> dict:
    return {
        "aweme_id": aweme_id,
        "desc": "标题",
        "create_time": 1750000000,
        "author": {
            "uid": "42",
            "nickname": "作者",
            "avatar_larger": {"url_list": ["https://p3/avatar.jpg"]},
        },
        "music": {"play_url": {"url_list": ["https://sf/music.mp3"]}},
        "video": {
            "cover": {"url_list": ["https://p3/cover.jpg"]},
            "play_addr": {
                "uri": "v0300",
                "url_list": ["https://v3-web.douyinvod.com/video.mp4"],
            },
        },
    }


@pytest.mark.asyncio
async def test_optional_assets_download_concurrently(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(cover=True, music=True, avatar=True, json=False)

    in_flight = 0
    peak = 0

    async def _fake_download_file(url, save_path, session, **_kwargs):
        nonlocal in_flight, peak
        if save_path.suffix == ".mp4" and "_live_" not in save_path.name:
            return True  # 主视频直接成功
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        return True

    async def _fake_get_session():
        return object()

    monkeypatch.setattr(downloader.file_manager, "download_file", _fake_download_file)
    monkeypatch.setattr(api_client, "get_session", _fake_get_session)

    ok = await downloader._download_aweme_assets(_video_aweme("7346971177114611003"), "作者")

    assert ok is True
    # cover/music/avatar 三个可选资产应同时在途，而不是串行。
    assert peak == 3

    await api_client.close()


@pytest.mark.asyncio
async def test_optional_asset_failure_keeps_video_success(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path, max_retries=0)
    downloader.config.update(cover=True, music=True, avatar=True, json=False)

    async def _fake_download_file(url, save_path, session, **_kwargs):
        if save_path.suffix == ".mp4" and "_live_" not in save_path.name:
            return True
        return False  # 所有可选资产失败

    async def _fake_get_session():
        return object()

    monkeypatch.setattr(downloader.file_manager, "download_file", _fake_download_file)
    monkeypatch.setattr(api_client, "get_session", _fake_get_session)

    ok = await downloader._download_aweme_assets(_video_aweme("7346971177114611004"), "作者")

    assert ok is True

    await api_client.close()
