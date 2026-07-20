import os
import tempfile

import pytest

from storage.database import Database


@pytest.mark.asyncio
async def test_get_aweme_history_paginates():
    with tempfile.TemporaryDirectory() as td:
        db = Database(db_path=os.path.join(td, "t.db"))
        await db.initialize()
        for i in range(5):
            await db.add_aweme(
                {
                    "aweme_id": f"id{i}",
                    "aweme_type": "video",
                    "title": f"t{i}",
                    "author_id": "u1",
                    "author_name": "A",
                    "create_time": 1700000000 + i,
                    "file_path": f"/tmp/{i}",
                    "metadata": "{}",
                }
            )
        page1 = await db.get_aweme_history(page=1, size=2)
        page2 = await db.get_aweme_history(page=2, size=2)
        assert len(page1["items"]) == 2
        assert len(page2["items"]) == 2
        assert page1["total"] == 5
        await db.close()


@pytest.mark.asyncio
async def test_get_aweme_history_filters_by_author():
    with tempfile.TemporaryDirectory() as td:
        db = Database(db_path=os.path.join(td, "t.db"))
        await db.initialize()
        await db.add_aweme(
            {
                "aweme_id": "a",
                "aweme_type": "video",
                "title": "Aa",
                "author_id": "u1",
                "author_name": "Alice",
                "create_time": 0,
                "file_path": "/tmp/a",
                "metadata": "{}",
            }
        )
        await db.add_aweme(
            {
                "aweme_id": "b",
                "aweme_type": "video",
                "title": "Bb",
                "author_id": "u2",
                "author_name": "Bob",
                "create_time": 0,
                "file_path": "/tmp/b",
                "metadata": "{}",
            }
        )
        res = await db.get_aweme_history(page=1, size=10, author="Alice")
        assert len(res["items"]) == 1
        assert res["items"][0]["author_name"] == "Alice"
        await db.close()


@pytest.mark.asyncio
async def test_get_aweme_history_filters_by_aweme_type():
    with tempfile.TemporaryDirectory() as td:
        db = Database(db_path=os.path.join(td, "t.db"))
        await db.initialize()
        await db.add_aweme(
            {
                "aweme_id": "a",
                "aweme_type": "video",
                "title": "",
                "author_id": "u",
                "author_name": "A",
                "create_time": 0,
                "file_path": "/tmp/a",
                "metadata": "{}",
            }
        )
        await db.add_aweme(
            {
                "aweme_id": "b",
                "aweme_type": "note",
                "title": "",
                "author_id": "u",
                "author_name": "A",
                "create_time": 0,
                "file_path": "/tmp/b",
                "metadata": "{}",
            }
        )
        res = await db.get_aweme_history(page=1, size=10, aweme_type="note")
        assert len(res["items"]) == 1
        assert res["items"][0]["aweme_id"] == "b"
        await db.close()


@pytest.mark.asyncio
async def test_get_aweme_history_filters_by_title_substring():
    with tempfile.TemporaryDirectory() as td:
        db = Database(db_path=os.path.join(td, "t.db"))
        await db.initialize()
        for idx, title in enumerate(["abcDEF", "xyz", "FooABCbar", None]):
            await db.add_aweme(
                {
                    "aweme_id": f"id{idx}",
                    "aweme_type": "video",
                    "title": title,
                    "author_id": "u",
                    "author_name": "A",
                    "create_time": 0,
                    "file_path": f"/tmp/{idx}",
                    "metadata": "{}",
                }
            )
        res = await db.get_aweme_history(page=1, size=10, title="abc")
        titles = sorted(item["title"] for item in res["items"])
        assert titles == ["FooABCbar", "abcDEF"]
        await db.close()


@pytest.mark.asyncio
async def test_get_aweme_history_empty_db():
    with tempfile.TemporaryDirectory() as td:
        db = Database(db_path=os.path.join(td, "t.db"))
        await db.initialize()
        res = await db.get_aweme_history(page=1, size=10)
        assert res == {"total": 0, "page": 1, "size": 10, "items": []}
        await db.close()


async def test_history_excludes_rows_without_file_path(tmp_path):
    """Sync-inserted aweme rows (file_path='') must not pollute History."""
    db = Database(db_path=str(tmp_path / "t.db"))
    await db.initialize()
    try:
        await db.add_aweme(
            {
                "aweme_id": "LIKED",
                "aweme_type": "video",
                "title": "liked-only",
                "file_path": "",
                "metadata": "",
            }
        )
        await db.add_aweme(
            {
                "aweme_id": "DL",
                "aweme_type": "video",
                "title": "downloaded",
                "file_path": "/tmp/d.mp4",
                "metadata": "",
            }
        )
        page = await db.get_aweme_history(page=1, size=50)
        ids = [it["aweme_id"] for it in page["items"]]
        assert ids == ["DL"]
        assert page["total"] == 1
    finally:
        await db.close()


