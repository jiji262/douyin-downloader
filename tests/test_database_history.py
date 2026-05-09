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
            await db.add_aweme({
                "aweme_id": f"id{i}",
                "aweme_type": "video",
                "title": f"t{i}",
                "author_id": "u1",
                "author_name": "A",
                "create_time": 1700000000 + i,
                "file_path": f"/tmp/{i}",
                "metadata": "{}",
            })
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
        await db.add_aweme({
            "aweme_id": "a", "aweme_type": "video", "title": "Aa",
            "author_id": "u1", "author_name": "Alice",
            "create_time": 0, "file_path": "/tmp/a", "metadata": "{}",
        })
        await db.add_aweme({
            "aweme_id": "b", "aweme_type": "video", "title": "Bb",
            "author_id": "u2", "author_name": "Bob",
            "create_time": 0, "file_path": "/tmp/b", "metadata": "{}",
        })
        res = await db.get_aweme_history(page=1, size=10, author="Alice")
        assert len(res["items"]) == 1
        assert res["items"][0]["author_name"] == "Alice"
        await db.close()


@pytest.mark.asyncio
async def test_get_aweme_history_filters_by_aweme_type():
    with tempfile.TemporaryDirectory() as td:
        db = Database(db_path=os.path.join(td, "t.db"))
        await db.initialize()
        await db.add_aweme({
            "aweme_id": "a", "aweme_type": "video", "title": "",
            "author_id": "u", "author_name": "A",
            "create_time": 0, "file_path": "/tmp/a", "metadata": "{}",
        })
        await db.add_aweme({
            "aweme_id": "b", "aweme_type": "note", "title": "",
            "author_id": "u", "author_name": "A",
            "create_time": 0, "file_path": "/tmp/b", "metadata": "{}",
        })
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
            await db.add_aweme({
                "aweme_id": f"id{idx}",
                "aweme_type": "video",
                "title": title,
                "author_id": "u",
                "author_name": "A",
                "create_time": 0,
                "file_path": f"/tmp/{idx}",
                "metadata": "{}",
            })
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
