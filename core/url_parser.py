import re
from typing import Optional, Dict, Any
from utils.validators import parse_url_type
from utils.logger import setup_logger

logger = setup_logger('URLParser')


class URLParser:
    @staticmethod
    def parse(url: str) -> Optional[Dict[str, Any]]:
        url_type = parse_url_type(url)
        if not url_type:
            logger.error("Unsupported URL type: %s", url)
            return None

        result = {
            'original_url': url,
            'type': url_type,
        }

        if url_type == 'video':
            aweme_id = URLParser._extract_video_id(url)
            if aweme_id:
                result['aweme_id'] = aweme_id

        elif url_type == 'user':
            sec_uid = URLParser._extract_user_id(url)
            if sec_uid:
                result['sec_uid'] = sec_uid

        elif url_type == 'collection':
            mix_id = URLParser._extract_mix_id(url)
            if mix_id:
                result['mix_id'] = mix_id

        elif url_type == 'gallery':
            note_id = URLParser._extract_note_id(url)
            if note_id:
                result['note_id'] = note_id
                result['aweme_id'] = note_id

        elif url_type == 'music':
            music_id = URLParser._extract_music_id(url)
            if music_id:
                result['music_id'] = music_id

        return result

    @staticmethod
    def _extract_video_id(url: str) -> Optional[str]:
        match = re.search(r'/video/(\d+)', url)
        if match:
            return match.group(1)

        match = re.search(r'modal_id=(\d+)', url)
        if match:
            return match.group(1)

        return None

    @staticmethod
    def _extract_user_id(url: str) -> Optional[str]:
        match = re.search(r'/user/([A-Za-z0-9_-]+)', url)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _extract_mix_id(url: str) -> Optional[str]:
        match = re.search(r'/collection/(\d+)', url)
        if not match:
            match = re.search(r'/mix/(\d+)', url)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _extract_note_id(url: str) -> Optional[str]:
        match = re.search(r'/(?:note|gallery)/(\d+)', url)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _extract_music_id(url: str) -> Optional[str]:
        match = re.search(r'/music/(\d+)', url)
        if match:
            return match.group(1)
        return None
