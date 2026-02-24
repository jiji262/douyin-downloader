#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
抖音下载策略模块
包含多种下载策略的实现
"""

from .base import IDownloadStrategy, DownloadTask, DownloadResult, TaskType, TaskStatus
from .api_strategy import EnhancedAPIStrategy
from .browser_strategy import BrowserDownloadStrategy as BrowserStrategy
from .retry_strategy import RetryStrategy

__all__ = [
    'IDownloadStrategy',
    'DownloadTask',
    'DownloadResult',
    'TaskType',
    'TaskStatus',
    'EnhancedAPIStrategy',
    'BrowserStrategy',
    'RetryStrategy'
]