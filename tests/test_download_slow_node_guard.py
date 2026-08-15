"""慢节点保护 + 下载中进度事件 + 单条作品总时限。

背景（线上日志 2026-08-12 job f25a60b4850f）：主页任务 35 条里最后一条从
09:08:12 一直沉默到 09:13:12 才失败切换，正好 300s —— 首选候选
``v95-bjb-mc-cold.douyinvod.com``（冷存储节点）握手成功后以每秒几 KB
滴水式吐字节，既不触发 ``sock_read=60`` 停滞检测（一直有数据），也只能
等满 ``total=300s``。期间下载器不发任何进度事件，UI 停在 97% 五分钟，
用户判定为卡死（同一日志里前面连续取消了 4 个任务）。换下一个候选后
2 秒就下完了。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from storage.file_manager import (
    _DOWNLOAD_MIN_SPEED_BPS,
    FileManager,
    SlowDownloadError,
    _ThroughputGuard,
)


class _FakeClock:
    """手动推进的单调时钟，避免用真实 sleep 拖慢测试。"""

    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# --------------------------------------------------------------------------
# _ThroughputGuard 单元测试
# --------------------------------------------------------------------------


def test_guard_trips_when_throughput_stays_below_floor():
    clock = _FakeClock()
    guard = _ThroughputGuard(min_bps=20 * 1024, window_s=30, clock=clock)

    # 30 秒只来了 64KB ≈ 2.1 KB/s，远低于 20 KB/s 地板。
    clock.advance(30)
    with pytest.raises(SlowDownloadError):
        guard.feed(64 * 1024)


def test_guard_allows_healthy_throughput():
    clock = _FakeClock()
    guard = _ThroughputGuard(min_bps=20 * 1024, window_s=30, clock=clock)

    # 30 秒 3MB ≈ 100 KB/s，窗口结束后重置继续。
    clock.advance(30)
    guard.feed(3 * 1024 * 1024)
    clock.advance(30)
    guard.feed(3 * 1024 * 1024)


def test_guard_stays_quiet_inside_the_window():
    """窗口未满不判定——起步慢（TLS/302）不该被误杀。"""
    clock = _FakeClock()
    guard = _ThroughputGuard(min_bps=20 * 1024, window_s=30, clock=clock)

    clock.advance(29.9)
    guard.feed(1)


# --------------------------------------------------------------------------
# download_file 集成
# --------------------------------------------------------------------------


def _slow_session(clock, *, chunk_bytes: int, chunk_interval_s: float, chunks: int, total: int):
    """构造一个"每 chunk_interval_s 吐 chunk_bytes"的滴水式响应。"""
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.content_length = total
    mock_response.headers = {"Content-Length": str(total)}

    async def iter_chunked(_size):
        for _ in range(chunks):
            clock.advance(chunk_interval_s)
            yield b"\0" * chunk_bytes

    mock_response.content = MagicMock()
    mock_response.content.iter_chunked = iter_chunked

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get.return_value = ctx
    return session


async def test_download_file_abandons_slow_node_instead_of_waiting_out_total_timeout(
    tmp_path, monkeypatch
):
    """滴水式节点应在一个判定窗口内放弃，而不是耗满 total=300s。"""
    clock = _FakeClock()
    monkeypatch.setattr("storage.file_manager._monotonic", clock)
    fm = FileManager(str(tmp_path))
    save_path = tmp_path / "video.mp4"
    # 每 10s 来 8KB = 0.8 KB/s，声明总大小 10MB —— 永远下不完。
    session = _slow_session(
        clock, chunk_bytes=8 * 1024, chunk_interval_s=10, chunks=100, total=10 * 1024 * 1024
    )

    result = await fm.download_file("https://cold.douyinvod.com/v.mp4", save_path, session=session)

    assert result is False
    assert not save_path.exists()
    assert not save_path.with_suffix(".mp4.tmp").exists()
    # 关键：远早于 300s 就放弃了。
    assert clock.now < 120


async def test_download_file_keeps_healthy_stream(tmp_path, monkeypatch):
    """健康速率不受影响（回归护栏：别把正常下载误杀）。"""
    clock = _FakeClock()
    monkeypatch.setattr("storage.file_manager._monotonic", clock)
    fm = FileManager(str(tmp_path))
    save_path = tmp_path / "video.mp4"
    chunk = 1024 * 1024
    # 每秒 1MB，共 8MB。
    session = _slow_session(clock, chunk_bytes=chunk, chunk_interval_s=1, chunks=8, total=8 * chunk)

    result = await fm.download_file("https://v3.douyinvod.com/v.mp4", save_path, session=session)

    assert result is True
    assert save_path.stat().st_size == 8 * chunk


async def test_slow_but_complete_small_file_is_not_abandoned(tmp_path, monkeypatch):
    """已经收齐声明字节数的慢文件不该在最后一块上被误判。

    小封面/小音频可能一整块就下完，但整体耗时越过判定窗口——此时数据
    已经拿全，判定"追不上"没有意义。
    """
    clock = _FakeClock()
    monkeypatch.setattr("storage.file_manager._monotonic", clock)
    fm = FileManager(str(tmp_path))
    save_path = tmp_path / "cover.jpg"
    session = _slow_session(clock, chunk_bytes=4096, chunk_interval_s=60, chunks=1, total=4096)

    result = await fm.download_file("https://p3.douyinpic.com/c.jpg", save_path, session=session)

    assert result is True
    assert save_path.read_bytes() == b"\0" * 4096


async def test_cancelled_download_leaves_no_tmp_file(tmp_path, monkeypatch):
    """取消 / 超时中断后不该在下载目录里留下半截 .tmp。

    CancelledError 是 BaseException，只捕 Exception 会漏掉最常见的取消路径。
    """
    clock = _FakeClock()
    monkeypatch.setattr("storage.file_manager._monotonic", clock)
    fm = FileManager(str(tmp_path))
    save_path = tmp_path / "video.mp4"

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.content_length = 10 * 1024 * 1024
    mock_response.headers = {}

    async def iter_chunked(_size):
        yield b"\0" * 4096
        raise asyncio.CancelledError()

    mock_response.content = MagicMock()
    mock_response.content.iter_chunked = iter_chunked
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get.return_value = ctx

    with pytest.raises(asyncio.CancelledError):
        await fm.download_file("https://v3.douyinvod.com/v.mp4", save_path, session=session)

    assert not save_path.exists()
    assert not save_path.with_suffix(".mp4.tmp").exists()


def test_slow_speed_floor_is_below_any_usable_link():
    """地板必须低到不会误伤真实用户：20 KB/s 下 6MB 视频要 300s+，
    本来就跨不过既有的 total 超时。"""
    assert _DOWNLOAD_MIN_SPEED_BPS <= 32 * 1024


# --------------------------------------------------------------------------
# 下载中进度回调
# --------------------------------------------------------------------------


async def test_download_file_emits_throttled_progress(tmp_path, monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr("storage.file_manager._monotonic", clock)
    fm = FileManager(str(tmp_path))
    chunk = 512 * 1024
    session = _slow_session(clock, chunk_bytes=chunk, chunk_interval_s=1, chunks=6, total=6 * chunk)
    seen = []

    result = await fm.download_file(
        "https://v3.douyinvod.com/v.mp4",
        tmp_path / "v.mp4",
        session=session,
        on_progress=lambda read, total: seen.append((read, total)),
    )

    assert result is True
    # 节流：6 秒的下载不该产生 6 条以上事件，但至少要有首尾两条。
    assert 2 <= len(seen) <= 5
    assert seen[-1] == (6 * chunk, 6 * chunk)
    # 单调递增
    assert [r for r, _ in seen] == sorted(r for r, _ in seen)


async def test_fast_download_stays_silent(tmp_path, monkeypatch):
    """秒下的小文件不该占掉事件流那 250 条的回放窗口——它从来就不"卡"。"""
    clock = _FakeClock()
    monkeypatch.setattr("storage.file_manager._monotonic", clock)
    fm = FileManager(str(tmp_path))
    session = _slow_session(
        clock, chunk_bytes=64 * 1024, chunk_interval_s=0.05, chunks=4, total=4 * 64 * 1024
    )
    seen = []

    result = await fm.download_file(
        "https://v3.douyinvod.com/v.mp4",
        tmp_path / "v.mp4",
        session=session,
        on_progress=lambda read, total: seen.append((read, total)),
    )

    assert result is True
    assert seen == []


async def test_download_file_survives_broken_progress_callback(tmp_path, monkeypatch):
    """reporter 抛错是尽力而为的旁路，不能把下载本身拖失败。"""
    clock = _FakeClock()
    monkeypatch.setattr("storage.file_manager._monotonic", clock)
    fm = FileManager(str(tmp_path))
    chunk = 256 * 1024
    session = _slow_session(clock, chunk_bytes=chunk, chunk_interval_s=1, chunks=4, total=4 * chunk)

    def _boom(_read, _total):
        raise RuntimeError("reporter died")

    result = await fm.download_file(
        "https://v3.douyinvod.com/v.mp4",
        tmp_path / "v.mp4",
        session=session,
        on_progress=_boom,
    )

    assert result is True
