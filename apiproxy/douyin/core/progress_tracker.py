#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
实时进度跟踪系统
支持WebSocket推送和进度监控
"""

import asyncio
import json
import time
import logging
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass, field, asdict
from enum import Enum
from datetime import datetime

logger = logging.getLogger(__name__)

# 动态导入WebSocket支持
try:
    import websockets
    from websockets.server import WebSocketServerProtocol
    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False
    logger.warning("websockets未安装，WebSocket功能不可用")


class EventType(Enum):
    """事件类型"""
    TASK_ADDED = "task_added"
    TASK_STARTED = "task_started"
    TASK_PROGRESS = "task_progress"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_RETRYING = "task_retrying"
    STATS_UPDATE = "stats_update"
    SPEED_UPDATE = "speed_update"
    ERROR = "error"
    INFO = "info"


@dataclass
class ProgressEvent:
    """进度事件"""
    event_type: EventType
    task_id: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'event_type': self.event_type.value,
            'task_id': self.task_id,
            'data': self.data,
            'timestamp': self.timestamp
        }
    
    def to_json(self) -> str:
        """转换为JSON"""
        return json.dumps(self.to_dict())


@dataclass
class TaskProgress:
    """任务进度信息"""
    task_id: str
    url: str
    status: str
    progress: float = 0.0  # 0-100
    downloaded_bytes: int = 0
    total_bytes: int = 0
    speed: float = 0.0  # bytes/second
    eta: float = 0.0  # seconds
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    error_message: Optional[str] = None
    
    def get_duration(self) -> float:
        """获取耗时"""
        if self.end_time:
            return self.end_time - self.start_time
        return time.time() - self.start_time
    
    def update_progress(self, downloaded: int, total: int):
        """更新进度"""
        self.downloaded_bytes = downloaded
        self.total_bytes = total
        
        if total > 0:
            self.progress = (downloaded / total) * 100
        
        # 计算速度
        duration = self.get_duration()
        if duration > 0:
            self.speed = downloaded / duration
        
        # 计算剩余时间
        if self.speed > 0 and total > downloaded:
            self.eta = (total - downloaded) / self.speed
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'task_id': self.task_id,
            'url': self.url,
            'status': self.status,
            'progress': round(self.progress, 2),
            'downloaded_bytes': self.downloaded_bytes,
            'total_bytes': self.total_bytes,
            'speed': round(self.speed, 2),
            'eta': round(self.eta, 2),
            'duration': round(self.get_duration(), 2),
            'error_message': self.error_message
        }


class ProgressTracker:
    """进度跟踪器"""
    
    def __init__(self, enable_websocket: bool = True, ws_port: int = 8765):
        """
        初始化进度跟踪器
        
        Args:
            enable_websocket: 是否启用WebSocket
            ws_port: WebSocket端口
        """
        self.enable_websocket = enable_websocket and WEBSOCKET_AVAILABLE
        self.ws_port = ws_port
        
        # 任务进度
        self.tasks: Dict[str, TaskProgress] = {}
        
        # 事件监听器
        self.listeners: List[Callable[[ProgressEvent], None]] = []
        
        # WebSocket连接
        self.websocket_clients: List[WebSocketServerProtocol] = []
        self.websocket_server = None
        self.ws_task = None
        
        # 统计信息
        self.stats = {
            'total_tasks': 0,
            'active_tasks': 0,
            'completed_tasks': 0,
            'failed_tasks': 0,
            'total_downloaded': 0,
            'average_speed': 0.0,
            'success_rate': 0.0
        }
        
        # 速度历史（用于计算平均速度）
        self.speed_history = []
        self.max_speed_history = 100
    
    def add_listener(self, listener: Callable[[ProgressEvent], None]):
        """添加事件监听器"""
        self.listeners.append(listener)
        logger.debug(f"添加事件监听器: {listener}")
    
    def remove_listener(self, listener: Callable[[ProgressEvent], None]):
        """移除事件监听器"""
        if listener in self.listeners:
            self.listeners.remove(listener)
    
    async def emit_event(self, event: ProgressEvent):
        """触发事件"""
        # 通知监听器
        for listener in self.listeners:
            try:
                if asyncio.iscoroutinefunction(listener):
                    await listener(event)
                else:
                    listener(event)
            except Exception as e:
                logger.error(f"事件监听器执行失败: {e}")
        
        # 推送到WebSocket客户端
        if self.websocket_clients:
            await self._broadcast_websocket(event.to_json())
    
    async def add_task(self, task_id: str, url: str):
        """添加任务"""
        self.tasks[task_id] = TaskProgress(
            task_id=task_id,
            url=url,
            status="pending"
        )
        
        self.stats['total_tasks'] += 1
        
        await self.emit_event(ProgressEvent(
            event_type=EventType.TASK_ADDED,
            task_id=task_id,
            data={'url': url}
        ))
    
    async def start_task(self, task_id: str):
        """开始任务"""
        if task_id in self.tasks:
            self.tasks[task_id].status = "processing"
            self.tasks[task_id].start_time = time.time()
            
            self.stats['active_tasks'] += 1
            
            await self.emit_event(ProgressEvent(
                event_type=EventType.TASK_STARTED,
                task_id=task_id
            ))
    
    async def update_progress(
        self,
        task_id: str,
        downloaded: int,
        total: int,
        extra_data: Optional[Dict] = None
    ):
        """更新任务进度"""
        if task_id not in self.tasks:
            return
        
        task = self.tasks[task_id]
        task.update_progress(downloaded, total)
        
        # 更新速度历史
        if task.speed > 0:
            self.speed_history.append(task.speed)
            if len(self.speed_history) > self.max_speed_history:
                self.speed_history.pop(0)
            
            # 计算平均速度
            self.stats['average_speed'] = sum(self.speed_history) / len(self.speed_history)
        
        event_data = task.to_dict()
        if extra_data:
            event_data.update(extra_data)
        
        await self.emit_event(ProgressEvent(
            event_type=EventType.TASK_PROGRESS,
            task_id=task_id,
            data=event_data
        ))
    
    async def complete_task(self, task_id: str, success: bool = True, error: Optional[str] = None):
        """完成任务"""
        if task_id not in self.tasks:
            return
        
        task = self.tasks[task_id]
        task.end_time = time.time()
        
        if success:
            task.status = "completed"
            task.progress = 100.0
            self.stats['completed_tasks'] += 1
            event_type = EventType.TASK_COMPLETED
        else:
            task.status = "failed"
            task.error_message = error
            self.stats['failed_tasks'] += 1
            event_type = EventType.TASK_FAILED
        
        self.stats['active_tasks'] = max(0, self.stats['active_tasks'] - 1)
        
        # 更新成功率
        total_finished = self.stats['completed_tasks'] + self.stats['failed_tasks']
        if total_finished > 0:
            self.stats['success_rate'] = (self.stats['completed_tasks'] / total_finished) * 100
        
        await self.emit_event(ProgressEvent(
            event_type=event_type,
            task_id=task_id,
            data=task.to_dict()
        ))
    
    async def retry_task(self, task_id: str, retry_count: int):
        """重试任务"""
        if task_id in self.tasks:
            self.tasks[task_id].status = "retrying"
            
            await self.emit_event(ProgressEvent(
                event_type=EventType.TASK_RETRYING,
                task_id=task_id,
                data={'retry_count': retry_count}
            ))
    
    async def update_stats(self):
        """更新统计信息"""
        await self.emit_event(ProgressEvent(
            event_type=EventType.STATS_UPDATE,
            data=self.stats.copy()
        ))
    
    def get_task_progress(self, task_id: str) -> Optional[TaskProgress]:
        """获取任务进度"""
        return self.tasks.get(task_id)
    
    def get_active_tasks(self) -> List[TaskProgress]:
        """获取活动任务"""
        return [
            task for task in self.tasks.values()
            if task.status in ["processing", "retrying"]
        ]
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        return self.stats.copy()
    
    def clear_completed_tasks(self):
        """清理已完成的任务"""
        completed_ids = [
            task_id for task_id, task in self.tasks.items()
            if task.status in ["completed", "failed"]
        ]
        
        for task_id in completed_ids:
            del self.tasks[task_id]
        
        logger.info(f"清理了 {len(completed_ids)} 个已完成任务")
    
    # WebSocket相关功能
    async def start_websocket_server(self):
        """启动WebSocket服务器"""
        if not self.enable_websocket:
            logger.info("WebSocket功能未启用")
            return
        
        try:
            self.websocket_server = await websockets.serve(
                self._handle_websocket,
                "localhost",
                self.ws_port
            )
            
            logger.info(f"WebSocket服务器启动在 ws://localhost:{self.ws_port}")
            
        except Exception as e:
            logger.error(f"启动WebSocket服务器失败: {e}")
    
    async def stop_websocket_server(self):
        """停止WebSocket服务器"""
        if self.websocket_server:
            self.websocket_server.close()
            await self.websocket_server.wait_closed()
            self.websocket_server = None
            logger.info("WebSocket服务器已停止")
    
    async def _handle_websocket(self, websocket: WebSocketServerProtocol, path: str):
        """处理WebSocket连接"""
        logger.info(f"新的WebSocket连接: {websocket.remote_address}")
        
        # 添加客户端
        self.websocket_clients.append(websocket)
        
        try:
            # 发送当前状态
            await websocket.send(json.dumps({
                'type': 'init',
                'data': {
                    'tasks': {
                        task_id: task.to_dict()
                        for task_id, task in self.tasks.items()
                    },
                    'stats': self.stats
                }
            }))
            
            # 保持连接
            async for message in websocket:
                try:
                    data = json.loads(message)
                    await self._handle_ws_message(websocket, data)
                except json.JSONDecodeError:
                    logger.warning(f"无效的WebSocket消息: {message}")
                    
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"WebSocket连接关闭: {websocket.remote_address}")
        finally:
            # 移除客户端
            if websocket in self.websocket_clients:
                self.websocket_clients.remove(websocket)
    
    async def _handle_ws_message(self, websocket: WebSocketServerProtocol, data: Dict):
        """处理WebSocket消息"""
        msg_type = data.get('type')
        
        if msg_type == 'ping':
            await websocket.send(json.dumps({'type': 'pong'}))
        elif msg_type == 'get_stats':
            await websocket.send(json.dumps({
                'type': 'stats',
                'data': self.stats
            }))
        elif msg_type == 'get_tasks':
            await websocket.send(json.dumps({
                'type': 'tasks',
                'data': {
                    task_id: task.to_dict()
                    for task_id, task in self.tasks.items()
                }
            }))
    
    async def _broadcast_websocket(self, message: str):
        """广播WebSocket消息"""
        if not self.websocket_clients:
            return
        
        # 并发发送给所有客户端
        disconnected = []
        
        for client in self.websocket_clients:
            try:
                await client.send(message)
            except websockets.exceptions.ConnectionClosed:
                disconnected.append(client)
        
        # 清理断开的连接
        for client in disconnected:
            self.websocket_clients.remove(client)
    
    # 上下文管理器
    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self.start_websocket_server()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.stop_websocket_server()


def create_console_listener(use_rich: bool = True):
    """
    创建控制台进度监听器
    
    Args:
        use_rich: 是否使用Rich库美化输出
    
    Returns:
        监听器函数
    """
    if use_rich:
        try:
            from rich.console import Console
            from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
            from rich.table import Table
            
            console = Console()
            progress = Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            )
            
            task_map = {}
            
            def rich_listener(event: ProgressEvent):
                if event.event_type == EventType.TASK_ADDED:
                    task_id = event.task_id
                    if task_id not in task_map:
                        task_map[task_id] = progress.add_task(
                            f"[cyan]{event.data.get('url', 'Unknown')}",
                            total=100
                        )
                
                elif event.event_type == EventType.TASK_PROGRESS:
                    task_id = event.task_id
                    if task_id in task_map:
                        progress.update(
                            task_map[task_id],
                            completed=event.data.get('progress', 0)
                        )
                
                elif event.event_type == EventType.TASK_COMPLETED:
                    console.print(f"[green]✓ 任务完成: {event.task_id}")
                
                elif event.event_type == EventType.TASK_FAILED:
                    console.print(f"[red]✗ 任务失败: {event.task_id} - {event.data.get('error_message', 'Unknown error')}")
            
            return rich_listener
            
        except ImportError:
            pass
    
    # 默认简单监听器
    def simple_listener(event: ProgressEvent):
        timestamp = datetime.fromtimestamp(event.timestamp).strftime('%H:%M:%S')
        
        if event.event_type == EventType.TASK_PROGRESS:
            progress = event.data.get('progress', 0)
            speed = event.data.get('speed', 0)
            print(f"[{timestamp}] 任务 {event.task_id}: {progress:.1f}% ({speed/1024/1024:.2f} MB/s)")
        elif event.event_type == EventType.TASK_COMPLETED:
            print(f"[{timestamp}] ✓ 任务完成: {event.task_id}")
        elif event.event_type == EventType.TASK_FAILED:
            print(f"[{timestamp}] ✗ 任务失败: {event.task_id}")
    
    return simple_listener