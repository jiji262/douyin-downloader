"""
数据库模型定义
"""
import sqlite3
from datetime import datetime
from typing import List, Dict, Optional
from pathlib import Path


class DatabaseManager:
    """数据库管理类 - 用于存储错误日志和下载记录"""
    
    def __init__(self, db_path: str = "web_data.db"):
        self.db_path = db_path
        self._init_db()
    
    def _get_connection(self):
        """获取数据库连接"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _init_db(self):
        """初始化数据库表"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # 错误日志表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS error_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                source TEXT,
                details TEXT
            )
        ''')
        
        # 下载记录表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS download_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                mode TEXT NOT NULL,
                url TEXT NOT NULL,
                nickname TEXT,
                status TEXT NOT NULL,
                file_path TEXT,
                error_message TEXT,
                duration REAL
            )
        ''')
        
        # 主页信息缓存表（用于统计）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS homepage_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sec_uid TEXT UNIQUE NOT NULL,
                url TEXT NOT NULL,
                nickname TEXT,
                total_videos INTEGER DEFAULT 0,
                downloaded_videos INTEGER DEFAULT 0,
                fail_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                last_scan_time TEXT,
                created_time TEXT NOT NULL
            )
        ''')
        
        # 创建索引
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_error_logs_timestamp ON error_logs(timestamp DESC)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_download_records_timestamp ON download_records(timestamp DESC)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_homepage_stats_status ON homepage_stats(status)')
        
        conn.commit()
        conn.close()
    
    def add_error_log(self, level: str, message: str, source: str = None, details: str = None, max_logs: int = 500):
        """添加错误日志"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO error_logs (timestamp, level, message, source, details)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            datetime.now().isoformat(),
            level,
            message,
            source,
            details
        ))
        
        # 清理旧日志，保持最大数量
        cursor.execute('''
            DELETE FROM error_logs WHERE id NOT IN (
                SELECT id FROM error_logs ORDER BY timestamp DESC LIMIT ?
            )
        ''', (max_logs,))
        
        conn.commit()
        conn.close()
    
    def get_error_logs(self, limit: int = 100, offset: int = 0) -> List[Dict]:
        """获取错误日志"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, timestamp, level, message, source, details
            FROM error_logs
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
        ''', (limit, offset))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    def add_download_record(self, mode: str, url: str, nickname: str = None, 
                           status: str = "success", file_path: str = None, 
                           error_message: str = None, duration: float = None):
        """添加下载记录"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO download_records (timestamp, mode, url, nickname, status, file_path, error_message, duration)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            datetime.now().isoformat(),
            mode,
            url,
            nickname,
            status,
            file_path,
            error_message,
            duration
        ))
        
        conn.commit()
        conn.close()
    
    def get_download_records(self, limit: int = 100, offset: int = 0, mode: str = None) -> List[Dict]:
        """获取下载记录"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        if mode:
            cursor.execute('''
                SELECT * FROM download_records
                WHERE mode = ?
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
            ''', (mode, limit, offset))
        else:
            cursor.execute('''
                SELECT * FROM download_records
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
            ''', (limit, offset))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    def update_homepage_stats(self, sec_uid: str, url: str, nickname: str = None,
                             video_count: int = 0, fail_increment: int = 0,
                             status: str = None, last_scan_time: str = None):
        """更新主页统计信息"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # 检查是否已存在
        cursor.execute('SELECT * FROM homepage_stats WHERE sec_uid = ?', (sec_uid,))
        existing = cursor.fetchone()
        
        if existing:
            # 更新现有记录
            updates = []
            values = []
            
            if nickname:
                updates.append("nickname = ?")
                values.append(nickname)
            
            if video_count > 0:
                updates.append("total_videos = total_videos + ?")
                values.append(video_count)
            
            if fail_increment > 0:
                updates.append("fail_count = fail_count + ?")
                values.append(fail_increment)
            
            if status:
                updates.append("status = ?")
                values.append(status)
            
            if last_scan_time:
                updates.append("last_scan_time = ?")
                values.append(last_scan_time)
            
            if updates:
                values.append(sec_uid)
                cursor.execute(f'''
                    UPDATE homepage_stats
                    SET {', '.join(updates)}
                    WHERE sec_uid = ?
                ''', values)
        else:
            # 插入新记录
            cursor.execute('''
                INSERT INTO homepage_stats (sec_uid, url, nickname, total_videos, fail_count, status, created_time, last_scan_time)
                VALUES (?, ?, ?, 0, 0, ?, ?, ?)
            ''', (
                sec_uid,
                url,
                nickname,
                status or 'active',
                datetime.now().isoformat(),
                last_scan_time or datetime.now().isoformat()
            ))
        
        conn.commit()
        conn.close()
    
    def get_homepage_stats(self, status: str = None) -> List[Dict]:
        """获取主页统计信息"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        if status:
            cursor.execute('''
                SELECT * FROM homepage_stats
                WHERE status = ?
                ORDER BY last_scan_time DESC
            ''', (status,))
        else:
            cursor.execute('''
                SELECT * FROM homepage_stats
                ORDER BY last_scan_time DESC
            ''')
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    def remove_homepage(self, sec_uid: str):
        """移除主页"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM homepage_stats WHERE sec_uid = ?', (sec_uid,))
        
        conn.commit()
        conn.close()
    
    def get_statistics(self) -> Dict:
        """获取统计信息"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        stats = {}
        
        # 总下载数
        cursor.execute('SELECT COUNT(*) as count FROM download_records WHERE status = "success"')
        stats['total_downloads'] = cursor.fetchone()['count']
        
        # 活跃主页数
        cursor.execute('SELECT COUNT(*) as count FROM homepage_stats WHERE status = "active"')
        stats['active_homepages'] = cursor.fetchone()['count']
        
        # 失效主页数
        cursor.execute('SELECT COUNT(*) as count FROM homepage_stats WHERE status = "inactive"')
        stats['inactive_homepages'] = cursor.fetchone()['count']
        
        # 错误日志数
        cursor.execute('SELECT COUNT(*) as count FROM error_logs')
        stats['error_count'] = cursor.fetchone()['count']
        
        conn.close()
        
        return stats
