import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from config import ConfigLoader
from storage import Database, FileManager, MetadataHandler
from auth import CookieManager
from control import QueueManager, RateLimiter, RetryHandler
from core.api_client import DouyinAPIClient
from utils.logger import setup_logger
from utils.validators import sanitize_filename

logger = setup_logger('BaseDownloader')


class DownloadResult:
    def __init__(self):
        self.total = 0
        self.success = 0
        self.failed = 0
        self.skipped = 0

    def __str__(self):
        return f"Total: {self.total}, Success: {self.success}, Failed: {self.failed}, Skipped: {self.skipped}"


class BaseDownloader(ABC):
    def __init__(
        self,
        config: ConfigLoader,
        api_client: DouyinAPIClient,
        file_manager: FileManager,
        cookie_manager: CookieManager,
        database: Optional[Database] = None,
        rate_limiter: Optional[RateLimiter] = None,
        retry_handler: Optional[RetryHandler] = None,
        queue_manager: Optional[QueueManager] = None,
    ):
        self.config = config
        self.api_client = api_client
        self.file_manager = file_manager
        self.cookie_manager = cookie_manager
        self.database = database
        self.rate_limiter = rate_limiter or RateLimiter()
        self.retry_handler = retry_handler or RetryHandler()
        thread_count = int(self.config.get('thread', 5) or 5)
        self.queue_manager = queue_manager or QueueManager(max_workers=thread_count)
        self.metadata_handler = MetadataHandler()

    def _download_headers(self, user_agent: Optional[str] = None) -> Dict[str, str]:
        headers = {
            'Referer': f'{self.api_client.BASE_URL}/',
            'Origin': self.api_client.BASE_URL,
            'Accept': '*/*',
        }

        headers['User-Agent'] = user_agent or self.api_client.headers.get('User-Agent', '')
        return headers

    @abstractmethod
    async def download(self, parsed_url: Dict[str, Any]) -> DownloadResult:
        pass

    async def _should_download(self, aweme_id: str) -> bool:
        if self.database:
            return not await self.database.is_downloaded(aweme_id)
        return True

    def _filter_by_time(self, aweme_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        start_time = self.config.get('start_time')
        end_time = self.config.get('end_time')

        if not start_time and not end_time:
            return aweme_list

        filtered: List[Dict[str, Any]] = []
        for aweme in aweme_list:
            create_time = aweme.get('create_time', 0)

            if start_time:
                from datetime import datetime
                start_ts = int(datetime.strptime(start_time, '%Y-%m-%d').timestamp())
                if create_time < start_ts:
                    continue

            if end_time:
                from datetime import datetime
                end_ts = int(datetime.strptime(end_time, '%Y-%m-%d').timestamp())
                if create_time > end_ts:
                    continue

            filtered.append(aweme)

        return filtered

    def _limit_count(self, aweme_list: List[Dict[str, Any]], mode: str) -> List[Dict[str, Any]]:
        number_config = self.config.get('number', {})
        limit = number_config.get(mode, 0)

        if limit > 0:
            return aweme_list[:limit]
        return aweme_list

    async def _download_aweme_assets(
        self,
        aweme_data: Dict[str, Any],
        author_name: str,
        mode: Optional[str] = None,
    ) -> bool:
        aweme_id = aweme_data.get('aweme_id')
        if not aweme_id:
            logger.error('Missing aweme_id in aweme data')
            return False

        desc = aweme_data.get('desc', 'no_title')
        safe_title = sanitize_filename(desc)

        save_dir = self.file_manager.get_save_path(
            author_name=author_name,
            mode=mode,
            aweme_title=desc,
            aweme_id=aweme_id,
            folderstyle=self.config.get('folderstyle', True)
        )

        session = await self.api_client.get_session()

        media_type = self._detect_media_type(aweme_data)
        if media_type == 'video':
            video_info = self._build_no_watermark_url(aweme_data)
            if not video_info:
                logger.error(f'No playable video URL found for aweme {aweme_id}')
                return False

            video_url, video_headers = video_info
            video_path = save_dir / f"{safe_title}_{aweme_id}.mp4"
            if not await self._download_with_retry(video_url, video_path, session, headers=video_headers):
                return False

            if self.config.get('cover'):
                cover_url = self._extract_first_url(aweme_data.get('video', {}).get('cover'))
                if cover_url:
                    cover_path = save_dir / f"{safe_title}_{aweme_id}_cover.jpg"
                    await self._download_with_retry(
                        cover_url,
                        cover_path,
                        session,
                        headers=self._download_headers(),
                        optional=True,
                    )

            if self.config.get('music'):
                music_url = self._extract_first_url(aweme_data.get('music', {}).get('play_url'))
                if music_url:
                    music_path = save_dir / f"{safe_title}_{aweme_id}_music.mp3"
                    await self._download_with_retry(
                        music_url,
                        music_path,
                        session,
                        headers=self._download_headers(),
                        optional=True,
                    )

        elif media_type == 'gallery':
            image_urls = self._collect_image_urls(aweme_data)
            if not image_urls:
                logger.error(f'No images found for aweme {aweme_id}')
                return False

            for index, image_url in enumerate(image_urls, start=1):
                suffix = Path(urlparse(image_url).path).suffix or '.jpg'
                image_path = save_dir / f"{safe_title}_{aweme_id}_{index}{suffix}"
                success = await self._download_with_retry(
                    image_url,
                    image_path,
                    session,
                    headers=self._download_headers(),
                )
                if not success:
                    logger.error(f'Failed downloading image {index} for aweme {aweme_id}')
                    return False
        else:
            logger.error(f"Unsupported media type for aweme {aweme_id}: {media_type}")
            return False

        if self.config.get('avatar'):
            author = aweme_data.get('author', {})
            avatar_url = self._extract_first_url(author.get('avatar_larger'))
            if avatar_url:
                avatar_path = save_dir / 'avatar.jpg'
                await self._download_with_retry(
                    avatar_url,
                    avatar_path,
                    session,
                    headers=self._download_headers(),
                    optional=True,
                )

        if self.config.get('json'):
            json_path = save_dir / f"{safe_title}_{aweme_id}_data.json"
            await self.metadata_handler.save_metadata(aweme_data, json_path)

        if self.database:
            author = aweme_data.get('author', {})
            metadata_json = json.dumps(aweme_data, ensure_ascii=False)
            await self.database.add_aweme({
                'aweme_id': aweme_id,
                'aweme_type': media_type,
                'title': desc,
                'author_id': author.get('uid'),
                'author_name': author.get('nickname', author_name),
                'create_time': aweme_data.get('create_time'),
                'file_path': str(save_dir),
                'metadata': metadata_json,
            })

        logger.info(f"Downloaded {media_type}: {desc} ({aweme_id})")
        return True

    async def _download_with_retry(
        self,
        url: str,
        save_path: Path,
        session,
        *,
        headers: Optional[Dict[str, str]] = None,
        optional: bool = False,
    ) -> bool:
        async def _task():
            success = await self.file_manager.download_file(url, save_path, session, headers=headers)
            if not success:
                raise RuntimeError(f'Download failed for {url}')
            return True

        try:
            await self.retry_handler.execute_with_retry(_task)
            return True
        except Exception as error:
            log_fn = logger.warning if optional else logger.error
            log_fn(f"Download error for {save_path.name}: {error}")
            return False

    def _detect_media_type(self, aweme_data: Dict[str, Any]) -> str:
        if aweme_data.get('image_post_info') or aweme_data.get('images'):
            return 'gallery'
        return 'video'

    def _build_no_watermark_url(self, aweme_data: Dict[str, Any]) -> Optional[Tuple[str, Dict[str, str]]]:
        video = aweme_data.get('video', {})
        play_addr = video.get('play_addr', {})
        url_candidates = [c for c in (play_addr.get('url_list') or []) if c]
        url_candidates.sort(key=lambda u: 0 if 'watermark=0' in u else 1)

        fallback_candidate: Optional[Tuple[str, Dict[str, str]]] = None

        for candidate in url_candidates:
            parsed = urlparse(candidate)
            headers = self._download_headers()

            if parsed.netloc.endswith('douyin.com'):
                if 'X-Bogus=' not in candidate:
                    signed_url, ua = self.api_client.sign_url(candidate)
                    headers = self._download_headers(user_agent=ua)
                    return signed_url, headers
                return candidate, headers

            fallback_candidate = (candidate, headers)

        if fallback_candidate:
            return fallback_candidate

        uri = play_addr.get('uri') or video.get('vid') or video.get('download_addr', {}).get('uri')
        if uri:
            params = {
                'video_id': uri,
                'ratio': '1080p',
                'line': '0',
                'is_play_url': '1',
                'watermark': '0',
                'source': 'PackSourceEnum_PUBLISH',
            }
            signed_url, ua = self.api_client.build_signed_path('/aweme/v1/play/', params)
            return signed_url, self._download_headers(user_agent=ua)

        return None

    def _collect_image_urls(self, aweme_data: Dict[str, Any]) -> List[str]:
        image_urls: List[str] = []
        image_post = aweme_data.get('image_post_info', {})
        images = image_post.get('images') or aweme_data.get('images') or []
        for item in images:
            url_list = item.get('url_list') if isinstance(item, dict) else None
            if url_list:
                image_urls.append(url_list[0])
        return image_urls

    @staticmethod
    def _extract_first_url(source: Any) -> Optional[str]:
        if isinstance(source, dict):
            url_list = source.get('url_list')
            if isinstance(url_list, list) and url_list:
                return url_list[0]
        elif isinstance(source, list) and source:
            return source[0]
        elif isinstance(source, str):
            return source
        return None
