import aiosqlite
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime


class Database:
    def __init__(self, db_path: str = 'dy_downloader.db'):
        self.db_path = db_path
        self._initialized = False

    async def initialize(self):
        if self._initialized:
            return

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS aweme (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    aweme_id TEXT UNIQUE NOT NULL,
                    aweme_type TEXT NOT NULL,
                    title TEXT,
                    author_id TEXT,
                    author_name TEXT,
                    create_time INTEGER,
                    download_time INTEGER,
                    file_path TEXT,
                    metadata TEXT
                )
            ''')

            await db.execute('''
                CREATE TABLE IF NOT EXISTS download_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL,
                    url_type TEXT NOT NULL,
                    download_time INTEGER,
                    total_count INTEGER,
                    success_count INTEGER,
                    config TEXT
                )
            ''')

            await db.execute('CREATE INDEX IF NOT EXISTS idx_aweme_id ON aweme(aweme_id)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_author_id ON aweme(author_id)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_download_time ON aweme(download_time)')

            await db.commit()

        self._initialized = True

    async def is_downloaded(self, aweme_id: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                'SELECT id FROM aweme WHERE aweme_id = ?',
                (aweme_id,)
            )
            result = await cursor.fetchone()
            return result is not None

    async def add_aweme(self, aweme_data: Dict[str, Any]):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                INSERT OR REPLACE INTO aweme
                (aweme_id, aweme_type, title, author_id, author_name, create_time, download_time, file_path, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                aweme_data.get('aweme_id'),
                aweme_data.get('aweme_type'),
                aweme_data.get('title'),
                aweme_data.get('author_id'),
                aweme_data.get('author_name'),
                aweme_data.get('create_time'),
                int(datetime.now().timestamp()),
                aweme_data.get('file_path'),
                aweme_data.get('metadata'),
            ))
            await db.commit()

    async def get_latest_aweme_time(self, author_id: str) -> Optional[int]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                'SELECT MAX(create_time) FROM aweme WHERE author_id = ?',
                (author_id,)
            )
            result = await cursor.fetchone()
            return result[0] if result and result[0] else None

    async def add_history(self, history_data: Dict[str, Any]):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                INSERT INTO download_history
                (url, url_type, download_time, total_count, success_count, config)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                history_data.get('url'),
                history_data.get('url_type'),
                int(datetime.now().timestamp()),
                history_data.get('total_count'),
                history_data.get('success_count'),
                history_data.get('config'),
            ))
            await db.commit()

    async def get_aweme_count_by_author(self, author_id: str) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                'SELECT COUNT(*) FROM aweme WHERE author_id = ?',
                (author_id,)
            )
            result = await cursor.fetchone()
            return result[0] if result else 0

    async def close(self):
        pass
