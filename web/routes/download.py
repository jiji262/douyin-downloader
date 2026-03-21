import asyncio
import re
from typing import Any, Callable, Dict, Optional, List

from fastapi import APIRouter, Depends, HTTPException

from auth import CookieManager
from config import ConfigLoader
from control import QueueManager, RateLimiter, RetryHandler
from core import DouyinAPIClient, URLParser, DownloaderFactory
from storage import Database, FileManager
from web.schemas import (
    DownloadRequest,
    DownloadResponse,
    ErrorResponse,
    URLParseResponse,
)
from web.task_manager import task_manager
from utils.logger import setup_logger

logger = setup_logger("DownloadRoute")

router = APIRouter(prefix="/api", tags=["download"])

_config: Optional[ConfigLoader] = None
_cookie_manager: Optional[CookieManager] = None
_database: Optional[Database] = None
_config_path: str = "config.yml"

_DOUYIN_URL_PATTERN = re.compile(
    r'(https?://)?'
    r'(www\.)?'
    r'(v\.douyin\.com|'
    r'www\.douyin\.com|'
    r'douyin\.com|'
    r'iesdouyin\.com|'
    r'www\.iesdouyin\.com)'
    r'(/[^\s<>"{}|\\^`\[\]]*)?',
    re.IGNORECASE
)


def extract_urls(text: str) -> List[str]:
    """
    从文本中提取抖音 URL。
    
    支持的 URL 格式：
    - https://v.douyin.com/xxx (短链接)
    - https://www.douyin.com/video/xxx
    - https://www.douyin.com/user/xxx
    - https://www.douyin.com/note/xxx
    - https://www.douyin.com/collection/xxx
    - https://www.douyin.com/music/xxx
    - https://douyin.com/xxx
    - https://iesdouyin.com/xxx
    
    Args:
        text: 可能包含 URL 的文本
        
    Returns:
        提取到的 URL 列表
    """
    urls = []
    matches = _DOUYIN_URL_PATTERN.findall(text)
    
    for match in matches:
        protocol = match[0] if match[0] else 'https://'
        www = match[1] if match[1] else ''
        domain = match[2]
        path = match[3] if len(match) > 3 and match[3] else ''
        
        if not path:
            continue
            
        url = f"{protocol}{www}{domain}{path}"
        url = url.rstrip('.,;:!?)，。；：！》）】」』')
        
        if url not in urls:
            urls.append(url)
    
    return urls


def extract_first_url(text: str) -> Optional[str]:
    """
    从文本中提取第一个抖音 URL。
    
    Args:
        text: 可能包含 URL 的文本
        
    Returns:
        第一个 URL，如果没有找到则返回 None
    """
    urls = extract_urls(text)
    return urls[0] if urls else None


def set_config_path(config_path: str):
    global _config_path, _config, _cookie_manager, _database
    _config_path = config_path
    _config = None
    _cookie_manager = None
    _database = None


def get_config() -> ConfigLoader:
    global _config
    if _config is None:
        _config = ConfigLoader(_config_path)
    return _config


def get_cookie_manager() -> CookieManager:
    global _cookie_manager
    if _cookie_manager is None:
        config = get_config()
        _cookie_manager = CookieManager()
        _cookie_manager.set_cookies(config.get_cookies())
    return _cookie_manager


async def get_database() -> Optional[Database]:
    global _database
    if _database is None:
        config = get_config()
        if config.get("database"):
            db_path = config.get("database_path", "dy_downloader.db") or "dy_downloader.db"
            _database = Database(db_path=str(db_path))
            await _database.initialize()
    return _database


class WebProgressReporter:
    def __init__(self, callback: Optional[Callable] = None):
        self._callback = callback
        self._step = ""
        self._detail = ""
        self._item_total = 0
        self._item_current = 0
        self._success = 0
        self._failed = 0
        self._skipped = 0

    def update_step(self, step: str, detail: str = "") -> None:
        self._step = step
        self._detail = detail
        self._notify()

    def set_item_total(self, total: int, detail: str = "") -> None:
        self._item_total = total
        self._detail = detail
        self._notify()

    def advance_item(self, status: str, detail: str = "") -> None:
        self._item_current += 1
        if status == "success":
            self._success += 1
        elif status == "failed":
            self._failed += 1
        elif status == "skipped":
            self._skipped += 1
        self._detail = detail
        self._notify()

    def _notify(self) -> None:
        if self._callback:
            from web.schemas import TaskProgress
            progress = TaskProgress(
                step=self._step,
                detail=self._detail,
                item_total=self._item_total,
                item_current=self._item_current,
                success=self._success,
                failed=self._failed,
                skipped=self._skipped,
            )
            self._callback(progress)

    def get_progress(self) -> Dict[str, Any]:
        return {
            "step": self._step,
            "detail": self._detail,
            "item_total": self._item_total,
            "item_current": self._item_current,
            "success": self._success,
            "failed": self._failed,
            "skipped": self._skipped,
        }


