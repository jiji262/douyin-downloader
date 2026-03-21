import re
from urllib.parse import urlparse
from typing import Optional
from utils.logger import setup_logger

logger = setup_logger('Validators')


def validate_url(url: str) -> bool:
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except Exception:
        return False


def sanitize_filename(filename: str, max_length: int = 80) -> str:
    filename = filename.replace('\n', ' ').replace('\r', ' ')
    filename = re.sub(r'[<>:"/\\|?*#\x00-\x1f]', '_', filename)
    filename = re.sub(r'[\s_]+', '_', filename)
    filename = filename.strip('._- ')

    if len(filename) > max_length:
        filename = filename[:max_length].rstrip('._- ')

    return filename or 'untitled'


def parse_url_type(url: str) -> Optional[str]:
    logger.info("Parsing URL type for: %s", url)
    
    if 'v.douyin.com' in url:
        logger.info("URL type detected: video (short link)")
        return 'video'

    path = urlparse(url).path
    logger.debug("URL path: %s", path)

    if '/video/' in path:
        logger.info("URL type detected: video")
        return 'video'
    if '/user/' in path:
        logger.info("URL type detected: user")
        return 'user'
    if '/note/' in path or '/gallery/' in path:
        logger.info("URL type detected: gallery/note")
        return 'gallery'
    if '/collection/' in path or '/mix/' in path:
        logger.info("URL type detected: collection/mix")
        return 'collection'
    if '/music/' in path:
        logger.info("URL type detected: music")
        return 'music'
    
    logger.warning("Unknown URL type for: %s", url)
    return None
