"""抖音直播录制。

技术路径：
- 通过 `/webcast/room/web/enter/` 获取 stream_url，常见字段：
    * flv_pull_url: {SD, HD, FULL_HD, ORIGIN}
    * hls_pull_url_map: {HD1, HD2, HD3}
- 选择最高清可用的流，优先 FLV（单文件落盘简单）
- 使用 aiohttp 分块写入到 `.flv` 临时文件，完成后原子重命名
- 时长限制：read_timeout 自然结束或 max_duration_seconds 触发
- 不依赖 ffmpeg；若用户需要转码可后处理

限制：
- 不处理多人房间 / 连麦切换
- 不采集弹幕（后续可扩展）
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiofiles
import aiohttp

from core.downloader_base import BaseDownloader, DownloadResult
from utils.logger import setup_logger
from utils.validators import sanitize_filename

logger = setup_logger("LiveDownloader")


# 质量优先级：数字越大越高清
_FLV_QUALITY_ORDER = {
    "ORIGIN": 100,
    "FULL_HD1": 90,
    "FULL_HD": 90,
    "HD1": 70,
    "HD": 70,
    "SD1": 50,
    "SD2": 50,
    "SD": 50,
    "LD": 30,
}


class LiveDownloader(BaseDownloader):
    async def download(self, parsed_url: Dict[str, Any]) -> DownloadResult:
        result = DownloadResult()

        room_id = parsed_url.get("room_id")
        if not room_id:
            logger.error("No room_id found in parsed URL")
            return result

        result.total = 1
        self._progress_set_item_total(1, "直播录制")
        self._progress_update_step("获取直播间信息", f"room_id={room_id}")

        info = await self.api_client.get_live_room_info(str(room_id))
        if not info:
            logger.error("Live room not available or fetch failed: %s", room_id)
            result.failed += 1
            self._progress_advance_item("failed", str(room_id))
            return result

        room = info.get("room") or {}
        user = info.get("user") or {}

        status = room.get("status")
        if status is not None and int(status or 0) != 2:
            # 2 = 正在直播；其他状态不录
            logger.warning(
                "Room %s not live (status=%s); skipping", room_id, status
            )
            result.skipped += 1
            self._progress_advance_item("skipped", str(room_id))
            return result

        stream_url, quality = self._select_best_stream_url(room)
        if not stream_url:
            logger.error("No playable live stream URL for room %s", room_id)
            result.failed += 1
            self._progress_advance_item("failed", str(room_id))
            return result

        author_name = (user.get("nickname") or "unknown").strip() or "unknown"
        title = (room.get("title") or "直播").strip() or "直播"
        save_dir, file_stem = self._plan_output_paths(author_name, title, str(room_id))

        # 保存元数据
        meta_path = save_dir / f"{file_stem}_room.json"
        try:
            async with aiofiles.open(meta_path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(info, ensure_ascii=False, indent=2))
        except Exception as exc:
            logger.debug("Save room meta failed: %s", exc)

        is_hls = ".m3u8" in stream_url.split("?")[0]
        suffix = ".flv" if not is_hls else ".m3u8"
        target_path = save_dir / f"{file_stem}{suffix}"
        if is_hls:
            # HLS 源只会下载 playlist（m3u8 文本），不是可直接播放的视频文件。
            # 告知用户正确的后处理方式。
            logger.warning(
                "选中的直播源为 HLS（m3u8 playlist），保存的将是播放列表文本而非视频。"
                "如需可播放文件，请用 ffmpeg 基于该 URL 抓流：ffmpeg -i '%s' -c copy out.ts",
                stream_url,
            )

        live_cfg = self._live_config()
        max_duration = float(live_cfg.get("max_duration_seconds") or 0)
        chunk_size = int(live_cfg.get("chunk_size") or 65536)
        idle_timeout = float(live_cfg.get("idle_timeout_seconds") or 30.0)

        self._progress_update_step(
            "录制直播流",
            f"quality={quality} | -> {target_path.name}",
        )

        ok = await self._record_stream(
            stream_url,
            target_path,
            max_duration=max_duration,
            chunk_size=chunk_size,
            idle_timeout=idle_timeout,
        )

        if ok:
            result.success += 1
            self._progress_advance_item("success", str(room_id))
            logger.info("Live recording finished: %s", target_path)
        else:
            result.failed += 1
            self._progress_advance_item("failed", str(room_id))

        return result

    # --- helpers ---

    def _live_config(self) -> Dict[str, Any]:
        cfg = self.config.get("live") or {}
        return cfg if isinstance(cfg, dict) else {}

    def _plan_output_paths(
        self, author_name: str, title: str, room_id: str
    ) -> Tuple[Path, str]:
        safe_title = sanitize_filename(title)
        date = datetime.now().strftime("%Y-%m-%d_%H%M")
        file_stem = sanitize_filename(f"{date}_{safe_title}_{room_id}")
        save_dir = self.file_manager.get_save_path(
            author_name=author_name,
            mode="live",
            aweme_title=title,
            aweme_id=room_id,
            folderstyle=self.config.get("folderstyle", True),
            download_date=date,
        )
        return save_dir, file_stem

    @staticmethod
    def _select_best_stream_url(room: Dict[str, Any]) -> Tuple[Optional[str], str]:
        """从 room.stream_url 中挑一条最佳地址。优先 FLV 高清。"""
        stream = room.get("stream_url") if isinstance(room, dict) else None
        if not isinstance(stream, dict):
            return None, ""

        # FLV 优先
        flv_map = stream.get("flv_pull_url")
        if isinstance(flv_map, dict) and flv_map:
            best_key = max(
                flv_map.keys(),
                key=lambda k: _FLV_QUALITY_ORDER.get(k.upper(), 0),
            )
            url = flv_map.get(best_key)
            if isinstance(url, str) and url:
                return url, best_key

        # 其次 HLS
        hls_map = stream.get("hls_pull_url_map")
        if isinstance(hls_map, dict) and hls_map:
            best_key = max(
                hls_map.keys(),
                key=lambda k: _FLV_QUALITY_ORDER.get(k.upper(), 0),
            )
            url = hls_map.get(best_key)
            if isinstance(url, str) and url:
                return url, best_key

        # 兜底：直接取根字段
        for key in ("flv_pull_url", "hls_pull_url", "rtmp_pull_url"):
            url = stream.get(key)
            if isinstance(url, str) and url:
                return url, key

        return None, ""

    async def _record_stream(
        self,
        url: str,
        target_path: Path,
        *,
        max_duration: float,
        chunk_size: int,
        idle_timeout: float,
    ) -> bool:
        """从 url 拉取字节流写入 target_path，直到流结束 / 超时 / 达到 max_duration。

        **数据保留策略**：主播下播、网络空闲、payload 截断等场景下，只要已经写入
        > 0 字节，就把 .tmp 提升为正式文件（录到一半的直播也比零字节有用）。
        仅 HTTP 4xx / 从未开始写入的情况下才会丢弃。
        """
        target_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = target_path.with_suffix(target_path.suffix + ".tmp")
        start = time.monotonic()
        bytes_written = 0
        last_chunk_ts = start

        # 直播 CDN 常同时校验 Referer 与 Origin 为 live.douyin.com（不是 www.douyin.com）。
        headers = self._download_headers()
        headers["Referer"] = "https://live.douyin.com/"
        headers["Origin"] = "https://live.douyin.com"

        def _promote_if_nonempty(reason: str) -> bool:
            if bytes_written <= 0:
                # 零字节也尝试清理 .tmp
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass
                return False
            try:
                os.replace(str(tmp_path), str(target_path))
            except Exception as exc:
                # 捕获所有异常：理论上只会是 OSError，但 rename 失败时宁可多兜底也别泄漏。
                logger.error("Live tmp → final rename failed: %s", exc)
                return False
            logger.info(
                "Live stream recorded (%s): %s (%.1fs, %.1f MiB)",
                reason,
                target_path.name,
                last_chunk_ts - start,
                bytes_written / (1024 * 1024),
            )
            return True

        session = await self.api_client.get_session()
        try:
            async with session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=None, sock_read=idle_timeout),
            ) as resp:
                if resp.status != 200:
                    logger.error(
                        "Live stream HTTP %s for %s", resp.status, target_path.name
                    )
                    return False
                async with aiofiles.open(tmp_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(chunk_size):
                        if not chunk:
                            continue
                        await f.write(chunk)
                        bytes_written += len(chunk)
                        now = time.monotonic()
                        last_chunk_ts = now
                        if max_duration and (now - start) >= max_duration:
                            logger.info(
                                "Live max_duration reached (%.1fs), stopping.",
                                max_duration,
                            )
                            break
            return _promote_if_nonempty("stream ended")
        except asyncio.CancelledError:
            # 外部取消（Ctrl+C 等）：保留已录制内容
            _promote_if_nonempty("cancelled")
            raise
        except aiohttp.ClientPayloadError as exc:
            # 直播中断（主播下播）常见表现，视为正常结束
            logger.info("Live payload ended: %s", exc)
            return _promote_if_nonempty("payload ended")
        except (asyncio.TimeoutError, aiohttp.ServerTimeoutError) as exc:
            # sock_read 空闲超时——多数情况是主播停止推流，保留已录数据
            logger.info(
                "Live stream idle timeout after %ss: %s", idle_timeout, exc
            )
            return _promote_if_nonempty("idle timeout")
        except Exception as exc:
            logger.error("Live stream recording failed: %s", exc)
            # 其它未知异常也尽量保留已写入的数据
            return _promote_if_nonempty("unexpected error")