async def download_url_for_web(
    url: str,
    config: ConfigLoader,
    cookie_manager: CookieManager,
    database: Optional[Database] = None,
    progress_callback: Optional[Callable] = None,
    cancel_event: Optional[asyncio.Event] = None,
    **kwargs,
):
    progress_reporter = WebProgressReporter(callback=progress_callback)
    
    download_path = config.get("path")
    logger.info("Download path from config: %s", download_path)
    
    file_manager = FileManager(download_path)
    logger.info("FileManager base_path: %s", file_manager.base_path)
    
    rate_limiter = RateLimiter(max_per_second=float(config.get("rate_limit", 2) or 2))
    retry_handler = RetryHandler(max_retries=config.get("retry_times", 3))
    thread_count = int(config.get("thread", 5) or 5)
    queue_manager = QueueManager(max_workers=thread_count)

    original_url = url
    
    logger.info("Starting download for URL: %s", url)

    async with DouyinAPIClient(
        cookie_manager.get_cookies(),
        proxy=config.get("proxy"),
    ) as api_client:
        if cancel_event and cancel_event.is_set():
            return None

        progress_reporter.update_step("解析链接", "检查短链并解析 URL")
        logger.info("Checking if URL is short link: %s", url)
        
        if url.startswith("https://v.douyin.com"):
            logger.info("Detected short link, resolving: %s", url)
            resolved_url = await api_client.resolve_short_url(url)
            if resolved_url:
                logger.info("Short link resolved to: %s", resolved_url)
                url = resolved_url
            else:
                progress_reporter.update_step("解析链接", "短链解析失败")
                logger.error("Failed to resolve short URL: %s", url)
                return None
        else:
            logger.info("Not a short link, proceeding with direct URL")

        if cancel_event and cancel_event.is_set():
            return None

        logger.info("Parsing URL: %s", url)
        parsed = URLParser.parse(url)
        if not parsed:
            progress_reporter.update_step("解析链接", "URL 解析失败")
            logger.error("Failed to parse URL: %s", url)
            return None

        logger.info("URL parsed successfully: type=%s, data=%s", parsed.get("type"), parsed)
        progress_reporter.update_step("创建下载器", f"URL 类型: {parsed['type']}")

        downloader = DownloaderFactory.create(
            parsed["type"],
            config,
            api_client,
            file_manager,
            cookie_manager,
            database,
            rate_limiter,
            retry_handler,
            queue_manager,
            progress_reporter=progress_reporter,
        )

        if not downloader:
            progress_reporter.update_step("创建下载器", "未找到匹配下载器")
            logger.error("No downloader found for type: %s", parsed["type"])
            return None

        if cancel_event and cancel_event.is_set():
            return None

        progress_reporter.update_step("执行下载", "开始拉取与下载资源")
        result = await downloader.download(parsed)
        
        logger.info(
            "Download result: total=%d, success=%d, failed=%d, skipped=%d",
            result.total if result else 0,
            result.success if result else 0,
            result.failed if result else 0,
            result.skipped if result else 0,
        )

        if cancel_event and cancel_event.is_set():
            return None

        if result and database:
            import json
            safe_config = {
                k: v for k, v in config.config.items()
                if k not in ("cookies", "cookie", "transcript")
            }
            await database.add_history({
                "url": original_url,
                "url_type": parsed["type"],
                "total_count": result.total,
                "success_count": result.success,
                "config": json.dumps(safe_config, ensure_ascii=False),
            })

        return result


@router.post(
    "/download",
    response_model=DownloadResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="提交下载任务",
    description="提交一个抖音视频/用户/合集/音乐的下载任务",
)
async def create_download_task(
    request: DownloadRequest,
    config: ConfigLoader = Depends(get_config),
    cookie_manager: CookieManager = Depends(get_cookie_manager),
):
    if not cookie_manager.validate_cookies():
        raise HTTPException(status_code=400, detail="Cookies 无效或不完整，请先更新 Cookie")

    extracted_url = extract_first_url(request.url)
    if not extracted_url:
        raise HTTPException(status_code=400, detail="未能在输入中找到有效的抖音链接")

    override_config = {}
    if request.path:
        override_config["path"] = request.path
    if request.thread:
        override_config["thread"] = request.thread
    if request.cover is not None:
        override_config["cover"] = request.cover
    if request.music is not None:
        override_config["music"] = request.music
    if request.avatar is not None:
        override_config["avatar"] = request.avatar
    if request.save_json is not None:
        override_config["json"] = request.save_json

    if override_config:
        config.update(**override_config)

    database = await get_database()

    async def download_wrapper(
        url: str,
        progress_callback: Optional[Callable] = None,
        cancel_event: Optional[asyncio.Event] = None,
        **kwargs,
    ):
        return await download_url_for_web(
            url=url,
            config=config,
            cookie_manager=cookie_manager,
            database=database,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
            **kwargs,
        )

    try:
        task_id = await task_manager.create_task(
            url=extracted_url,
            download_func=download_wrapper,
        )
        return DownloadResponse(
            task_id=task_id,
            message="任务已创建",
            status=task_manager.get_task(task_id).status,
        )
    except Exception as e:
        logger.error("Failed to create download task: %s", e)
        raise HTTPException(status_code=500, detail=f"创建任务失败: {str(e)}")


@router.get(
    "/url/parse",
    response_model=URLParseResponse,
    responses={400: {"model": ErrorResponse}},
    summary="解析 URL",
    description="解析抖音链接，返回链接类型和相关信息（支持从文本中提取 URL）",
)
async def parse_url(url: str):
    extracted_url = extract_first_url(url)
    if not extracted_url:
        raise HTTPException(status_code=400, detail="未能在输入中找到有效的抖音链接")

    parsed = URLParser.parse(extracted_url)
    if not parsed:
        raise HTTPException(status_code=400, detail="无法解析该 URL")

    return URLParseResponse(
        original_url=parsed.get("original_url", extracted_url),
        url_type=parsed.get("type", "unknown"),
        aweme_id=parsed.get("aweme_id"),
        sec_uid=parsed.get("sec_uid"),
        mix_id=parsed.get("mix_id"),
        music_id=parsed.get("music_id"),
    )
