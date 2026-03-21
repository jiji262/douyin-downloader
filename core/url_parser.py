import re
from typing import Optional, Dict, Any
from utils.validators import parse_url_type
from utils.logger import setup_logger

logger = setup_logger('URLParser')


class URLParser:
    @staticmethod
    def parse(url: str) -> Optional[Dict[str, Any]]:
        logger.info("Starting URL parse: %s", url)
        
        url_type = parse_url_type(url)
        if not url_type:
            logger.error("Unsupported URL type: %s", url)
            return None

        logger.info("URL type determined: %s", url_type)

        result = {
            'original_url': url,
            'type': url_type,
        }

        if url_type == 'video':
            aweme_id = URLParser._extract_video_id(url)
            if aweme_id:
                result['aweme_id'] = aweme_id
                logger.info("Extracted video aweme_id: %s", aweme_id)
            else:
                logger.warning("Failed to extract video aweme_id from: %s", url)

        elif url_type == 'user':
            sec_uid = URLParser._extract_user_id(url)
            if sec_uid:
                result['sec_uid'] = sec_uid
                logger.info("Extracted user sec_uid: %s", sec_uid)
            else:
                logger.warning("Failed to extract user sec_uid from: %s", url)

        elif url_type == 'collection':
            mix_id = URLParser._extract_mix_id(url)
            if mix_id:
                result['mix_id'] = mix_id
                logger.info("Extracted collection mix_id: %s", mix_id)
            else:
                logger.warning("Failed to extract collection mix_id from: %s", url)

        elif url_type == 'gallery':
            note_id = URLParser._extract_note_id(url)
            if note_id:
                result['note_id'] = note_id
                result['aweme_id'] = note_id
                logger.info("Extracted gallery note_id: %s", note_id)
            else:
                logger.warning("Failed to extract gallery note_id from: %s", url)

        elif url_type == 'music':
            music_id = URLParser._extract_music_id(url)
            if music_id:
                result['music_id'] = music_id
                logger.info("Extracted music_id: %s", music_id)
            else:
                logger.warning("Failed to extract music_id from: %s", url)

        logger.info("URL parse result: %s", result)
        return result

    @staticmethod
    def _extract_video_id(url: str) -> Optional[str]:
        match = re.search(r'/video/(\d+)', url)
        if match:
            logger.debug("Extracted video_id from /video/ path: %s", match.group(1))
            return match.group(1)

        match = re.search(r'modal_id=(\d+)', url)
        if match:
            logger.debug("Extracted video_id from modal_id param: %s", match.group(1))
            return match.group(1)

        logger.debug("No video_id found in URL: %s", url)
        return None

    @staticmethod
    def _extract_user_id(url: str) -> Optional[str]:
        match = re.search(r'/user/([A-Za-z0-9_-]+)', url)
        if match:
            logger.debug("Extracted user_id from /user/ path: %s", match.group(1))
            return match.group(1)
        logger.debug("No user_id found in URL: %s", url)
        return None

    @staticmethod
    def _extract_mix_id(url: str) -> Optional[str]:
        match = re.search(r'/collection/(\d+)', url)
        if not match:
            match = re.search(r'/mix/(\d+)', url)
        if match:
            logger.debug("Extracted mix_id: %s", match.group(1))
            return match.group(1)
        logger.debug("No mix_id found in URL: %s", url)
        return None

    @staticmethod
    def _extract_note_id(url: str) -> Optional[str]:
        match = re.search(r'/(?:note|gallery)/(\d+)', url)
        if match:
            logger.debug("Extracted note_id: %s", match.group(1))
            return match.group(1)
        logger.debug("No note_id found in URL: %s", url)
        return None

    @staticmethod
    def _extract_music_id(url: str) -> Optional[str]:
        match = re.search(r'/music/(\d+)', url)
        if match:
            logger.debug("Extracted music_id: %s", match.group(1))
            return match.group(1)
        logger.debug("No music_id found in URL: %s", url)
        return None
