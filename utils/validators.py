import re
from urllib.parse import urlparse
from typing import Optional


def validate_url(url: str) -> bool:
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except:
        return False


def sanitize_filename(filename: str, max_length: int = 80) -> str:
    # 换行符 → 空格
    filename = filename.replace('\n', ' ').replace('\r', ' ')
    # Windows 非法字符 + #，逗号 → 下划线
    filename = re.sub(r'[<>:"/\\|?*#\x00-\x1f]', '_', filename)
    # 连续空格/下划线 → 单个下划线
    filename = re.sub(r'[\s_]+', '_', filename)
    # 去首尾
    filename = filename.strip('._- ')

    if len(filename) > max_length:
        filename = filename[:max_length].rstrip('._- ')

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