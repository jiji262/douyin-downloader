#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
智能限速器
防止请求过快导致被封禁
"""

import asyncio
import time
import logging
from collections import deque
from typing import Optional, Dict, Any
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class RateLimitStrategy(Enum):
    """限速策略枚举"""
    FIXED = "fixed"          # 固定速率
    ADAPTIVE = "adaptive"    # 自适应速率
    BURST = "burst"          # 突发模式


@dataclass
class RateLimitConfig:
    """限速配置"""
    max_per_second: int = 2
    max_per_minute: int = 30
    max_per_hour: int = 1000
    burst_size: int = 5
    strategy: RateLimitStrategy = RateLimitStrategy.ADAPTIVE
    cooldown_time: int = 60  # 触发限制后的冷却时间（秒）


class AdaptiveRateLimiter:
    """自适应限速器"""
    
    def __init__(self, config: Optional[RateLimitConfig] = None):
        """
        初始化限速器
        
        Args:
            config: 限速配置
        """
        self.config = config or RateLimitConfig()
        self.requests = deque()
        self.failures = deque()
        self.lock = asyncio.Lock()
        
        # 当前限制值（可动态调整）
        self.current_max_per_second = self.config.max_per_second
        self.current_max_per_minute = self.config.max_per_minute
        self.current_max_per_hour = self.config.max_per_hour
        
        # 统计信息
        self.stats = {
            'total_requests': 0,
            'blocked_requests': 0,
            'rate_adjustments': 0,
            'current_rate': self.current_max_per_second,
            'failure_rate': 0.0
        }
        
        # 冷却状态
        self.cooldown_until = 0
        
    async def acquire(self) -> bool:
        """
        获取请求许可
        
        Returns:
            是否获得许可
        """
        async with self.lock:
            now = time.time()
            
            # 检查是否在冷却期
            if self.cooldown_until > now:
                remaining = self.cooldown_until - now
                logger.warning(f"限速器处于冷却期，还需等待 {remaining:.1f} 秒")
                await asyncio.sleep(remaining)
                self.cooldown_until = 0
            
            # 清理过期记录
            self._clean_old_records(now)
            
            # 检查速率限制
            while not self._can_proceed(now):
                # 计算需要等待的时间
                wait_time = self._calculate_wait_time(now)
                if wait_time > 0:
                    logger.debug(f"速率限制，等待 {wait_time:.2f} 秒")
                    await asyncio.sleep(wait_time)
                    now = time.time()
                    self._clean_old_records(now)
                else:
                    # 无法继续，记录被阻塞的请求
                    self.stats['blocked_requests'] += 1
                    return False
            
            # 记录请求
            self.requests.append(now)
            self.stats['total_requests'] += 1
            
            # 自适应调整
            if self.config.strategy == RateLimitStrategy.ADAPTIVE:
                self._adjust_rate()
            
            return True
    
    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self.acquire()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        if exc_type is not None:
            # 发生异常，记录失败
            self.record_failure()
    
    def record_failure(self):
        """记录失败请求"""
        now = time.time()
        self.failures.append(now)
        
        # 自适应调整
        if self.config.strategy == RateLimitStrategy.ADAPTIVE:
            self._handle_failure()
    
    def _can_proceed(self, now: float) -> bool:
        """检查是否可以继续请求"""
        # 检查每秒限制
        recent_second = [r for r in self.requests if now - r < 1]
        if len(recent_second) >= self.current_max_per_second:
            return False
        
        # 检查每分钟限制
        recent_minute = [r for r in self.requests if now - r < 60]
        if len(recent_minute) >= self.current_max_per_minute:
            return False
        
        # 检查每小时限制
        recent_hour = [r for r in self.requests if now - r < 3600]
        if len(recent_hour) >= self.current_max_per_hour:
            return False
        
        # 突发模式检查
        if self.config.strategy == RateLimitStrategy.BURST:
            recent_burst = [r for r in self.requests if now - r < 0.1]
            if len(recent_burst) >= self.config.burst_size:
                return False
        
        return True
    
    def _calculate_wait_time(self, now: float) -> float:
        """计算需要等待的时间"""
        wait_times = []
        
        # 计算每秒限制的等待时间
        recent_second = [r for r in self.requests if now - r < 1]
        if len(recent_second) >= self.current_max_per_second:
            oldest_in_second = min(recent_second)
            wait_times.append(1 - (now - oldest_in_second))
        
        # 计算每分钟限制的等待时间
        recent_minute = [r for r in self.requests if now - r < 60]
        if len(recent_minute) >= self.current_max_per_minute:
            oldest_in_minute = min(recent_minute)
            wait_times.append(60 - (now - oldest_in_minute))
        
        # 返回最小等待时间
        return min(wait_times) if wait_times else 0.1
    
    def _clean_old_records(self, now: float):
        """清理过期记录"""
        # 保留最近1小时的请求记录
        while self.requests and now - self.requests[0] > 3600:
            self.requests.popleft()
        
        # 保留最近10分钟的失败记录
        while self.failures and now - self.failures[0] > 600:
            self.failures.popleft()
    
    def _adjust_rate(self):
        """自适应调整速率"""
        now = time.time()
        
        # 计算失败率
        recent_failures = [f for f in self.failures if now - f < 60]
        recent_requests = [r for r in self.requests if now - r < 60]
        
        if len(recent_requests) > 10:
            failure_rate = len(recent_failures) / len(recent_requests)
            self.stats['failure_rate'] = failure_rate
            
            if failure_rate > 0.3:
                # 失败率过高，降低速率
                self._decrease_rate()
            elif failure_rate < 0.05 and len(recent_requests) > 20:
                # 失败率很低，尝试提高速率
                self._increase_rate()
    
    def _handle_failure(self):
        """处理失败，调整限速策略"""
        now = time.time()
        recent_failures = [f for f in self.failures if now - f < 10]
        
        # 如果短时间内失败次数过多，触发冷却
        if len(recent_failures) >= 5:
            logger.warning(f"检测到频繁失败，进入冷却期 {self.config.cooldown_time} 秒")
            self.cooldown_until = now + self.config.cooldown_time
            self._decrease_rate()
    
    def _decrease_rate(self):
        """降低请求速率"""
        old_rate = self.current_max_per_second
        
        self.current_max_per_second = max(1, int(self.current_max_per_second * 0.7))
        self.current_max_per_minute = max(10, int(self.current_max_per_minute * 0.7))
        self.current_max_per_hour = max(100, int(self.current_max_per_hour * 0.7))
        
        if old_rate != self.current_max_per_second:
            self.stats['rate_adjustments'] += 1
            self.stats['current_rate'] = self.current_max_per_second
            logger.info(f"降低请求速率: {old_rate}/s -> {self.current_max_per_second}/s")
    
    def _increase_rate(self):
        """提高请求速率"""
        old_rate = self.current_max_per_second
        
        # 不超过配置的最大值
        self.current_max_per_second = min(
            self.config.max_per_second,
            int(self.current_max_per_second * 1.2)
        )
        self.current_max_per_minute = min(
            self.config.max_per_minute,
            int(self.current_max_per_minute * 1.2)
        )
        self.current_max_per_hour = min(
            self.config.max_per_hour,
            int(self.current_max_per_hour * 1.2)
        )
        
        if old_rate != self.current_max_per_second:
            self.stats['rate_adjustments'] += 1
            self.stats['current_rate'] = self.current_max_per_second
            logger.info(f"提高请求速率: {old_rate}/s -> {self.current_max_per_second}/s")
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return self.stats.copy()
    
    def reset_stats(self):
        """重置统计信息"""
        self.stats = {
            'total_requests': 0,
            'blocked_requests': 0,
            'rate_adjustments': 0,
            'current_rate': self.current_max_per_second,
            'failure_rate': 0.0
        }
    
    def set_cooldown(self, seconds: int):
        """手动设置冷却时间"""
        self.cooldown_until = time.time() + seconds
        logger.info(f"手动设置冷却期 {seconds} 秒")


class SimpleRateLimiter:
    """简单限速器（固定速率）"""
    
    def __init__(self, requests_per_second: float = 1.0):
        """
        初始化简单限速器
        
        Args:
            requests_per_second: 每秒允许的请求数
        """
        self.requests_per_second = requests_per_second
        self.min_interval = 1.0 / requests_per_second
        self.last_request_time = 0
        self.lock = asyncio.Lock()
    
    async def acquire(self):
        """获取请求许可"""
        async with self.lock:
            now = time.time()
            time_since_last = now - self.last_request_time
            
            if time_since_last < self.min_interval:
                sleep_time = self.min_interval - time_since_last
                await asyncio.sleep(sleep_time)
            
            self.last_request_time = time.time()
    
    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self.acquire()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        pass