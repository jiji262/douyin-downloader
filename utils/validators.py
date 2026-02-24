import re
from urllib.parse import urlparse
from typing import Optional


def validate_url(url: str) -> bool:
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except:
        return False


def sanitize_filename(filename: str, max_length: int = 200) -> str:
    invalid_chars = r'[<>:"/\\|?*\x00-\x1f]'
    filename = re.sub(invalid_chars, '_', filename)
    filename = filename.strip('. ')

    if len(filename) > max_length:
        filename = filename[:max_length]

    return filename or 'untitled'


def parse_url_type(url: str) -> Optional[str]:
    if 'v.douyin.com' in url:
        return 'video'

    path = urlparse(url).path

    if '/video/' in path:
        return 'video'
    if '/user/' in path:
        return 'user'
    if '/note/' in path or '/gallery/' in path:
        return 'gallery'
    return None
