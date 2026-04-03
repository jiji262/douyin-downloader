"""
下载任务管理服务
"""
import threading
import time
from typing import Dict, List, Optional, Callable
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum


class DownloadMode(Enum):
    """下载模式"""
    FAST = "fast"      # 急速
    BALANCE = "balance"  # 平衡
    STABLE = "stable"   # 稳定


@dataclass
class TaskProgress:
    """任务进度信息"""
    task_id: str
    mode: str  # video 或 homepage
    status: str  # pending, running, completed, failed, cancelled
    total: int = 0
    current: int = 0
    percent: float = 0.0
    speed: str = ""
    current_item: str = ""
    error_message: str = ""
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    
    def to_dict(self) -> dict:
        return {
            'task_id': self.task_id,
            'mode': self.mode,
            'status': self.status,
            'total': self.total,
            'current': self.current,
            'percent': round(self.percent, 2),
            'speed': self.speed,
            'current_item': self.current_item,
            'error_message': self.error_message,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'end_time': self.end_time.isoformat() if self.end_time else None,
        }


class TaskManager:
    """任务管理器 - 管理视频下载任务和主页扫描任务"""
    
    def __init__(self):
        self._tasks: Dict[str, TaskProgress] = {}
        self._lock = threading.Lock()
        self._video_tasks: Dict[str, threading.Thread] = {}
        self._homepage_task: Optional[threading.Thread] = None
        self._homepage_running = False
        self._stop_flag = False
    
    def create_video_task(self, task_id: str, urls: List[str]) -> TaskProgress:
        """创建视频下载任务"""
        with self._lock:
            task = TaskProgress(
                task_id=task_id,
                mode='video',
                status='pending',
                total=len(urls),
                current=0,
                percent=0.0,
            )
            self._tasks[task_id] = task
            return task
    
    def update_task_progress(self, task_id: str, current: int = None, 
                            percent: float = None, speed: str = None,
                            current_item: str = None, error_message: str = None,
                            status: str = None):
        """更新任务进度"""
        with self._lock:
            if task_id not in self._tasks:
                return
            
            task = self._tasks[task_id]
            
            if current is not None:
                task.current = current
            
            if percent is not None:
                task.percent = percent
            
            if speed is not None:
                task.speed = speed
            
            if current_item is not None:
                task.current_item = current_item
            
            if error_message is not None:
                task.error_message = error_message
            
            if status is not None:
                task.status = status
                if status == 'running' and not task.start_time:
                    task.start_time = datetime.now()
                elif status in ['completed', 'failed', 'cancelled'] and not task.end_time:
                    task.end_time = datetime.now()
    
    def get_task(self, task_id: str) -> Optional[TaskProgress]:
        """获取任务进度"""
        with self._lock:
            return self._tasks.get(task_id)
    
    def get_all_tasks(self) -> List[TaskProgress]:
        """获取所有任务"""
        with self._lock:
            return list(self._tasks.values())
    
    def cancel_task(self, task_id: str) -> bool:
        """取消任务"""
        with self._lock:
            if task_id not in self._tasks:
                return False
            
            task = self._tasks[task_id]
            if task.status in ['completed', 'failed', 'cancelled']:
                return False
            
            task.status = 'cancelled'
            task.end_time = datetime.now()
            return True
    
    def start_homepage_scan(self, thread: threading.Thread):
        """启动主页扫描任务"""
        with self._lock:
            if self._homepage_running:
                return False
            
            self._homepage_running = True
            self._stop_flag = False
            self._homepage_task = thread
            return True
    
    def stop_homepage_scan(self):
        """停止主页扫描任务"""
        self._stop_flag = True
        self._homepage_running = False
    
    def is_homepage_running(self) -> bool:
        """检查主页扫描是否运行中"""
        return self._homepage_running
    
    def should_stop(self) -> bool:
        """检查是否应该停止"""
        return self._stop_flag
    
    def create_homepage_task(self, total_homepages: int) -> TaskProgress:
        """创建主页扫描任务进度"""
        with self._lock:
            task = TaskProgress(
                task_id='homepage_scanner',
                mode='homepage',
                status='running',
                total=total_homepages,
                current=0,
                percent=0.0,
                start_time=datetime.now(),
            )
            self._tasks['homepage_scanner'] = task
            return task
    
    def update_homepage_progress(self, current_homepage: int, total_videos: int = 0,
                                 downloaded_videos: int = 0, current_nickname: str = ""):
        """更新主页扫描进度"""
        with self._lock:
            if 'homepage_scanner' not in self._tasks:
                return
            
            task = self._tasks['homepage_scanner']
            task.current = current_homepage
            if task.total > 0:
                task.percent = (current_homepage / task.total) * 100
            
            task.current_item = f"正在处理：{current_nickname}" if current_nickname else ""
            task.speed = f"已发现视频：{total_videos}, 已下载：{downloaded_videos}"
    
    def complete_homepage_task(self, status: str = 'completed', error_message: str = ""):
        """完成主页扫描任务"""
        with self._lock:
            if 'homepage_scanner' not in self._tasks:
                return
            
            task = self._tasks['homepage_scanner']
            task.status = status
            task.end_time = datetime.now()
            task.percent = 100.0
            if error_message:
                task.error_message = error_message


# 全局任务管理器实例
_task_manager: Optional[TaskManager] = None


def get_task_manager() -> TaskManager:
    """获取全局任务管理器实例"""
    global _task_manager
    if _task_manager is None:
        _task_manager = TaskManager()
    return _task_manager
