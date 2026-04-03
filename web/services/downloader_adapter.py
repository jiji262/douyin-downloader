"""
下载器适配器 - 桥接 Web 模块和核心下载功能
"""
import asyncio
import time
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any
from datetime import datetime

from config import ConfigLoader
from auth import CookieManager
from storage import Database, FileManager
from control import RateLimiter, RetryHandler, QueueManager
from core.api_client import DouyinAPIClient
from core.video_downloader import VideoDownloader
from core.user_downloader import UserDownloader
from core.downloader_factory import DownloaderFactory
from utils.logger import setup_logger

logger = setup_logger("WebDownloaderAdapter")


class WebProgressReporter:
    """Web 进度报告器 - 将 CLI 进度转换为 Web 可用的格式"""
    
    def __init__(self, callback: Callable[[dict], None]):
        self.callback = callback
        self.current_step = ""
        self.item_total = 0
        self.items_processed = 0
    
    def update_step(self, step: str, detail: str = ""):
        self.current_step = f"{step}: {detail}" if detail else step
        self.callback({
            'type': 'step',
            'step': self.current_step,
        })
    
    def set_item_total(self, total: int, detail: str = ""):
        self.item_total = total
        self.callback({
            'type': 'total',
            'total': total,
            'detail': detail,
        })
    
    def advance_item(self, status: str, detail: str = ""):
        self.items_processed += 1
        percent = (self.items_processed / self.item_total * 100) if self.item_total > 0 else 0
        self.callback({
            'type': 'progress',
            'current': self.items_processed,
            'total': self.item_total,
            'percent': percent,
            'status': status,
            'detail': detail,
        })


class DownloaderAdapter:
    """下载器适配器"""
    
    def __init__(self, config_path: str = "config/config.yaml"):
        self.config_path = config_path
        self._cookie_manager: Optional[CookieManager] = None
        self._api_client: Optional[DouyinAPIClient] = None
        self._file_manager: Optional[FileManager] = None
        self._database: Optional[Database] = None
    
    def _init_components(self, download_dir: str = None):
        """初始化下载组件"""
        config = ConfigLoader(self.config_path)
        
        # 如果指定了下载目录，覆盖配置
        if download_dir:
            config.config['path'] = download_dir
        
        self._cookie_manager = CookieManager(config)
        cookies = self._cookie_manager.load_cookies()
        
        if not cookies:
            raise ValueError("No cookies found. Please login first.")
        
        self._api_client = DouyinAPIClient(cookies)
        self._file_manager = FileManager(config)
        self._database = Database(config)
    
    async def download_video(self, url: str, download_dir: str = None,
                            mode: str = "balance",
                            progress_callback: Callable[[dict], None] = None) -> Dict:
        """下载单个视频"""
        start_time = time.time()
        
        try:
            self._init_components(download_dir)
            
            # 创建进度报告器
            reporter = WebProgressReporter(progress_callback) if progress_callback else None
            
            # 创建下载器
            downloader = VideoDownloader(
                config=ConfigLoader(self.config_path),
                api_client=self._api_client,
                file_manager=self._file_manager,
                cookie_manager=self._cookie_manager,
                database=self._database,
                rate_limiter=RateLimiter(),
                retry_handler=RetryHandler(),
                queue_manager=QueueManager(max_workers=self._get_thread_count(mode)),
                progress_reporter=reporter,
            )
            
            # 解析 URL
            from core.url_parser import URLParser
            parser = URLParser()
            parsed = parser.parse(url)
            
            if not parsed:
                return {
                    'success': False,
                    'error': 'Failed to parse URL',
                    'duration': time.time() - start_time,
                }
            
            # 执行下载
            result = await downloader.download(parsed)
            
            duration = time.time() - start_time
            
            return {
                'success': result.success > 0,
                'total': result.total,
                'success_count': result.success,
                'failed_count': result.failed,
                'skipped_count': result.skipped,
                'duration': duration,
            }
            
        except Exception as e:
            logger.error(f"Download video error: {e}")
            return {
                'success': False,
                'error': str(e),
                'duration': time.time() - start_time,
            }
    
    async def download_homepage(self, sec_uid: str, modes: List[str] = None,
                               download_dir: str = None, mode: str = "balance",
                               progress_callback: Callable[[dict], None] = None) -> Dict:
        """下载主页内容"""
        start_time = time.time()
        
        try:
            self._init_components(download_dir)
            
            # 创建进度报告器
            reporter = WebProgressReporter(progress_callback) if progress_callback else None
            
            # 创建下载器
            downloader = UserDownloader(
                config=ConfigLoader(self.config_path),
                api_client=self._api_client,
                file_manager=self._file_manager,
                cookie_manager=self._cookie_manager,
                database=self._database,
                rate_limiter=RateLimiter(),
                retry_handler=RetryHandler(),
                queue_manager=QueueManager(max_workers=self._get_thread_count(mode)),
                progress_reporter=reporter,
            )
            
            # 解析 URL（构造一个临时的）
            parsed = {
                'sec_uid': sec_uid,
                'url': f'https://www.douyin.com/user/{sec_uid}',
            }
            
            # 设置模式
            if modes:
                downloader.config._config['mode'] = modes
            
            # 执行下载
            result = await downloader.download(parsed)
            
            duration = time.time() - start_time
            
            return {
                'success': result.success > 0,
                'total': result.total,
                'success_count': result.success,
                'failed_count': result.failed,
                'skipped_count': result.skipped,
                'duration': duration,
            }
            
        except Exception as e:
            logger.error(f"Download homepage error: {e}")
            return {
                'success': False,
                'error': str(e),
                'duration': time.time() - start_time,
            }
    
    def _get_thread_count(self, mode: str) -> int:
        """根据模式获取线程数"""
        mode_map = {
            'fast': 10,      # 急速
            'balance': 5,    # 平衡
            'stable': 2,     # 稳定
        }
        return mode_map.get(mode.lower(), 5)
    
    async def check_cookie_valid(self) -> bool:
        """检查 Cookie 是否有效"""
        try:
            self._init_components()
            
            # 尝试获取用户信息
            user_info = await self._api_client.get_user_info('self')
            return user_info is not None
            
        except Exception:
            return False
    
    def get_cookie_status(self) -> Dict:
        """获取 Cookie 状态"""
        try:
            config = ConfigLoader(self.config_path)
            cookie_manager = CookieManager(config)
            cookies = cookie_manager.load_cookies()
            
            if not cookies:
                return {
                    'valid': False,
                    'message': 'No cookies found',
                }
            
            return {
                'valid': True,
                'message': 'Cookies loaded',
                'keys': list(cookies.keys()),
            }
            
        except Exception as e:
            return {
                'valid': False,
                'message': str(e),
            }


# 全局适配器实例
_adapter: Optional[DownloaderAdapter] = None


def get_downloader_adapter() -> DownloaderAdapter:
    """获取全局下载器适配器实例"""
    global _adapter
    if _adapter is None:
        _adapter = DownloaderAdapter()
    return _adapter
