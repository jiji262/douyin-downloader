import json
import asyncio

import pytest

from storage import Database


@pytest.mark.asyncio
async def test_database_aweme_lifecycle(tmp_path):
    db_path = tmp_path / "test.db"
    database = Database(str(db_path))

    await database.initialize()

    aweme_payload = {
        'aweme_id': '123',
        'aweme_type': 'video',
        'title': 'test',
        'author_id': 'author',
        'author_name': 'Author',
        'create_time': 1700000000,
        'file_path': '/tmp',
        'metadata': json.dumps({'a': 1}, ensure_ascii=False),
    }

    await database.add_aweme(aweme_payload)

    assert await database.is_downloaded('123') is True
    assert await database.get_aweme_count_by_author('author') == 1
    assert await database.get_latest_aweme_time('author') == 1700000000

    await database.add_history({
        'url': 'https://www.douyin.com/video/123',
        'url_type': 'video',
        'total_count': 1,
        'success_count': 1,
        'config': json.dumps({'path': './Downloaded/'}, ensure_ascii=False),
    })

    await database.close()


@pytest.mark.asyncio
async def test_database_transcript_job_upsert(tmp_path):
    db_path = tmp_path / "test.db"
    database = Database(str(db_path))
    await database.initialize()

    await database.upsert_transcript_job(
        {
            "aweme_id": "123",
            "video_path": "/tmp/demo.mp4",
            "transcript_dir": "/tmp",
            "text_path": "/tmp/demo.transcript.txt",
            "json_path": "/tmp/demo.transcript.json",
            "model": "gpt-4o-mini-transcribe",
            "status": "skipped",
            "skip_reason": "missing_api_key",
            "error_message": None,
        }
    )

    row = await database.get_transcript_job("123")
    assert row is not None
    assert row["status"] == "skipped"
    assert row["skip_reason"] == "missing_api_key"

    await database.upsert_transcript_job(
        {
            "aweme_id": "123",
            "video_path": "/tmp/demo.mp4",
            "transcript_dir": "/tmp",
            "text_path": "/tmp/demo.transcript.txt",
            "json_path": "/tmp/demo.transcript.json",
            "model": "gpt-4o-mini-transcribe",
            "status": "success",
            "skip_reason": None,
            "error_message": None,
        }
    )

    row = await database.get_transcript_job("123")
    assert row["status"] == "success"
    assert row["skip_reason"] is None

    await database.close()


@pytest.mark.asyncio
async def test_database_get_conn_reuses_single_connection_under_concurrency(
    tmp_path, monkeypatch
):
    import storage.database as database_module

    connect_calls = []

    class _FakeConn:
        def __init__(self, db_path: str):
            self.db_path = db_path
            self.closed = False

        async def close(self):
            self.closed = True

    async def _fake_connect(db_path: str):
        connect_calls.append(db_path)
        await asyncio.sleep(0)
        return _FakeConn(db_path)

    monkeypatch.setattr(database_module.aiosqlite, "connect", _fake_connect)

    database = Database(str(tmp_path / "test.db"))
    conn_a, conn_b = await asyncio.gather(database._get_conn(), database._get_conn())

    assert conn_a is conn_b
    assert connect_calls == [str(tmp_path / "test.db")]

    await database.close()
