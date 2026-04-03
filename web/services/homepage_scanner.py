"""
主页扫描器 - 后台循环任务
"""
import asyncio
import threading
import time
from datetime import datetime
from typing import List, Dict, Optional

from web.utils import HomepageManager, DatabaseManager, URLParser
from web.services import get_task_manager, get_downloader_adapter
from utils.logger import setup_logger

logger = setup_logger("HomepageScanner")


class HomepageScanner:
    """主页扫描器"""
    
    def __init__(self):
        self.hp_manager = HomepageManager()
        self.db_manager = DatabaseManager()
        self.task_manager = get_task_manager()
        self.adapter = get_downloader_adapter()
        
        self._running = False
        self._stop_flag = False
        self._thread: Optional[threading.Thread] = None
        self._scan_interval = 1800  # 默认 30 分钟
    
    def set_scan_interval(self, minutes: int):
        """设置扫描间隔（分钟）"""
        self._scan_interval = minutes * 60
    
    def start(self):
        """启动扫描器"""
        if self._running:
            return
        
        self._running = True
        self._stop_flag = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        
        logger.info("Homepage scanner started")
    
    def stop(self):
        """停止扫描器"""
        self._stop_flag = True
        self._running = False
        
        if self._thread:
            self._thread.join(timeout=5)
        
        logger.info("Homepage scanner stopped")
    
    def is_running(self) -> bool:
        """检查是否运行中"""
        return self._running
    
    def _run(self):
        """扫描主循环"""
        while not self._stop_flag:
            try:
                self._scan_once()
            except Exception as e:
                logger.error(f"Scan error: {e}")
                self.db_manager.add_error_log(
                    level='ERROR',
                    message=f'Homepage scan failed',
                    source='homepage_scanner',
                    details=str(e)
                )
            
            # 等待下一次扫描
            if not self._stop_flag:
                for _ in range(self._scan_interval):
                    if self._stop_flag:
                        break
                    time.sleep(1)
    
    def _scan_once(self):
        """执行一次扫描"""
        homepages = self.hp_manager.get_all_homepages()
        
        if not homepages:
            return
        
        # 创建任务进度
        self.task_manager.create_homepage_task(len(homepages))
        
        total_videos_found = 0
        total_videos_downloaded = 0
        
        for i, url in enumerate(homepages, 1):
            if self._stop_flag:
                self.task_manager.complete_homepage_task('cancelled')
                return
            
            try:
                # 更新进度
                self.task_manager.update_homepage_progress(
                    current_homepage=i,
                    total_videos=total_videos_found,
                    downloaded_videos=total_videos_downloaded,
                    current_nickname=f"Processing {i}/{len(homepages)}"
                )
                
                # 解析 URL 获取 sec_uid
                parsed = URLParser.parse_douyin_user_url(url)
                
                if not parsed or not parsed.get('sec_uid'):
                    logger.warning(f"Failed to parse homepage URL: {url}")
                    continue
                
                sec_uid = parsed['sec_uid']
                
                # 下载主页内容
                result = asyncio.run(self.adapter.download_homepage(
                    sec_uid=sec_uid,
                    modes=['post'],  # 只下载视频
                    progress_callback=lambda data: self._handle_progress(data, i)
                ))
                
                if result.get('success'):
                    total_videos_downloaded += result.get('success_count', 0)
                    
                    # 更新主页统计
                    self.db_manager.update_homepage_stats(
                        sec_uid=sec_uid,
                        url=url,
                        video_count=result.get('success_count', 0),
                        last_scan_time=datetime.now().isoformat(),
                        status='active'
                    )
                    
                    # 重置失败计数
                    self.db_manager.update_homepage_stats(
                        sec_uid=sec_uid,
                        url=url,
                        fail_increment=0
                    )
                else:
                    # 增加失败计数
                    stats = self.db_manager.get_homepage_stats()
                    homepage_stat = next((h for h in stats if h['sec_uid'] == sec_uid), None)
                    
                    if homepage_stat:
                        new_fail_count = homepage_stat.get('fail_count', 0) + 1
                        
                        # 如果失败次数过多，标记为失效
                        if new_fail_count >= 5:
                            self.db_manager.update_homepage_stats(
                                sec_uid=sec_uid,
                                url=url,
                                status='inactive'
                            )
                            self.hp_manager.remove_homepage(url)
                            logger.info(f"Removed inactive homepage: {url}")
                        else:
                            self.db_manager.update_homepage_stats(
                                sec_uid=sec_uid,
                                url=url,
                                fail_increment=1
                            )
                    
                    # 记录错误
                    self.db_manager.add_error_log(
                        level='ERROR',
                        message=f'Homepage download failed: {url}',
                        source='homepage_scanner',
                        details=result.get('error', 'Unknown error')
                    )
                
                total_videos_found += result.get('total', 0)
                
            except Exception as e:
                logger.error(f"Error processing homepage {url}: {e}")
                self.db_manager.add_error_log(
                    level='ERROR',
                    message=f'Homepage processing failed: {url}',
                    source='homepage_scanner',
                    details=str(e)
                )
        
        # 完成扫描
        self.task_manager.complete_homepage_task('completed')
        logger.info(f"Scan completed. Found: {total_videos_found}, Downloaded: {total_videos_downloaded}")
    
    def _handle_progress(self, data: dict, homepage_index: int):
        """处理进度回调"""
        if data.get('type') == 'progress':
            pass  # 单个视频进度已在 download_homepage 中处理


# 全局扫描器实例
_scanner: Optional[HomepageScanner] = None


def get_scanner() -> HomepageScanner:
    """获取全局扫描器实例"""
    global _scanner
    if _scanner is None:
        _scanner = HomepageScanner()
    return _scanner


def start_homepage_scanner():
    """启动主页扫描器"""
    scanner = get_scanner()
    scanner.start()


def stop_homepage_scanner():
    """停止主页扫描器"""
    scanner = get_scanner()
    scanner.stop()


def is_scanner_running() -> bool:
    """检查扫描器是否运行中"""
    scanner = get_scanner()
    return scanner.is_running()
