#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
下载策略基础类和接口定义
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from enum import Enum
import time


class TaskType(Enum):
    """任务类型枚举"""
    VIDEO = "video"
    IMAGE = "image"
    MUSIC = "music"
    USER = "user"
    MIX = "mix"
    LIVE = "live"


class TaskStatus(Enum):
    """任务状态枚举"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"


@dataclass
class DownloadTask:
    """下载任务数据类"""
    task_id: str
    url: str
    task_type: TaskType
    priority: int = 0
    retry_count: int = 0
    max_retries: int = 3
    status: TaskStatus = TaskStatus.PENDING
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    error_message: Optional[str] = None
    
    def increment_retry(self) -> bool:
        """增加重试次数，返回是否还能重试"""
        self.retry_count += 1
        self.updated_at = time.time()
        return self.retry_count < self.max_retries
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'task_id': self.task_id,
            'url': self.url,
            'task_type': self.task_type.value,
            'priority': self.priority,
            'retry_count': self.retry_count,
            'max_retries': self.max_retries,
            'status': self.status.value,
            'metadata': self.metadata,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'error_message': self.error_message
        }


@dataclass
class DownloadResult:
    """下载结果数据类"""
    success: bool
    task_id: str
    file_paths: List[str] = field(default_factory=list)
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    duration: float = 0.0
    retry_count: int = 0
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'success': self.success,
            'task_id': self.task_id,
            'file_paths': self.file_paths,
            'error_message': self.error_message,
            'metadata': self.metadata,
            'duration': self.duration,
            'retry_count': self.retry_count
        }


class IDownloadStrategy(ABC):
    """下载策略抽象基类"""
    
    @abstractmethod
    async def can_handle(self, task: DownloadTask) -> bool:
        """判断是否可以处理该任务"""
        pass
    
    @abstractmethod
    async def download(self, task: DownloadTask) -> DownloadResult:
        """执行下载任务"""
        pass
    
    @abstractmethod
    def get_priority(self) -> int:
        """获取策略优先级，数值越大优先级越高"""
        pass
    
    @property
    @abstractmethod
    def name(self) -> str:
        """策略名称"""
        pass
    
    def __str__(self) -> str:
        return f"{self.name} (Priority: {self.get_priority()})"