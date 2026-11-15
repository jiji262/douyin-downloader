"""FFmpeg 连续采集、分段和有界停止。"""

import asyncio
import subprocess
import time
from urllib.parse import urlsplit

STOP_TIMEOUT = 4.0
REMUX_STALL_SECONDS = 60.0
REMUX_POLL_SECONDS = 1.0
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# 分段模板与列表都用相对名，进程以录制目录为 cwd：目录名里的 % 不会被 segment 当成模板。
SEGMENT_TEMPLATE = "part-%06d.ts"
SEGMENT_LIST = "segments.csv"


def clean_headers(headers):
    return {
        key: str(value).replace("\r", "").replace("\n", "")
        for key, value in headers.items()
        if key in ("User-Agent", "Referer", "Origin")
    }


def proxy_arguments(proxy):
    # FFmpeg 的 http 协议只会经 http:// 代理隧道；SOCKS 等其他代理传进去只会让连接失败。
    if proxy and urlsplit(proxy).scheme == "http":
        return ["-http_proxy", proxy]
    return []


def recording_command(binary, url, *, headers, interval, start, timeout, proxy=""):
    if urlsplit(url).scheme not in ("http", "https"):
        raise ValueError("直播流只支持 HTTP/HTTPS")
    safe_headers = clean_headers(headers)
    return [
        binary,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostats",
        "-y",
        "-rw_timeout",
        str(int(timeout * 1000000)),
        "-seekable",
        "0",
        "-protocol_whitelist",
        "http,https,httpproxy,tcp,tls,crypto",
        "-headers",
        "".join("%s: %s\r\n" % item for item in safe_headers.items()),
        *proxy_arguments(proxy),
        "-i",
        url,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c",
        "copy",
        "-f",
        "segment",
        "-segment_time",
        str(interval),
        "-reset_timestamps",
        "1",
        "-segment_start_number",
        str(start),
        "-segment_format",
        "mpegts",
        "-segment_format_options",
        "flush_packets=1",
        "-segment_list_type",
        "csv",
        "-segment_list",
        SEGMENT_LIST,
        "-progress",
        "pipe:1",
        SEGMENT_TEMPLATE,
    ]


async def wait_process(process, timeout):
    task = asyncio.create_task(process.wait())
    try:
        done, _ = await asyncio.wait({task}, timeout=timeout)
        if done:
            return task.result()
        return None
    finally:
        if not task.done():
            task.cancel()


async def kill_process(process):
    if process.returncode is not None:
        return
    try:
        process.kill()
    except ProcessLookupError:
        pass
    await process.wait()


async def _request_quit(process):
    try:
        process.stdin.write(b"q\n")
        await process.stdin.drain()
    except (OSError, AttributeError):
        pass


async def stop_process(process, *, timeout=STOP_TIMEOUT):
    # 收尾被取消（例如退出时 shutdown）也必须强杀并回收，不能留下继续写盘的 FFmpeg。
    try:
        if process.returncode is None:
            await _request_quit(process)
            await wait_process(process, timeout)
    finally:
        await kill_process(process)


def _file_size(path):
    try:
        return path.stat().st_size
    except OSError:
        return -1


async def _watch_remux(process, temporary):
    size, grown_at = -1, time.monotonic()
    while True:
        code = await wait_process(process, REMUX_POLL_SECONDS)
        if code is not None:
            return code
        current = _file_size(temporary)
        if current > size:
            size, grown_at = current, time.monotonic()
        elif time.monotonic() - grown_at >= REMUX_STALL_SECONDS:
            return None


async def remux(binary, source, target):
    # 大分段转封装耗时与体积成正比，只在产物长时间不增长时判定卡死；不加 faststart，避免二次整文件改写。
    temporary = target.with_suffix(".pending.mp4")
    process = await asyncio.create_subprocess_exec(
        binary,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c",
        "copy",
        str(temporary),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        creationflags=NO_WINDOW,
    )
    try:
        return await _watch_remux(process, temporary) == 0
    finally:
        await kill_process(process)
