from typing import Any, Dict

from core.downloader_base import BaseDownloader, DownloadResult
from utils.logger import setup_logger

logger = setup_logger('UserDownloader')


class UserDownloader(BaseDownloader):
    async def download(self, parsed_url: Dict[str, Any]) -> DownloadResult:
        result = DownloadResult()

        sec_uid = parsed_url.get('sec_uid')
        if not sec_uid:
            logger.error("No sec_uid found in parsed URL")
            return result

        user_info = await self.api_client.get_user_info(sec_uid)
        if not user_info:
            logger.error(f"Failed to get user info: {sec_uid}")
            return result

        modes = self.config.get('mode', ['post'])

        for mode in modes:
            if mode == 'post':
                mode_result = await self._download_user_post(sec_uid, user_info)
                result.total += mode_result.total
                result.success += mode_result.success
                result.failed += mode_result.failed
                result.skipped += mode_result.skipped

        return result

    async def _download_user_post(self, sec_uid: str, user_info: Dict[str, Any]) -> DownloadResult:
        result = DownloadResult()
        aweme_list = []
        max_cursor = 0
        has_more = True

        increase_enabled = self.config.get('increase', {}).get('post', False)
        latest_time = None

        if increase_enabled and self.database:
            latest_time = await self.database.get_latest_aweme_time(user_info.get('uid'))

        while has_more:
            await self.rate_limiter.acquire()

            data = await self.api_client.get_user_post(sec_uid, max_cursor)
            if not data:
                break

            aweme_items = data.get('aweme_list', [])
            if not aweme_items:
                break

            if increase_enabled and latest_time:
                new_items = [a for a in aweme_items if a.get('create_time', 0) > latest_time]
                aweme_list.extend(new_items)
                if len(new_items) < len(aweme_items):
                    break
            else:
                aweme_list.extend(aweme_items)

            has_more = data.get('has_more', False)
            max_cursor = data.get('max_cursor', 0)

            number_limit = self.config.get('number', {}).get('post', 0)
            if number_limit > 0 and len(aweme_list) >= number_limit:
                aweme_list = aweme_list[:number_limit]
                break

        aweme_list = self._filter_by_time(aweme_list)
        aweme_list = self._limit_count(aweme_list, 'post')

        result.total = len(aweme_list)

        author_name = user_info.get('nickname', 'unknown')

        async def _process_aweme(item: Dict[str, Any]):
            aweme_id = item.get('aweme_id')
            if not await self._should_download(aweme_id):
                return {'status': 'skipped', 'aweme_id': aweme_id}

            success = await self._download_aweme_assets(item, author_name, mode='post')
            return {
                'status': 'success' if success else 'failed',
                'aweme_id': aweme_id,
            }

        download_results = await self.queue_manager.download_batch(_process_aweme, aweme_list)

        for entry in download_results:
            status = entry.get('status') if isinstance(entry, dict) else None
            if status == 'success':
                result.success += 1
            elif status == 'failed':
                result.failed += 1
            elif status == 'skipped':
                result.skipped += 1
            else:
                result.failed += 1

        return result
