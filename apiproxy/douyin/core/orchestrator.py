#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
下载编排器
协调多种下载策略，实现智能降级和任务管理
"""

import asyncio
import time
import logging
import uuid
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from enum import Enum

from apiproxy.douyin.strategies.base import (
    IDownloadStrategy, 
    DownloadTask, 
    DownloadResult,
    TaskType,
    TaskStatus
)
from apiproxy.douyin.strategies.api_strategy import EnhancedAPIStrategy
from apiproxy.douyin.strategies.retry_strategy import RetryStrategy
from .rate_limiter import AdaptiveRateLimiter, RateLimitConfig

logger = logging.getLogger(__name__)


class OrchestratorConfig:
    """编排器配置"""
    def __init__(
        self,
        max_concurrent: int = 5,
        enable_retry: bool = True,
        enable_rate_limit: bool = True,
        rate_limit_config: Optional[RateLimitConfig] = None,
        priority_queue: bool = True,
        save_progress: bool = True
    ):
        self.max_concurrent = max_concurrent
        self.enable_retry = enable_retry
        self.enable_rate_limit = enable_rate_limit
        self.rate_limit_config = rate_limit_config or RateLimitConfig()
        self.priority_queue = priority_queue
        self.save_progress = save_progress


class DownloadOrchestrator:
    """下载任务编排器"""
    
    def __init__(self, config: Optional[OrchestratorConfig] = None):
        """
        初始化编排器
        
        Args:
            config: 编排器配置
        """
        self.config = config or OrchestratorConfig()
        self.strategies: List[IDownloadStrategy] = []
        self.rate_limiter = AdaptiveRateLimiter(self.config.rate_limit_config) if self.config.enable_rate_limit else None
        
        # 任务队列
        self.pending_queue = asyncio.Queue()
        self.priority_tasks: List[DownloadTask] = []
        self.active_tasks: Dict[str, DownloadTask] = {}
        self.completed_tasks: List[DownloadTask] = []
        self.failed_tasks: List[DownloadTask] = []
        
        # 工作线程
        self.workers: List[asyncio.Task] = []
        self.running = False
        
        # 统计信息
        self.stats = {
            'total_tasks': 0,
            'completed_tasks': 0,
            'failed_tasks': 0,
            'retried_tasks': 0,
            'average_duration': 0.0,
            'success_rate': 0.0
        }
        
        # 初始化默认策略
        self._init_default_strategies()
    
    def _init_default_strategies(self):
        """初始化默认策略"""
        # API策略
        api_strategy = EnhancedAPIStrategy()
        
        # 如果启用重试，包装策略
        if self.config.enable_retry:
            api_strategy = RetryStrategy(api_strategy)
        
        self.register_strategy(api_strategy)
    
    def register_strategy(self, strategy: IDownloadStrategy):
        """
        注册下载策略
        
        Args:
            strategy: 下载策略实例
        """
        self.strategies.append(strategy)
        # 按优先级排序
        self.strategies.sort(key=lambda s: s.get_priority(), reverse=True)
        logger.info(f"注册策略: {strategy.name} (优先级: {strategy.get_priority()})")
    
    async def add_task(self, url: str, task_type: Optional[TaskType] = None, priority: int = 0) -> str:
        """
        添加下载任务
        
        Args:
            url: 下载URL
            task_type: 任务类型
            priority: 优先级（数值越大优先级越高）
        
        Returns:
            任务ID
        """
        # 自动识别任务类型
        if task_type is None:
            task_type = self._detect_task_type(url)
        
        # 创建任务
        task = DownloadTask(
            task_id=str(uuid.uuid4()),
            url=url,
            task_type=task_type,
            priority=priority
        )
        
        # 添加到队列
        if self.config.priority_queue and priority > 0:
            self.priority_tasks.append(task)
            self.priority_tasks.sort(key=lambda t: t.priority, reverse=True)
        else:
            await self.pending_queue.put(task)
        
        self.stats['total_tasks'] += 1
        logger.info(f"添加任务: {task.task_id} ({task_type.value}) 优先级: {priority}")
        
        return task.task_id
    
    async def add_batch(self, urls: List[str], task_type: Optional[TaskType] = None) -> List[str]:
        """
        批量添加任务
        
        Args:
            urls: URL列表
            task_type: 任务类型
        
        Returns:
            任务ID列表
        """
        task_ids = []
        for i, url in enumerate(urls):
            # 批量任务使用递减优先级
            priority = len(urls) - i
            task_id = await self.add_task(url, task_type, priority)
            task_ids.append(task_id)
        
        return task_ids
    
    async def start(self):
        """启动编排器"""
        if self.running:
            logger.warning("编排器已在运行")
            return
        
        self.running = True
        logger.info(f"启动编排器，最大并发数: {self.config.max_concurrent}")
        
        # 创建工作线程
        for i in range(self.config.max_concurrent):
            worker = asyncio.create_task(self._worker(i))
            self.workers.append(worker)
    
    async def stop(self):
        """停止编排器"""
        if not self.running:
            return
        
        logger.info("停止编排器...")
        self.running = False
        
        # 取消所有工作线程
        for worker in self.workers:
            worker.cancel()
        
        # 等待工作线程结束
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers.clear()
        
        logger.info("编排器已停止")
    
    async def wait_completion(self, timeout: Optional[float] = None):
        """
        等待所有任务完成
        
        Args:
            timeout: 超时时间（秒）
        """
        start_time = time.time()
        
        while self.running:
            # 检查是否所有任务都完成
            if (self.pending_queue.empty() and 
                not self.priority_tasks and 
                not self.active_tasks):
                logger.info("所有任务已完成")
                break
            
            # 检查超时
            if timeout and (time.time() - start_time) > timeout:
                logger.warning(f"等待超时 ({timeout} 秒)")
                break
            
            await asyncio.sleep(1)
        
        # 计算统计信息
        self._calculate_stats()
    
    async def _worker(self, worker_id: int):
        """
        工作线程
        
        Args:
            worker_id: 工作线程ID
        """
        logger.info(f"工作线程 {worker_id} 启动")
        
        while self.running:
            try:
                # 获取任务
                task = await self._get_next_task()
                if task is None:
                    await asyncio.sleep(0.1)
                    continue
                
                # 标记为活动任务
                self.active_tasks[task.task_id] = task
                
                # 限速控制
                if self.rate_limiter:
                    await self.rate_limiter.acquire()
                
                # 执行任务
                logger.info(f"工作线程 {worker_id} 开始处理任务: {task.task_id}")
                result = await self._execute_task(task)
                
                # 移除活动任务
                del self.active_tasks[task.task_id]
                
                # 处理结果
                if result.success:
                    self.completed_tasks.append(task)
                    self.stats['completed_tasks'] += 1
                    logger.info(f"任务 {task.task_id} 完成")
                else:
                    # 检查是否需要重试
                    if task.increment_retry():
                        logger.warning(f"任务 {task.task_id} 失败，准备重试 ({task.retry_count}/{task.max_retries})")
                        await self.pending_queue.put(task)
                        self.stats['retried_tasks'] += 1
                    else:
                        self.failed_tasks.append(task)
                        self.stats['failed_tasks'] += 1
                        logger.error(f"任务 {task.task_id} 最终失败: {result.error_message}")
                
                # 保存进度
                if self.config.save_progress:
                    await self._save_progress()
                
            except asyncio.CancelledError:
                logger.info(f"工作线程 {worker_id} 被取消")
                break
            except Exception as e:
                logger.error(f"工作线程 {worker_id} 异常: {e}")
                await asyncio.sleep(1)
        
        logger.info(f"工作线程 {worker_id} 结束")
    
    async def _get_next_task(self) -> Optional[DownloadTask]:
        """获取下一个任务"""
        # 优先处理高优先级任务
        if self.priority_tasks:
            return self.priority_tasks.pop(0)
        
        # 从普通队列获取
        try:
            return await asyncio.wait_for(
                self.pending_queue.get(),
                timeout=0.1
            )
        except asyncio.TimeoutError:
            return None
    
    async def _execute_task(self, task: DownloadTask) -> DownloadResult:
        """
        执行任务，尝试所有策略
        
        Args:
            task: 下载任务
        
        Returns:
            下载结果
        """
        last_error = None
        
        for strategy in self.strategies:
            try:
                # 检查策略是否能处理任务
                if not await strategy.can_handle(task):
                    continue
                
                logger.info(f"使用策略 {strategy.name} 处理任务 {task.task_id}")
                
                # 执行下载
                result = await strategy.download(task)
                
                if result.success:
                    return result
                
                last_error = result.error_message
                logger.warning(f"策略 {strategy.name} 失败: {last_error}")
                
            except Exception as e:
                last_error = str(e)
                logger.error(f"策略 {strategy.name} 异常: {e}")
        
        # 所有策略都失败
        return DownloadResult(
            success=False,
            task_id=task.task_id,
            error_message=f"所有策略都失败: {last_error}",
            retry_count=task.retry_count
        )
    
    def _detect_task_type(self, url: str) -> TaskType:
        """
        自动检测任务类型
        
        Args:
            url: URL
        
        Returns:
            任务类型
        """
        url_lower = url.lower()
        
        if '/user/' in url_lower:
            return TaskType.USER
        elif '/video/' in url_lower or '/note/' in url_lower:
            return TaskType.VIDEO
        elif '/music/' in url_lower:
            return TaskType.MUSIC
        elif '/mix/' in url_lower or '/collection/' in url_lower:
            return TaskType.MIX
        elif 'live.douyin.com' in url_lower:
            return TaskType.LIVE
        else:
            return TaskType.VIDEO  # 默认为视频
    
    def _calculate_stats(self):
        """计算统计信息"""
        total = self.stats['total_tasks']
        if total > 0:
            self.stats['success_rate'] = self.stats['completed_tasks'] / total * 100
        
        # 计算平均时长
        durations = []
        for task in self.completed_tasks:
            if hasattr(task, 'duration'):
                durations.append(task.duration)
        
        if durations:
            self.stats['average_duration'] = sum(durations) / len(durations)
    
    async def _save_progress(self):
        """保存进度（可扩展为持久化到文件或数据库）"""
        # TODO: 实现进度保存逻辑
        pass
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        self._calculate_stats()
        return self.stats.copy()
    
    def get_task_status(self, task_id: str) -> Optional[TaskStatus]:
        """
        获取任务状态
        
        Args:
            task_id: 任务ID
        
        Returns:
            任务状态
        """
        # 检查活动任务
        if task_id in self.active_tasks:
            return self.active_tasks[task_id].status
        
        # 检查完成任务
        for task in self.completed_tasks:
            if task.task_id == task_id:
                return TaskStatus.COMPLETED
        
        # 检查失败任务
        for task in self.failed_tasks:
            if task.task_id == task_id:
                return TaskStatus.FAILED
        
        # 检查待处理任务
        for task in self.priority_tasks:
            if task.task_id == task_id:
                return TaskStatus.PENDING
        
        return None