import re
from urllib.parse import urlparse
from typing import Optional


def validate_url(url: str) -> bool:
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except Exception:
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


SHORT_URL_HOSTS = (
    'v.douyin.com',
    'v.iesdouyin.com',
    'iesdouyin.com',
)


def is_short_url(url: str) -> bool:
    """判断是否为需要预先解析的短链。"""
    if not url:
        return False
    # 允许用户粘贴不带 scheme 的短链（例如直接从 App 复制）
    candidate = url.strip()
    lowered = candidate.lower()
    for scheme in ('https://', 'http://'):
        if lowered.startswith(scheme):
            lowered = lowered[len(scheme):]
            break
    for host in SHORT_URL_HOSTS:
        if lowered.startswith(f'{host}/') or lowered == host:
            return True
    return False


def normalize_short_url(url: str) -> str:
    """确保短链带 https:// 前缀，便于传给 aiohttp。"""
    stripped = (url or '').strip()
    if stripped.lower().startswith(('http://', 'https://')):
        return stripped
    return f'https://{stripped}'


def parse_url_type(url: str) -> Optional[str]:
    # 短链在调用方（CLI/调度层）统一先解析为真实 URL 后再判断类型；
    # 若仍是短链，返回 'short' 明确提示需要解析，而不是错误地全部落到 'video'。
    if is_short_url(url):
        return 'short'

    parsed = urlparse(url)
    host = (parsed.netloc or '').lower()
    path = parsed.path

    # live.douyin.com/{room_id} — 直播间专用子域，path 仅有一段数字。
    if host.startswith('live.douyin.com'):
        return 'live'

    if '/video/' in path:
        return 'video'
    if '/user/' in path:
        return 'user'
    if '/note/' in path or '/gallery/' in path or '/slides/' in path:
        return 'gallery'
    if '/collection/' in path or '/mix/' in path:
        return 'collection'
    if '/music/' in path:
        return 'music'
    if '/live/' in path or '/follow/live/' in path:
        return 'live'
    return None
