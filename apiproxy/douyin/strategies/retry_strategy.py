#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
智能重试策略
包装其他策略并提供智能重试机制
"""

import asyncio
import time
import logging
from typing import Optional, List
from functools import wraps

from .base import IDownloadStrategy, DownloadTask, DownloadResult, TaskStatus

logger = logging.getLogger(__name__)


class RetryStrategy(IDownloadStrategy):
    """智能重试策略，包装其他策略并提供重试机制"""
    
    def __init__(
        self,
        strategy: IDownloadStrategy,
        max_retries: int = 3,
        retry_delays: Optional[List[float]] = None,
        exponential_backoff: bool = True
    ):
        """
        初始化重试策略
        
        Args:
            strategy: 被包装的策略
            max_retries: 最大重试次数
            retry_delays: 自定义重试延迟列表
            exponential_backoff: 是否使用指数退避
        """
        self.strategy = strategy
        self.max_retries = max_retries
        self.retry_delays = retry_delays or [1, 2, 5, 10, 30]
        self.exponential_backoff = exponential_backoff
        self.retry_stats = {
            'total_retries': 0,
            'successful_retries': 0,
            'failed_retries': 0
        }
    
    @property
    def name(self) -> str:
        return f"Retry({self.strategy.name})"
    
    def get_priority(self) -> int:
        """继承被包装策略的优先级"""
        return self.strategy.get_priority()
    
    async def can_handle(self, task: DownloadTask) -> bool:
        """判断是否可以处理任务"""
        return await self.strategy.can_handle(task)
    
    async def download(self, task: DownloadTask) -> DownloadResult:
        """执行下载任务，带重试机制"""
        original_retry_count = task.retry_count
        last_error = None
        
        for attempt in range(self.max_retries):
            try:
                # 更新任务状态
                if attempt > 0:
                    task.status = TaskStatus.RETRYING
                    logger.info(f"任务 {task.task_id} 第 {attempt + 1}/{self.max_retries} 次重试")
                
                # 执行下载
                result = await self.strategy.download(task)
                
                if result.success:
                    if attempt > 0:
                        self.retry_stats['successful_retries'] += 1
                        logger.info(f"任务 {task.task_id} 重试成功 (第 {attempt + 1} 次)")
                    return result
                
                # 下载失败，准备重试
                last_error = result.error_message
                
                # 检查是否应该重试
                if not self._should_retry(result, attempt):
                    logger.warning(f"任务 {task.task_id} 不符合重试条件，停止重试")
                    return result
                
                # 计算延迟时间
                delay = self._calculate_delay(attempt)
                logger.info(f"任务 {task.task_id} 将在 {delay} 秒后重试")
                await asyncio.sleep(delay)
                
                # 增加重试计数
                task.retry_count += 1
                self.retry_stats['total_retries'] += 1
                
            except Exception as e:
                last_error = str(e)
                logger.error(f"任务 {task.task_id} 执行异常: {e}")
                
                if attempt < self.max_retries - 1:
                    delay = self._calculate_delay(attempt)
                    logger.info(f"任务 {task.task_id} 将在 {delay} 秒后重试")
                    await asyncio.sleep(delay)
                    task.retry_count += 1
                    self.retry_stats['total_retries'] += 1
                else:
                    self.retry_stats['failed_retries'] += 1
                    break
        
        # 所有重试都失败
        task.status = TaskStatus.FAILED
        self.retry_stats['failed_retries'] += 1
        
        return DownloadResult(
            success=False,
            task_id=task.task_id,
            error_message=f"重试 {self.max_retries} 次后仍然失败: {last_error}",
            retry_count=task.retry_count
        )
    
    def _should_retry(self, result: DownloadResult, attempt: int) -> bool:
        """判断是否应该重试"""
        # 如果已经达到最大重试次数，不重试
        if attempt >= self.max_retries - 1:
            return False
        
        # 如果没有错误消息，可能是未知错误，应该重试
        if not result.error_message:
            return True
        
        # 检查是否是可重试的错误
        retryable_errors = [
            'timeout',
            'connection',
            'network',
            '429',  # Too Many Requests
            '503',  # Service Unavailable
            '502',  # Bad Gateway
            '504',  # Gateway Timeout
            '空响应',
            '返回空',
            'empty response',
            'temporary'
        ]
        
        error_lower = result.error_message.lower()
        for error in retryable_errors:
            if error in error_lower:
                return True
        
        # 检查是否是不可重试的错误
        non_retryable_errors = [
            '404',  # Not Found
            '403',  # Forbidden
            '401',  # Unauthorized
            'invalid',
            'not found',
            'deleted',
            '已删除',
            '不存在'
        ]
        
        for error in non_retryable_errors:
            if error in error_lower:
                return False
        
        # 默认重试
        return True
    
    def _calculate_delay(self, attempt: int) -> float:
        """计算重试延迟时间"""
        if self.exponential_backoff:
            # 指数退避：2^attempt 秒，最大30秒
            delay = min(2 ** attempt, 30)
        else:
            # 使用预定义的延迟列表
            if attempt < len(self.retry_delays):
                delay = self.retry_delays[attempt]
            else:
                delay = self.retry_delays[-1]
        
        # 添加一些随机性以避免同时重试
        import random
        jitter = random.uniform(0, 0.3 * delay)
        
        return delay + jitter
    
    def get_stats(self) -> dict:
        """获取重试统计信息"""
        return self.retry_stats.copy()
    
    def reset_stats(self):
        """重置统计信息"""
        self.retry_stats = {
            'total_retries': 0,
            'successful_retries': 0,
            'failed_retries': 0
        }


def with_retry(
    max_retries: int = 3,
    retry_delays: Optional[List[float]] = None,
    exponential_backoff: bool = True
):
    """
    装饰器：为异步函数添加重试机制
    
    Usage:
        @with_retry(max_retries=3)
        async def download_file(url):
            ...
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            delays = retry_delays or [1, 2, 5, 10, 30]
            
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    
                    if attempt < max_retries - 1:
                        if exponential_backoff:
                            delay = min(2 ** attempt, 30)
                        else:
                            delay = delays[attempt] if attempt < len(delays) else delays[-1]
                        
                        logger.warning(f"函数 {func.__name__} 失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                        logger.info(f"将在 {delay} 秒后重试")
                        await asyncio.sleep(delay)
                    else:
                        logger.error(f"函数 {func.__name__} 重试 {max_retries} 次后仍然失败")
            
            raise last_exception
        
        return wrapper
    return decorator