import os
from pathlib import Path
from typing import Dict, Optional, Union

import aiofiles
import aiohttp
from utils.logger import setup_logger
from utils.validators import sanitize_filename

logger = setup_logger("FileManager")


class FileManager:
    _IMAGE_CONTENT_TYPE_SUFFIXES = {
        "image/gif": ".gif",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }

    def __init__(self, base_path: str = "./Downloaded"):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def get_save_path(
        self,
        author_name: str,
        mode: str = None,
        aweme_title: str = None,
        aweme_id: str = None,
        folderstyle: bool = True,
        download_date: str = "",
    ) -> Path:
        safe_author = sanitize_filename(author_name)

        if mode:
            save_dir = self.base_path / safe_author / mode
        else:
            save_dir = self.base_path / safe_author

        if folderstyle and aweme_title and aweme_id:
            safe_title = sanitize_filename(aweme_title)
            date_prefix = f"{download_date}_" if download_date else ""
            save_dir = save_dir / f"{date_prefix}{safe_title}_{aweme_id}"

        save_dir.mkdir(parents=True, exist_ok=True)
        return save_dir

    async def download_file(
        self,
        url: str,
        save_path: Path,
        session: aiohttp.ClientSession = None,
        headers: Optional[Dict[str, str]] = None,
        proxy: Optional[str] = None,
        *,
        prefer_response_content_type: bool = False,
        return_saved_path: bool = False,
    ) -> Union[bool, Path]:
        should_close = False
        if session is None:
            default_headers = headers or {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Referer": "https://www.douyin.com/",
                "Accept": "*/*",
            }
            session = aiohttp.ClientSession(headers=default_headers)
            should_close = True

        final_path = save_path
        tmp_path = save_path.with_suffix(save_path.suffix + ".tmp")
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=300),
                headers=headers,
                proxy=proxy or None,
            ) as response:
                if response.status == 200:
                    final_path = self._resolve_save_path_from_content_type(
                        save_path,
                        response.headers,
                        prefer_response_content_type=prefer_response_content_type,
                    )
                    tmp_path = final_path.with_suffix(final_path.suffix + ".tmp")
                    expected_size = response.content_length
                    written = 0
                    async with aiofiles.open(tmp_path, "wb") as f:
                        async for chunk in response.content.iter_chunked(8192):
                            await f.write(chunk)
                            written += len(chunk)
                    if expected_size is not None and written != expected_size:
                        logger.warning(
                            "Size mismatch for %s: expected %d, got %d",
                            save_path.name,
                            expected_size,
                            written,
                        )
                        tmp_path.unlink(missing_ok=True)
                        return False
                    os.replace(str(tmp_path), str(final_path))
                    return final_path if return_saved_path else True
                else:
                    logger.debug(
                        "Download failed for %s, status=%s",
                        final_path.name,
                        response.status,
                    )
                    return False
        except Exception as e:
            logger.debug("Download error for %s: %s", final_path.name, e)
            tmp_path.unlink(missing_ok=True)
            return False
        finally:
            if should_close:
                await session.close()

    @classmethod
    def _resolve_save_path_from_content_type(
        cls,
        save_path: Path,
        response_headers,
        *,
        prefer_response_content_type: bool = False,
    ) -> Path:
        if not prefer_response_content_type:
            return save_path

        content_type = (
            response_headers.get("Content-Type", "")
            if response_headers
            else ""
        )
        normalized_type = content_type.split(";", 1)[0].strip().lower()
        suffix = cls._IMAGE_CONTENT_TYPE_SUFFIXES.get(normalized_type)
        if not suffix:
            return save_path
        return save_path.with_suffix(suffix)

    def file_exists(self, file_path: Path) -> bool:
        try:
            return file_path.exists() and file_path.stat().st_size > 0
        except OSError:
            return False

    def get_file_size(self, file_path: Path) -> int:
        try:
            return file_path.stat().st_size if self.file_exists(file_path) else 0
        except OSError:
            return 0