async def _history_db(td):
    import os as _os

    db = Database(db_path=_os.path.join(td, "hist.db"))
    await db.initialize()
    return db


async def test_history_author_filter_is_substring_and_sec_uid_exact(tmp_path):
    db = await _history_db(str(tmp_path))
    try:
        await db.add_aweme(
            {
                "aweme_id": "A1",
                "aweme_type": "video",
                "title": "t",
                "author_name": "张三的美食",
                "author_sec_uid": "SEC_A",
                "file_path": "/tmp/a.mp4",
                "metadata": "",
            }
        )
        await db.add_aweme(
            {
                "aweme_id": "B1",
                "aweme_type": "video",
                "title": "t",
                "author_name": "李四",
                "author_sec_uid": "SEC_B",
                "file_path": "/tmp/b.mp4",
                "metadata": "",
            }
        )
        # substring match on author name
        page = await db.get_aweme_history(author="美食")
        assert [it["aweme_id"] for it in page["items"]] == ["A1"]
        # exact author_sec_uid filter
        page = await db.get_aweme_history(author_sec_uid="SEC_B")
        assert [it["aweme_id"] for it in page["items"]] == ["B1"]
    finally:
        await db.close()


async def test_history_job_id_filter(tmp_path):
    db = await _history_db(str(tmp_path))
    try:
        await db.add_aweme(
            {
                "aweme_id": "J1",
                "aweme_type": "video",
                "title": "t",
                "file_path": "/tmp/1.mp4",
                "metadata": "",
                "job_id": "JOB_A",
            }
        )
        await db.add_aweme(
            {
                "aweme_id": "J2",
                "aweme_type": "video",
                "title": "t",
                "file_path": "/tmp/2.mp4",
                "metadata": "",
                "job_id": "JOB_B",
            }
        )
        page = await db.get_aweme_history(job_id="JOB_A")
        assert [it["aweme_id"] for it in page["items"]] == ["J1"]
        assert page["total"] == 1
        # items expose job_id + cover_urls for the renderer
        assert page["items"][0]["job_id"] == "JOB_A"
        assert page["items"][0]["cover_urls"] == []
    finally:
        await db.close()


async def test_history_sort_by_create_time(tmp_path):
    db = await _history_db(str(tmp_path))
    try:
        # OLD_PUB downloaded later (higher download_time) but published earlier.
        await db.add_aweme(
            {
                "aweme_id": "NEW_PUB",
                "aweme_type": "video",
                "title": "t",
                "create_time": 2_000,
                "file_path": "/tmp/n.mp4",
                "metadata": "",
            }
        )
        await db.add_aweme(
            {
                "aweme_id": "OLD_PUB",
                "aweme_type": "video",
                "title": "t",
                "create_time": 1_000,
                "file_path": "/tmp/o.mp4",
                "metadata": "",
            }
        )
        default_page = await db.get_aweme_history()
        assert [it["aweme_id"] for it in default_page["items"]] == [
            "OLD_PUB",
            "NEW_PUB",
        ]  # download_time DESC (OLD_PUB inserted later)
        by_publish = await db.get_aweme_history(sort="create_time")
        assert [it["aweme_id"] for it in by_publish["items"]] == ["NEW_PUB", "OLD_PUB"]
    finally:
        await db.close()


async def test_history_surfaces_cover_urls(tmp_path):
    db = await _history_db(str(tmp_path))
    try:
        await db.add_aweme(
            {
                "aweme_id": "C1",
                "aweme_type": "video",
                "title": "t",
                "file_path": "/tmp/c.mp4",
                "metadata": "",
                "cover_urls": '["https://p9/c.jpg"]',
            }
        )
        page = await db.get_aweme_history()
        assert page["items"][0]["cover_urls"] == ["https://p9/c.jpg"]
    finally:
        await db.close()


async def test_history_author_filter_treats_wildcards_literally(tmp_path):
    """`100%` must match the literal author name, not act as a LIKE prefix."""
    db = Database(db_path=str(tmp_path / "esc.db"))
    await db.initialize()
    try:
        await db.add_aweme(
            {
                "aweme_id": "W1",
                "aweme_type": "video",
                "title": "t",
                "author_name": "100%正品店",
                "file_path": "/tmp/1.mp4",
                "metadata": "",
            }
        )
        await db.add_aweme(
            {
                "aweme_id": "W2",
                "aweme_type": "video",
                "title": "t",
                "author_name": "100分先生",
                "file_path": "/tmp/2.mp4",
                "metadata": "",
            }
        )
        page = await db.get_aweme_history(author="100%")
        assert [it["aweme_id"] for it in page["items"]] == ["W1"]
        # Title filters remain substring matches after escaping LIKE wildcards.
        page = await db.get_aweme_history(title="t")
        assert page["total"] == 2
    finally:
        await db.close()
