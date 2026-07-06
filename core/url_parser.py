import re
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from utils.logger import setup_logger
from utils.validators import parse_url_type

logger = setup_logger("URLParser")


class URLParser:
    @staticmethod
    def parse(url: str) -> Optional[Dict[str, Any]]:
        url_type = parse_url_type(url)
        if not url_type:
            logger.error("Unsupported URL type: %s", url)
            return None

        result = {
            "original_url": url,
            "type": url_type,
        }

        if url_type == "video":
            aweme_id = URLParser._extract_video_id(url)
            if aweme_id:
                result["aweme_id"] = aweme_id

        elif url_type == "user":
            sec_uid = URLParser._extract_user_id(url)
            if sec_uid:
                result["sec_uid"] = sec_uid

        elif url_type == "collection":
            mix_id = URLParser._extract_mix_id(url)
            if mix_id:
                result["mix_id"] = mix_id

        elif url_type == "gallery":
            note_id = URLParser._extract_note_id(url)
            if note_id:
                result["note_id"] = note_id
                result["aweme_id"] = note_id

        elif url_type == "music":
            music_id = URLParser._extract_music_id(url)
            if music_id:
                result["music_id"] = music_id

        elif url_type == "live":
            room_id = URLParser._extract_room_id(url)
            if room_id:
                result["room_id"] = room_id

        elif url_type == "live_replay":
            episode_id, replay_id = URLParser._extract_live_replay_ids(url)
            if episode_id:
                result["episode_id"] = episode_id
            if replay_id:
                result["replay_id"] = replay_id

        return result

    @staticmethod
    def _extract_video_id(url: str) -> Optional[str]:
        match = re.search(r"/video/(\d+)", url)
        if match:
            return match.group(1)

        match = re.search(r"modal_id=(\d+)", url)
        if match:
            return match.group(1)

        return None

    @staticmethod
    def _extract_user_id(url: str) -> Optional[str]:
        match = re.search(r"/user/([A-Za-z0-9_-]+)", url)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _extract_mix_id(url: str) -> Optional[str]:
        match = re.search(r"/collection/(\d+)", url)
        if not match:
            match = re.search(r"/mix/(\d+)", url)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _extract_note_id(url: str) -> Optional[str]:
        match = re.search(r"/(?:note|gallery|slides)/(\d+)", url)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _extract_music_id(url: str) -> Optional[str]:
        match = re.search(r"/music/(\d+)", url)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _extract_room_id(url: str) -> Optional[str]:
        # 直播链接形态：
        #   https://live.douyin.com/123456789
        #   https://www.douyin.com/follow/live/123456789
        match = re.search(r"/live/(\d+)", url)
        if match:
            return match.group(1)
        match = re.search(r"live\.douyin\.com/(\d+)", url)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _extract_live_replay_ids(url: str) -> Tuple[Optional[str], Optional[str]]:
        # 直播回放链接形态：
        #   https://www.douyin.com/vsdetail/7331203341890049058
        #   https://webcast.amemv.com/douyin/webcast/reflow/episode/733...?replay_id=734...
        parsed = urlparse(url)
        path = parsed.path
        episode_id: Optional[str] = None
        replay_id: Optional[str] = None

        if path.startswith("/vsdetail/"):
            match = re.match(r"^/vsdetail/(\d+)(?:/|$)", path)
            if match:
                episode_id = match.group(1)
        elif path.startswith("/douyin/webcast/reflow/episode/"):
            match = re.match(r"^/douyin/webcast/reflow/episode/(\d+)(?:/|$)", path)
            if match:
                episode_id = match.group(1)

        replay_ids = parse_qs(parsed.query).get("replay_id", [])
        if replay_ids and replay_ids[0].strip().isdigit():
            replay_id = replay_ids[0].strip()
        return episode_id, replay_id
