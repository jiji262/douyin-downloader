"""
服务模块初始化文件
"""
from .task_manager import TaskManager, get_task_manager, DownloadMode, TaskProgress
from .vnc_manager import VNCManager, get_vnc_manager
from .downloader_adapter import DownloaderAdapter, get_downloader_adapter, WebProgressReporter

__all__ = [
    'TaskManager', 
    'get_task_manager', 
    'DownloadMode', 
    'TaskProgress',
    'VNCManager',
    'get_vnc_manager',
    'DownloaderAdapter',
    'get_downloader_adapter',
    'WebProgressReporter',
]
