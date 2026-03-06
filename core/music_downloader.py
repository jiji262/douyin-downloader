from __future__ import annotations

import json
import posixpath
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from core.downloader_base import BaseDownloader, DownloadResult
from utils.logger import setup_logger
from utils.validators import sanitize_filename

logger = setup_logger("MusicDownloader")


class MusicDownloader(BaseDownloader):
    async def download(self, parsed_url: Dict[str, Any]) -> DownloadResult:
        result = DownloadResult()

        music_id = parsed_url.get("music_id")
        if not music_id:
            logger.error("No music_id found in parsed URL")
            return result

        result.total = 1
        self._progress_set_item_total(1, "单音乐下载")
        self._progress_update_step("下载音乐", f"music_id={music_id}")

        detail = await self._get_music_detail(str(music_id))
        music_url = self._extract_music_url(detail)
        if music_url:
            success = await self._download_music_asset(str(music_id), detail, music_url)
            if success:
                result.success += 1
                self._progress_advance_item("success", str(music_id))
            else:
                result.failed += 1
                self._progress_advance_item("failed", str(music_id))
            return result

        # 回退：音乐详情无法直接拿到音频链接时，尝试下载该音乐下的首条作品
        aweme = await self._get_first_music_aweme(str(music_id))
        if aweme and aweme.get("aweme_id"):
            if not await self._should_download(str(aweme.get("aweme_id"))):
                result.skipped += 1
                self._progress_advance_item("skipped", str(aweme.get("aweme_id")))
                return result

            aweme_author = (aweme.get("author") or {}).get("nickname", "music")
            success = await self._download_aweme_assets(aweme, aweme_author, mode="music")
            if success:
                result.success += 1
                self._progress_advance_item("success", str(aweme.get("aweme_id")))
            else:
                result.failed += 1
                self._progress_advance_item("failed", str(aweme.get("aweme_id")))
            return result

        logger.error("No playable music source found for music_id=%s", music_id)
        result.failed += 1
        self._progress_advance_item("failed", str(music_id))
        return result

    async def _download_music_asset(
        self, music_id: str, detail: Optional[Dict[str, Any]], music_url: str
    ) -> bool:
        session = await self.api_client.get_session()
        detail = detail or {}

        title = (
            detail.get("title")
            or detail.get("music_name")
            or (detail.get("music") or {}).get("title")
            or f"music_{music_id}"
        )
        author_name = (
            detail.get("author_name")
            or (detail.get("owner") or {}).get("nickname")
            or "music"
        )
        publish_date = datetime.now().strftime("%Y-%m-%d")
        record_id = f"music_{music_id}"
        file_stem = sanitize_filename(f"{publish_date}_{title}_{record_id}")

        save_dir = self.file_manager.get_save_path(
            author_name=author_name,
            mode="music",
            aweme_title=title,
            aweme_id=record_id,
            folderstyle=self.config.get("folderstyle", True),
            download_date=publish_date,
        )

        music_ext = self._infer_audio_extension(music_url)
        music_path = save_dir / f"{file_stem}{music_ext}"
        if self.file_manager.file_exists(music_path):
            logger.info("Music already exists locally: %s", music_path.name)
            return True

        success = await self._download_with_retry(
            music_url,
            music_path,
            session,
            headers=self._download_headers(),
        )
        if not success:
            return False

        cover_url = self._extract_first_url(
            detail.get("cover_large")
            or detail.get("cover_thumb")
            or (detail.get("music") or {}).get("cover_large")
        )
        if cover_url and self.config.get("cover"):
            cover_path = save_dir / f"{file_stem}_cover.jpg"
            await self._download_with_retry(
                cover_url,
                cover_path,
                session,
                headers=self._download_headers(),
                optional=True,
            )

        if self.config.get("json"):
            await self.metadata_handler.save_metadata(
                detail or {"music_id": music_id}, save_dir / f"{file_stem}_data.json"
            )

        if self.database:
            await self.database.add_aweme(
                {
                    "aweme_id": record_id,
                    "aweme_type": "music",
                    "title": title,
                    "author_id": None,
                    "author_name": author_name,
                    "create_time": None,
                    "file_path": str(save_dir),
                    "metadata": json.dumps(detail or {}, ensure_ascii=False),
                }
            )

        await self.metadata_handler.append_download_manifest(
            self.file_manager.base_path,
            {
                "date": publish_date,
                "aweme_id": record_id,
                "author_name": author_name,
                "desc": title,
                "media_type": "music",
                "file_names": [music_path.name],
                "file_paths": [self._to_manifest_path(music_path)],
            },
        )
        return True

    async def _get_music_detail(self, music_id: str) -> Optional[Dict[str, Any]]:
        getter = getattr(self.api_client, "get_music_detail", None)
        if not callable(getter):
            return None
        try:
            return await getter(music_id)
        except Exception as exc:
            logger.warning("Get music detail failed: %s", exc)
            return None

    async def _get_first_music_aweme(self, music_id: str) -> Optional[Dict[str, Any]]:
        getter = getattr(self.api_client, "get_music_aweme", None)
        if not callable(getter):
            return None
        try:
            data = await getter(music_id, cursor=0, count=1)
        except Exception as exc:
            logger.warning("Get music aweme failed: %s", exc)
            return None

        if not isinstance(data, dict):
            return None
        items = data.get("items")
        if not isinstance(items, list):
            items = data.get("aweme_list")
        if not isinstance(items, list) or not items:
            return None
        first_item = items[0]
        if isinstance(first_item, dict) and first_item.get("aweme_id"):
            return first_item
        nested_aweme = first_item.get("aweme") if isinstance(first_item, dict) else None
        if isinstance(nested_aweme, dict) and nested_aweme.get("aweme_id"):
            return nested_aweme
        return None

    def _extract_music_url(self, detail: Optional[Dict[str, Any]]) -> Optional[str]:
        if not isinstance(detail, dict):
            return None

        candidates = (
            detail.get("play_url"),
            detail.get("play_url_lowbr"),
            detail.get("audio_url"),
            (detail.get("music") or {}).get("play_url"),
            (detail.get("music") or {}).get("play_url_lowbr"),
            (detail.get("music_info") or {}).get("play_url"),
        )

        for candidate in candidates:
            url = self._extract_first_url(candidate)
            if url:
                return url
        return None

    @staticmethod
    def _infer_audio_extension(music_url: str) -> str:
        if not music_url:
            return ".mp3"

        raw_path = urlparse(music_url).path or ""
        ext = posixpath.splitext(raw_path)[1].lower()
        allowed_exts = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus"}
        if ext in allowed_exts:
            return ext
        return ".mp3"
