import asyncio
import json

import pytest

from storage import Database


@pytest.mark.asyncio
async def test_database_aweme_lifecycle(tmp_path):
    db_path = tmp_path / "test.db"
    database = Database(str(db_path))

    await database.initialize()

    aweme_payload = {
        "aweme_id": "123",
        "aweme_type": "video",
        "title": "test",
        "author_id": "author",
        "author_name": "Author",
        "create_time": 1700000000,
        "file_path": "/tmp",
        "metadata": json.dumps({"a": 1}, ensure_ascii=False),
    }

    await database.add_aweme(aweme_payload)

    assert await database.is_downloaded("123") is True
    assert await database.get_aweme_count_by_author("author") == 1
    assert await database.get_latest_aweme_time("author") == 1700000000

    await database.add_history(
        {
            "url": "https://www.douyin.com/video/123",
            "url_type": "video",
            "total_count": 1,
            "success_count": 1,
            "config": json.dumps({"path": "./Downloaded/"}, ensure_ascii=False),
        }
    )

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
async def test_database_initialize_sets_wal_journal_mode(tmp_path):
    db_path = tmp_path / "test.db"
    database = Database(str(db_path))
    await database.initialize()

    db = await database._get_conn()
    cursor = await db.execute("PRAGMA journal_mode")
    row = await cursor.fetchone()
    assert row is not None
    assert str(row[0]).lower() == "wal"

    cursor = await db.execute("PRAGMA synchronous")
    row = await cursor.fetchone()
    # synchronous=NORMAL == 1
    assert row is not None
    assert int(row[0]) == 1

    await database.close()


@pytest.mark.asyncio
async def test_add_aweme_batch_inserts_all_items(tmp_path):
    db_path = tmp_path / "test.db"
    database = Database(str(db_path))
    await database.initialize()

    items = [
        {
            "aweme_id": str(i),
            "aweme_type": "video",
            "title": f"title-{i}",
            "author_id": "author",
            "author_name": "Author",
            "create_time": 1700000000 + i,
            "file_path": "/tmp",
            "metadata": json.dumps({"i": i}, ensure_ascii=False),
        }
        for i in range(100)
    ]

    await database.add_aweme_batch(items)

    assert await database.get_aweme_count_by_author("author") == 100
    for i in range(100):
        assert await database.is_downloaded(str(i)) is True

    await database.close()


@pytest.mark.asyncio
async def test_add_aweme_batch_empty_list_is_noop(tmp_path):
    db_path = tmp_path / "test.db"
    database = Database(str(db_path))
    await database.initialize()

    await database.add_aweme_batch([])

    assert await database.get_aweme_count_by_author("author") == 0

    await database.close()


@pytest.mark.asyncio
async def test_add_aweme_batch_replaces_on_conflict(tmp_path):
    db_path = tmp_path / "test.db"
    database = Database(str(db_path))
    await database.initialize()

    base = {
        "aweme_id": "777",
        "aweme_type": "video",
        "title": "first",
        "author_id": "author",
        "author_name": "Author",
        "create_time": 1700000000,
        "file_path": "/tmp/a",
        "metadata": json.dumps({"v": 1}, ensure_ascii=False),
    }
    await database.add_aweme_batch([base])

    updated = dict(base)
    updated["title"] = "second"
    updated["file_path"] = "/tmp/b"
    await database.add_aweme_batch([updated])

    db = await database._get_conn()
    cursor = await db.execute("SELECT title, file_path FROM aweme WHERE aweme_id = ?", ("777",))
    row = await cursor.fetchone()
    assert row == ("second", "/tmp/b")

    cursor = await db.execute("SELECT COUNT(*) FROM aweme WHERE aweme_id = ?", ("777",))
    count_row = await cursor.fetchone()
    assert count_row[0] == 1

    await database.close()


@pytest.mark.asyncio
async def test_add_aweme_batch_uses_single_commit(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    database = Database(str(db_path))
    await database.initialize()

    db = await database._get_conn()
    commit_count = {"n": 0}
    original_commit = db.commit

    async def counting_commit():
        commit_count["n"] += 1
        return await original_commit()

    monkeypatch.setattr(db, "commit", counting_commit)

    items = [
        {
            "aweme_id": str(i),
            "aweme_type": "video",
            "title": f"t{i}",
            "author_id": "a",
            "author_name": "A",
            "create_time": 1700000000 + i,
            "file_path": "/tmp",
            "metadata": "{}",
        }
        for i in range(50)
    ]
    await database.add_aweme_batch(items)

    assert commit_count["n"] == 1, (
        f"expected exactly 1 commit for batch insert, got {commit_count['n']}"
    )

    await database.close()


@pytest.mark.asyncio
async def test_database_get_conn_reuses_single_connection_under_concurrency(tmp_path, monkeypatch):
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
