import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiosqlite


class Database:
    def __init__(self, db_path: str = 'dy_downloader.db'):
        self.db_path = db_path
        self._initialized = False
        self._conn: Optional[aiosqlite.Connection] = None
        # 延迟到首次 _get_conn 调用时在当前 event loop 上创建 Lock，
        # 避免在 __init__ 阶段抢到错误的 loop。
        self._conn_lock: Optional[asyncio.Lock] = None

    async def _get_conn(self) -> aiosqlite.Connection:
        if self._conn is not None:
            return self._conn
        if self._conn_lock is None:
            self._conn_lock = asyncio.Lock()
        async with self._conn_lock:
            if self._conn is None:
                self._conn = await aiosqlite.connect(self.db_path)
        return self._conn

    async def initialize(self):
        if self._initialized:
            return

        db = await self._get_conn()

        # WAL gives concurrent reader/writer; NORMAL avoids fsync on every commit
        # (loses at most last few txns on power loss — acceptable for download history).
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")

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

        # Incremental migration: add author_sec_uid column to legacy aweme tables.
        # Running initialize() twice must be a no-op.
        cursor = await db.execute("PRAGMA table_info(aweme)")
        existing_columns = {row[1] for row in await cursor.fetchall()}
        if "author_sec_uid" not in existing_columns:
            await db.execute("ALTER TABLE aweme ADD COLUMN author_sec_uid TEXT")

        await db.commit()
        self._initialized = True

    async def is_downloaded(self, aweme_id: str) -> bool:
        db = await self._get_conn()
        cursor = await db.execute(
            'SELECT id FROM aweme WHERE aweme_id = ?',
            (aweme_id,)
        )
        result = await cursor.fetchone()
        return result is not None

    async def add_aweme(
        self,
        aweme_data: Dict[str, Any],
        *,
        author_sec_uid: Optional[str] = None,
    ):
        db = await self._get_conn()
        # Prefer the explicit kwarg; fall back to a key on the payload so existing
        # callers (tests, legacy downloaders) keep working.
        sec_uid = (
            author_sec_uid
            if author_sec_uid is not None
            else aweme_data.get("author_sec_uid")
        )
        await db.execute('''
            INSERT OR REPLACE INTO aweme
            (aweme_id, aweme_type, title, author_id, author_name, author_sec_uid,
             create_time, download_time, file_path, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            aweme_data.get('aweme_id'),
            aweme_data.get('aweme_type'),
            aweme_data.get('title'),
            aweme_data.get('author_id'),
            aweme_data.get('author_name'),
            sec_uid,
            aweme_data.get('create_time'),
            int(datetime.now().timestamp()),
            aweme_data.get('file_path'),
            aweme_data.get('metadata'),
        ))
        await db.commit()

    async def add_aweme_batch(self, items: List[Dict[str, Any]]) -> None:
        """Insert N awemes in a single transaction. Replaces existing rows by aweme_id."""
        if not items:
            return
        db = await self._get_conn()
        now_ts = int(datetime.now().timestamp())
        rows = [
            (
                item.get('aweme_id'),
                item.get('aweme_type'),
                item.get('title'),
                item.get('author_id'),
                item.get('author_name'),
                item.get('author_sec_uid'),
                item.get('create_time'),
                now_ts,
                item.get('file_path'),
                item.get('metadata'),
            )
            for item in items
        ]
        await db.executemany('''
            INSERT OR REPLACE INTO aweme
            (aweme_id, aweme_type, title, author_id, author_name, author_sec_uid,
             create_time, download_time, file_path, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', rows)
        await db.commit()

    async def get_latest_aweme_time(self, author_id: str) -> Optional[int]:
        db = await self._get_conn()
        cursor = await db.execute(
            'SELECT MAX(create_time) FROM aweme WHERE author_id = ?',
            (author_id,)
        )
        result = await cursor.fetchone()
        return result[0] if result and result[0] else None

    async def add_history(self, history_data: Dict[str, Any]):
        db = await self._get_conn()
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

    async def get_aweme_history(
        self,
        *,
        page: int = 1,
        size: int = 50,
        author: Optional[str] = None,
        date_from: Optional[int] = None,
        date_to: Optional[int] = None,
        aweme_type: Optional[str] = None,
        title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Paginated aweme history, newest download first.

        `date_from` / `date_to` are unix-seconds (filter against `create_time`).
        `aweme_type` matches the `aweme_type` column (e.g. 'video', 'gallery').
        `title` is a case-insensitive substring match on the title column.
        """
        db = await self._get_conn()
        where: list = []
        params: list = []
        if author:
            where.append("author_name = ?")
            params.append(author)
        if date_from is not None:
            where.append("create_time >= ?")
            params.append(int(date_from))
        if date_to is not None:
            where.append("create_time <= ?")
            params.append(int(date_to))
        if aweme_type:
            where.append("aweme_type = ?")
            params.append(aweme_type)
        if title:
            where.append("LOWER(COALESCE(title, '')) LIKE ?")
            params.append(f"%{title.lower()}%")
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        cursor = await db.execute(
            f"SELECT COUNT(*) FROM aweme {where_sql}", params
        )
        row = await cursor.fetchone()
        total = int(row[0]) if row else 0

        offset = max(0, (page - 1) * size)
        cursor = await db.execute(
            f"SELECT aweme_id, aweme_type, title, author_id, author_name, "
            f"author_sec_uid, create_time, download_time, file_path FROM aweme "
            f"{where_sql} ORDER BY download_time DESC, id DESC LIMIT ? OFFSET ?",
            params + [int(size), int(offset)],
        )
        rows = await cursor.fetchall()
        items = [
            {
                "aweme_id": r[0],
                "aweme_type": r[1],
                "title": r[2],
                "author_id": r[3],
                "author_name": r[4],
                "author_sec_uid": r[5],
                "create_time": r[6],
                "download_time": r[7],
                "file_path": r[8],
            }
            for r in rows
        ]
        return {"total": total, "page": int(page), "size": int(size), "items": items}

    async def get_aweme_count_by_author(self, author_id: str) -> int:
        db = await self._get_conn()
        cursor = await db.execute(
            'SELECT COUNT(*) FROM aweme WHERE author_id = ?',
            (author_id,)
        )
        result = await cursor.fetchone()
        return result[0] if result else 0

    async def upsert_transcript_job(self, job_data: Dict[str, Any]):
        now_ts = int(datetime.now().timestamp())
        db = await self._get_conn()
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
        db = await self._get_conn()
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

    async def delete_aweme_by_ids(self, aweme_ids: List[str]) -> int:
        """Delete aweme rows by their string id. Returns the number of rows removed.

        Empty input is a no-op that returns 0 without issuing any SQL.

        Uses a parameterized ``DELETE ... WHERE aweme_id IN (?,?,...)`` statement
        because ``aiosqlite.Cursor.rowcount`` is not reliably populated after
        ``executemany`` across all versions. Chunked at 500 ids per statement to
        stay well below SQLite's host-parameter limit (historically 999).
        """
        if not aweme_ids:
            return 0
        # De-duplicate input while preserving a stable order. Duplicate ids would
        # otherwise match the same row twice in different chunks and inflate the
        # returned count beyond the rows actually affected.
        seen: Dict[str, None] = {}
        for aid in aweme_ids:
            if aid not in seen:
                seen[aid] = None
        unique_ids = list(seen.keys())

        db = await self._get_conn()
        if self._conn_lock is None:
            self._conn_lock = asyncio.Lock()
        deleted = 0
        chunk_size = 500
        async with self._conn_lock:
            for start in range(0, len(unique_ids), chunk_size):
                chunk = unique_ids[start : start + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                cursor = await db.execute(
                    f"DELETE FROM aweme WHERE aweme_id IN ({placeholders})",
                    chunk,
                )
                if cursor.rowcount is not None and cursor.rowcount > 0:
                    deleted += cursor.rowcount
            await db.commit()
        return deleted

    async def truncate_history(self) -> None:
        """Delete every row from `aweme` and `download_history`.

        Does not touch disk files or any other table (e.g. transcript_job).
        """
        db = await self._get_conn()
        if self._conn_lock is None:
            self._conn_lock = asyncio.Lock()
        async with self._conn_lock:
            await db.execute("DELETE FROM aweme")
            await db.execute("DELETE FROM download_history")
            await db.commit()

    async def close(self):
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
