import aiofiles
import aiohttp
from pathlib import Path
from typing import Dict, Optional
from utils.validators import sanitize_filename
from utils.logger import setup_logger

logger = setup_logger('FileManager')


class FileManager:
    def __init__(self, base_path: str = './Downloaded'):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def get_save_path(self, author_name: str, mode: str = None, aweme_title: str = None,
                     aweme_id: str = None, folderstyle: bool = True) -> Path:
        safe_author = sanitize_filename(author_name)

        if mode:
            save_dir = self.base_path / safe_author / mode
        else:
            save_dir = self.base_path / safe_author

        if folderstyle and aweme_title and aweme_id:
            safe_title = sanitize_filename(aweme_title)
            save_dir = save_dir / f"{safe_title}_{aweme_id}"

        save_dir.mkdir(parents=True, exist_ok=True)
        return save_dir

    async def download_file(
        self,
        url: str,
        save_path: Path,
        session: aiohttp.ClientSession = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> bool:
        should_close = False
        if session is None:
            default_headers = headers or {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                               'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
                'Referer': 'https://www.douyin.com/',
                'Accept': '*/*',
            }
            session = aiohttp.ClientSession(headers=default_headers)
            should_close = True

        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=300),
                headers=headers,
            ) as response:
                if response.status == 200:
                    async with aiofiles.open(save_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(8192):
                            await f.write(chunk)
                    return True
                else:
                    logger.error(f"Download failed: {url}, status: {response.status}")
                    return False
        except Exception as e:
            logger.error(f"Download error: {url}, error: {e}")
            return False
        finally:
            if should_close:
                await session.close()

    def file_exists(self, file_path: Path) -> bool:
        return file_path.exists() and file_path.stat().st_size > 0

    def get_file_size(self, file_path: Path) -> int:
        return file_path.stat().st_size if self.file_exists(file_path) else 0
