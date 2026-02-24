import aiosqlite
from typing import Dict, Any, Optional
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

            await db.execute('''
                CREATE TABLE IF NOT EXISTS transcript_job (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    aweme_id TEXT NOT NULL,
                    video_path TEXT NOT NULL,
                    transcript_dir TEXT,
                    text_path TEXT,
                    json_path TEXT,
                    model TEXT NOT NULL,
                    status TEXT NOT NULL,
                    skip_reason TEXT,
                    error_message TEXT,
                    created_at INTEGER,
                    updated_at INTEGER,
                    UNIQUE(aweme_id, video_path, model)
                )
            ''')

            await db.execute('CREATE INDEX IF NOT EXISTS idx_aweme_id ON aweme(aweme_id)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_author_id ON aweme(author_id)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_download_time ON aweme(download_time)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_transcript_aweme_id ON transcript_job(aweme_id)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_transcript_status ON transcript_job(status)')

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

    async def upsert_transcript_job(self, job_data: Dict[str, Any]):
        now_ts = int(datetime.now().timestamp())
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                INSERT INTO transcript_job (
                    aweme_id,
                    video_path,
                    transcript_dir,
                    text_path,
                    json_path,
                    model,
                    status,
                    skip_reason,
                    error_message,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(aweme_id, video_path, model) DO UPDATE SET
                    transcript_dir = excluded.transcript_dir,
                    text_path = excluded.text_path,
                    json_path = excluded.json_path,
                    status = excluded.status,
                    skip_reason = excluded.skip_reason,
                    error_message = excluded.error_message,
                    updated_at = excluded.updated_at
            ''', (
                job_data.get('aweme_id'),
                job_data.get('video_path'),
                job_data.get('transcript_dir'),
                job_data.get('text_path'),
                job_data.get('json_path'),
                job_data.get('model') or 'gpt-4o-mini-transcribe',
                job_data.get('status'),
                job_data.get('skip_reason'),
                job_data.get('error_message'),
                now_ts,
                now_ts,
            ))
            await db.commit()

    async def get_transcript_job(self, aweme_id: str) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                '''
                SELECT aweme_id, video_path, transcript_dir, text_path, json_path,
                       model, status, skip_reason, error_message, created_at, updated_at
                FROM transcript_job
                WHERE aweme_id = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                ''',
                (aweme_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                'aweme_id': row[0],
                'video_path': row[1],
                'transcript_dir': row[2],
                'text_path': row[3],
                'json_path': row[4],
                'model': row[5],
                'status': row[6],
                'skip_reason': row[7],
                'error_message': row[8],
                'created_at': row[9],
                'updated_at': row[10],
            }

    async def close(self):
        pass
