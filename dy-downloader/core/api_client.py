from __future__ import annotations

import aiohttp
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

from utils.logger import setup_logger
from utils.xbogus import XBogus

logger = setup_logger('APIClient')


class DouyinAPIClient:
    BASE_URL = 'https://www.douyin.com'

    def __init__(self, cookies: Dict[str, str]):
        self.cookies = cookies or {}
        self._session: Optional[aiohttp.ClientSession] = None
        self.headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36'
            ),
            'Referer': 'https://www.douyin.com/',
            'Accept': 'application/json',
            'Accept-Encoding': 'gzip, deflate',
            'Accept-Language': 'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7',
            'Connection': 'keep-alive',
        }
        self._signer = XBogus(self.headers['User-Agent'])

    async def __aenter__(self) -> 'DouyinAPIClient':
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    async def _ensure_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self.headers,
                cookies=self.cookies,
                timeout=aiohttp.ClientTimeout(total=30),
                raise_for_status=False,
            )

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def get_session(self) -> aiohttp.ClientSession:
        await self._ensure_session()
        assert self._session is not None
        return self._session

    def _default_query(self) -> Dict[str, Any]:
        return {
            'device_platform': 'webapp',
            'aid': '6383',
            'channel': 'channel_pc_web',
            'pc_client_type': '1',
            'version_code': '170400',
            'version_name': '17.4.0',
            'cookie_enabled': 'true',
            'screen_width': '1920',
            'screen_height': '1080',
            'browser_language': 'zh-CN',
            'browser_platform': 'Win32',
            'browser_name': 'Chrome',
            'browser_version': '123.0.0.0',
            'browser_online': 'true',
            'engine_name': 'Blink',
            'engine_version': '123.0.0.0',
            'os_name': 'Windows',
            'os_version': '10',
            'cpu_core_num': '8',
            'device_memory': '8',
            'platform': 'PC',
            'downlink': '10',
            'effective_type': '4g',
            'round_trip_time': '50',
            'msToken': self.cookies.get('msToken', ''),
        }

    def sign_url(self, url: str) -> Tuple[str, str]:
        signed_url, _xbogus, ua = self._signer.build(url)
        return signed_url, ua

    def build_signed_path(self, path: str, params: Dict[str, Any]) -> Tuple[str, str]:
        query = urlencode(params)
        url = f"{self.BASE_URL}{path}?{query}"
        return self.sign_url(url)

    async def get_video_detail(self, aweme_id: str) -> Optional[Dict[str, Any]]:
        params = self._default_query()
        params.update({
            'aweme_id': aweme_id,
            'aid': '1128',
        })

        await self._ensure_session()
        signed_url, ua = self.build_signed_path('/aweme/v1/web/aweme/detail/', params)

        try:
            async with self._session.get(signed_url, headers={**self.headers, 'User-Agent': ua}) as response:
                if response.status == 200:
                    data = await response.json(content_type=None)
                    return data.get('aweme_detail')
                logger.error(f"Video detail request failed: {aweme_id}, status={response.status}")
        except Exception as e:
            logger.error(f"Failed to get video detail: {aweme_id}, error: {e}")

        return None

    async def get_user_post(self, sec_uid: str, max_cursor: int = 0, count: int = 20) -> Dict[str, Any]:
        params = self._default_query()
        params.update({
            'sec_user_id': sec_uid,
            'max_cursor': max_cursor,
            'count': count,
            'locate_query': 'false',
            'show_live_replay_strategy': '1',
            'need_time_list': '1',
            'time_list_query': '0',
            'whale_cut_token': '',
            'cut_version': '1',
            'publish_video_strategy_type': '2',
        })

        await self._ensure_session()
        signed_url, ua = self.build_signed_path('/aweme/v1/web/aweme/post/', params)

        try:
            async with self._session.get(signed_url, headers={**self.headers, 'User-Agent': ua}) as response:
                if response.status == 200:
                    return await response.json(content_type=None)
                logger.error(f"User post request failed: {sec_uid}, status={response.status}")
        except Exception as e:
            logger.error(f"Failed to get user post: {sec_uid}, error: {e}")

        return {}

    async def get_user_info(self, sec_uid: str) -> Optional[Dict[str, Any]]:
        params = self._default_query()
        params.update({'sec_user_id': sec_uid})

        await self._ensure_session()
        signed_url, ua = self.build_signed_path('/aweme/v1/web/user/profile/other/', params)

        try:
            async with self._session.get(signed_url, headers={**self.headers, 'User-Agent': ua}) as response:
                if response.status == 200:
                    data = await response.json(content_type=None)
                    return data.get('user')
                logger.error(f"User info request failed: {sec_uid}, status={response.status}")
        except Exception as e:
            logger.error(f"Failed to get user info: {sec_uid}, error: {e}")

        return None

    async def resolve_short_url(self, short_url: str) -> Optional[str]:
        try:
            await self._ensure_session()
            async with self._session.get(short_url, allow_redirects=True) as response:
                return str(response.url)
        except Exception as e:
            logger.error(f"Failed to resolve short URL: {short_url}, error: {e}")
            return None
