#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
队列管理器
支持任务持久化和断点恢复
"""

import asyncio
import json
import sqlite3
import time
import logging
import pickle
from typing import List, Dict, Optional, Any
from pathlib import Path
from dataclasses import dataclass, asdict
from enum import Enum

from apiproxy.douyin.strategies.base import DownloadTask, TaskStatus, TaskType

logger = logging.getLogger(__name__)


class PersistentQueue:
    """持久化队列管理器"""
    
    def __init__(
        self,
        db_path: str = "download_queue.db",
        max_size: int = 10000,
        checkpoint_interval: int = 60
    ):
        """
        初始化队列管理器
        
        Args:
            db_path: 数据库文件路径
            max_size: 队列最大容量
            checkpoint_interval: 检查点保存间隔（秒）
        """
        self.db_path = Path(db_path)
        self.max_size = max_size
        self.checkpoint_interval = checkpoint_interval
        
        self.conn: Optional[sqlite3.Connection] = None
        self.queue = asyncio.Queue(maxsize=max_size)
        self._checkpoint_task = None
        self._lock = asyncio.Lock()
        
        # 初始化数据库
        self._init_database()
        
        # 恢复未完成的任务
        self._restore_tasks()
    
    def _init_database(self):
        """初始化数据库"""
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        cursor = self.conn.cursor()
        
        # 创建任务表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                task_type TEXT NOT NULL,
                priority INTEGER DEFAULT 0,
                status TEXT NOT NULL,
                retry_count INTEGER DEFAULT 0,
                max_retries INTEGER DEFAULT 3,
                metadata TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                completed_at REAL,
                error_message TEXT,
                result TEXT
            )
        ''')
        
        # 创建索引
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_status ON tasks(status)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_priority ON tasks(priority DESC)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_created_at ON tasks(created_at)')
        
        # 创建进度表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                total_tasks INTEGER,
                pending_tasks INTEGER,
                active_tasks INTEGER,
                completed_tasks INTEGER,
                failed_tasks INTEGER,
                success_rate REAL,
                average_duration REAL
            )
        ''')
        
        self.conn.commit()
        logger.info(f"数据库初始化完成: {self.db_path}")
    
    def _restore_tasks(self):
        """从数据库恢复未完成的任务"""
        cursor = self.conn.cursor()
        
        # 将所有PROCESSING状态的任务重置为PENDING
        cursor.execute('''
            UPDATE tasks 
            SET status = ?, updated_at = ?
            WHERE status = ?
        ''', (TaskStatus.PENDING.value, time.time(), TaskStatus.PROCESSING.value))
        
        # 获取所有待处理的任务
        cursor.execute('''
            SELECT task_id, url, task_type, priority, retry_count, max_retries, metadata, created_at
            FROM tasks
            WHERE status IN (?, ?)
            ORDER BY priority DESC, created_at ASC
        ''', (TaskStatus.PENDING.value, TaskStatus.RETRYING.value))
        
        restored_count = 0
        for row in cursor.fetchall():
            task = self._row_to_task(row)
            if task:
                try:
                    self.queue.put_nowait(task)
                    restored_count += 1
                except asyncio.QueueFull:
                    break
        
        self.conn.commit()
        
        if restored_count > 0:
            logger.info(f"从数据库恢复了 {restored_count} 个未完成任务")
    
    def _row_to_task(self, row: tuple) -> Optional[DownloadTask]:
        """将数据库行转换为任务对象"""
        try:
            task_id, url, task_type, priority, retry_count, max_retries, metadata_str, created_at = row
            
            metadata = {}
            if metadata_str:
                try:
                    metadata = json.loads(metadata_str)
                except:
                    pass
            
            return DownloadTask(
                task_id=task_id,
                url=url,
                task_type=TaskType(task_type),
                priority=priority,
                retry_count=retry_count,
                max_retries=max_retries,
                metadata=metadata,
                created_at=created_at
            )
        except Exception as e:
            logger.error(f"转换任务失败: {e}")
            return None
    
    async def add_task(self, task: DownloadTask) -> bool:
        """
        添加任务到队列
        
        Args:
            task: 下载任务
        
        Returns:
            是否成功添加
        """
        async with self._lock:
            try:
                # 保存到数据库
                cursor = self.conn.cursor()
                cursor.execute('''
                    INSERT OR REPLACE INTO tasks (
                        task_id, url, task_type, priority, status, 
                        retry_count, max_retries, metadata, 
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    task.task_id,
                    task.url,
                    task.task_type.value,
                    task.priority,
                    task.status.value,
                    task.retry_count,
                    task.max_retries,
                    json.dumps(task.metadata),
                    task.created_at,
                    task.updated_at
                ))
                self.conn.commit()
                
                # 添加到内存队列
                await self.queue.put(task)
                
                logger.debug(f"任务 {task.task_id} 已添加到队列")
                return True
                
            except Exception as e:
                logger.error(f"添加任务失败: {e}")
                return False
    
    async def get_task(self, timeout: float = 1.0) -> Optional[DownloadTask]:
        """
        从队列获取任务
        
        Args:
            timeout: 超时时间
        
        Returns:
            下载任务
        """
        try:
            task = await asyncio.wait_for(self.queue.get(), timeout=timeout)
            
            # 更新数据库状态
            await self.update_task_status(task.task_id, TaskStatus.PROCESSING)
            
            return task
            
        except asyncio.TimeoutError:
            return None
    
    async def update_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        error_message: Optional[str] = None,
        result: Optional[Dict] = None
    ):
        """
        更新任务状态
        
        Args:
            task_id: 任务ID
            status: 新状态
            error_message: 错误信息
            result: 执行结果
        """
        async with self._lock:
            cursor = self.conn.cursor()
            
            update_fields = {
                'status': status.value,
                'updated_at': time.time()
            }
            
            if error_message:
                update_fields['error_message'] = error_message
            
            if result:
                update_fields['result'] = json.dumps(result)
            
            if status == TaskStatus.COMPLETED:
                update_fields['completed_at'] = time.time()
            
            # 构建UPDATE语句
            set_clause = ', '.join([f'{k} = ?' for k in update_fields.keys()])
            values = list(update_fields.values()) + [task_id]
            
            cursor.execute(
                f'UPDATE tasks SET {set_clause} WHERE task_id = ?',
                values
            )
            self.conn.commit()
    
    async def requeue_task(self, task: DownloadTask):
        """
        将任务重新加入队列
        
        Args:
            task: 下载任务
        """
        task.retry_count += 1
        task.status = TaskStatus.RETRYING
        task.updated_at = time.time()
        
        await self.add_task(task)
        logger.info(f"任务 {task.task_id} 重新加入队列 (重试 {task.retry_count}/{task.max_retries})")
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取队列统计信息"""
        cursor = self.conn.cursor()
        
        # 统计各状态任务数
        cursor.execute('''
            SELECT status, COUNT(*) 
            FROM tasks 
            GROUP BY status
        ''')
        
        status_counts = {}
        for status, count in cursor.fetchall():
            status_counts[status] = count
        
        # 计算成功率
        total = sum(status_counts.values())
        completed = status_counts.get(TaskStatus.COMPLETED.value, 0)
        success_rate = (completed / total * 100) if total > 0 else 0
        
        # 计算平均耗时
        cursor.execute('''
            SELECT AVG(completed_at - created_at)
            FROM tasks
            WHERE status = ? AND completed_at IS NOT NULL
        ''', (TaskStatus.COMPLETED.value,))
        
        avg_duration = cursor.fetchone()[0] or 0
        
        stats = {
            'total_tasks': total,
            'pending_tasks': status_counts.get(TaskStatus.PENDING.value, 0),
            'processing_tasks': status_counts.get(TaskStatus.PROCESSING.value, 0),
            'completed_tasks': completed,
            'failed_tasks': status_counts.get(TaskStatus.FAILED.value, 0),
            'retrying_tasks': status_counts.get(TaskStatus.RETRYING.value, 0),
            'success_rate': success_rate,
            'average_duration': avg_duration,
            'queue_size': self.queue.qsize()
        }
        
        return stats
    
    async def save_progress(self):
        """保存进度到数据库"""
        stats = self.get_statistics()
        
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO progress (
                timestamp, total_tasks, pending_tasks, active_tasks,
                completed_tasks, failed_tasks, success_rate, average_duration
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            time.time(),
            stats['total_tasks'],
            stats['pending_tasks'],
            stats['processing_tasks'],
            stats['completed_tasks'],
            stats['failed_tasks'],
            stats['success_rate'],
            stats['average_duration']
        ))
        self.conn.commit()
        
        logger.debug("进度已保存")
    
    async def start_checkpoint(self):
        """启动检查点保存任务"""
        if not self._checkpoint_task:
            self._checkpoint_task = asyncio.create_task(self._checkpoint_loop())
            logger.info("检查点保存任务已启动")
    
    async def stop_checkpoint(self):
        """停止检查点保存任务"""
        if self._checkpoint_task:
            self._checkpoint_task.cancel()
            try:
                await self._checkpoint_task
            except asyncio.CancelledError:
                pass
            self._checkpoint_task = None
            logger.info("检查点保存任务已停止")
    
    async def _checkpoint_loop(self):
        """检查点保存循环"""
        while True:
            try:
                await asyncio.sleep(self.checkpoint_interval)
                await self.save_progress()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"保存检查点失败: {e}")
    
    def get_recent_progress(self, hours: int = 24) -> List[Dict]:
        """
        获取最近的进度记录
        
        Args:
            hours: 获取最近多少小时的记录
        
        Returns:
            进度记录列表
        """
        cursor = self.conn.cursor()
        since = time.time() - hours * 3600
        
        cursor.execute('''
            SELECT timestamp, total_tasks, completed_tasks, failed_tasks, success_rate
            FROM progress
            WHERE timestamp > ?
            ORDER BY timestamp DESC
            LIMIT 100
        ''', (since,))
        
        records = []
        for row in cursor.fetchall():
            records.append({
                'timestamp': row[0],
                'total_tasks': row[1],
                'completed_tasks': row[2],
                'failed_tasks': row[3],
                'success_rate': row[4]
            })
        
        return records
    
    def cleanup_old_tasks(self, days: int = 7):
        """
        清理旧任务记录
        
        Args:
            days: 保留最近多少天的记录
        """
        cursor = self.conn.cursor()
        cutoff = time.time() - days * 86400
        
        cursor.execute('''
            DELETE FROM tasks
            WHERE status IN (?, ?) AND updated_at < ?
        ''', (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value, cutoff))
        
        deleted = cursor.rowcount
        self.conn.commit()
        
        if deleted > 0:
            logger.info(f"清理了 {deleted} 条旧任务记录")
    
    def export_tasks(self, status: Optional[TaskStatus] = None) -> List[Dict]:
        """
        导出任务列表
        
        Args:
            status: 筛选状态
        
        Returns:
            任务列表
        """
        cursor = self.conn.cursor()
        
        if status:
            cursor.execute('''
                SELECT * FROM tasks WHERE status = ?
                ORDER BY created_at DESC
            ''', (status.value,))
        else:
            cursor.execute('''
                SELECT * FROM tasks
                ORDER BY created_at DESC
            ''')
        
        tasks = []
        columns = [desc[0] for desc in cursor.description]
        
        for row in cursor.fetchall():
            task_dict = dict(zip(columns, row))
            # 解析JSON字段
            if task_dict.get('metadata'):
                try:
                    task_dict['metadata'] = json.loads(task_dict['metadata'])
                except:
                    pass
            if task_dict.get('result'):
                try:
                    task_dict['result'] = json.loads(task_dict['result'])
                except:
                    pass
            tasks.append(task_dict)
        
        return tasks
    
    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
            self.conn = None
            logger.info("数据库连接已关闭")
    
    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self.start_checkpoint()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.stop_checkpoint()
        await self.save_progress()
        self.close()