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

    # 作者目录层可选风格（与 DEFAULT_CONFIG["author_dir"]、REST SettingsPatch
    # 的 Literal、前端下拉三处保持一致）。
    _AUTHOR_DIR_STYLES = ("nickname", "sec_uid", "nickname_uid")

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
        folder_name: Optional[str] = None,
        *,
        author_sec_uid: Optional[str] = None,
        author_dir_style: str = "nickname",
    ) -> Path:
        """Compute (and create) the destination directory for a download.

        ``folder_name`` is the pre-rendered, already-sanitized leaf directory
        name produced by ``utils.naming.render_template``. When provided, it
        overrides the legacy ``{date}_{title}_{id}`` layout. When omitted we
        fall back to the historical composition so external callers and the
        sibling CLI project keep working unchanged.

        ``author_dir_style`` controls how the author-level directory is
        composed (see :data:`_AUTHOR_DIR_STYLES`). Unknown values or missing
        ``author_sec_uid`` fall back to ``nickname`` with a ``WARNING`` so
        downloads never fail on a misconfiguration.
        """
        safe_author = self._compose_author_dir(
            author_name, author_sec_uid, author_dir_style
        )

        if mode:
            save_dir = self.base_path / safe_author / mode
        else:
            save_dir = self.base_path / safe_author

        if folderstyle:
            leaf = folder_name
            if leaf is None and aweme_title and aweme_id:
                safe_title = sanitize_filename(aweme_title)
                date_prefix = f"{download_date}_" if download_date else ""
                leaf = f"{date_prefix}{safe_title}_{aweme_id}"
            if leaf:
                save_dir = save_dir / leaf

        save_dir.mkdir(parents=True, exist_ok=True)
        return save_dir

    @classmethod
    def _compose_author_dir(
        cls,
        author_name: str,
        author_sec_uid: Optional[str],
        style: str,
    ) -> str:
        """Build the sanitized author-level directory name per ``style``.

        Behaviour matrix (kept in lock-step with the ``author_dir`` option
        surfaced in settings UI and ``DEFAULT_CONFIG``):

        - ``nickname``    → ``sanitize_filename(author_name)`` (legacy)
        - ``sec_uid``     → ``sanitize_filename(author_sec_uid)``;
          empty/None → fall back to nickname + ``logger.warning``.
        - ``nickname_uid`` → ``sanitize_filename(f"{author_name}_{author_sec_uid}")``;
          sec_uid missing → fall back to nickname + ``logger.warning``.
        - Unknown style   → fall back to nickname + ``logger.warning``.

        Never raises — a misconfiguration must degrade into a still-working
        download, not a hard failure.
        """
        nickname_dir = sanitize_filename(author_name)
        sec_uid = (author_sec_uid or "").strip()

        if style not in cls._AUTHOR_DIR_STYLES:
            logger.warning(
                "Unknown author_dir style %r, falling back to nickname (%s)",
                style,
                nickname_dir,
            )
            return nickname_dir

        if style == "nickname":
            return nickname_dir

        if style == "sec_uid":
            if not sec_uid:
                logger.warning(
                    "author_dir=sec_uid but sec_uid is missing for %r, "
                    "falling back to nickname",
                    author_name,
                )
                return nickname_dir
            return sanitize_filename(sec_uid)

        # style == "nickname_uid"
        if not sec_uid:
            logger.warning(
                "author_dir=nickname_uid but sec_uid is missing for %r, "
                "falling back to nickname",
                author_name,
            )
            return nickname_dir
        return sanitize_filename(f"{author_name}_{sec_uid}")

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
