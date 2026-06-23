"""Tests for `storage/database.py` migration + `delete_aweme_by_ids`.

Covers task 1.2 of the desktop-ux-overhaul spec:

1. Legacy DB without the `author_sec_uid` column -> `initialize()` adds it.
2. Second `initialize()` is a no-op (idempotent).
3. `add_aweme` persists None / non-null values for `author_sec_uid` correctly,
   including the payload-key fallback.
4. `get_aweme_history` surfaces `author_sec_uid` on each returned item.
5. `delete_aweme_by_ids(["a","b"])` removes only matching rows and returns
   the affected row count.
6. Empty list is a no-op returning 0; duplicate ids don't double-count.
"""

import aiosqlite

from storage.database import Database

# ---------------------------------------------------------------------------
# Legacy DDL: the `aweme` table as it existed BEFORE the `author_sec_uid`
# migration. Creating this directly lets us prove the migration upgrades an
# existing, pre-populated database in place without data loss.
# ---------------------------------------------------------------------------
_LEGACY_AWEME_DDL = """
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
"""


async def _table_columns(db_path: str, table: str):
    """Return the set of column names for the given table via PRAGMA."""
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(f"PRAGMA table_info({table})")
        rows = await cursor.fetchall()
    return {row[1] for row in rows}


# ---------------------------------------------------------------------------
# 1. Migration — column added onto a legacy DB
# ---------------------------------------------------------------------------
async def test_initialize_adds_author_sec_uid_to_legacy_db(tmp_path):
    db_path = tmp_path / "test.db"

    # Simulate a pre-migration database: the aweme table exists WITHOUT the
    # author_sec_uid column and contains a row. The migration must be additive.
    async with aiosqlite.connect(str(db_path)) as raw:
        await raw.execute(_LEGACY_AWEME_DDL)
        await raw.execute(
            """
            INSERT INTO aweme
            (aweme_id, aweme_type, title, author_id, author_name,
             create_time, download_time, file_path, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("legacy_1", "video", "t", "u", "A", 1700000000, 1700000000, "/tmp", "{}"),
        )
        await raw.commit()

    pre_cols = await _table_columns(str(db_path), "aweme")
    assert "author_sec_uid" not in pre_cols, "fixture should start pre-migration"

    db = Database(db_path=str(db_path))
    await db.initialize()
    try:
        post_cols = await _table_columns(str(db_path), "aweme")
        assert "author_sec_uid" in post_cols

        # Legacy row must still exist and the new column defaults to NULL.
        conn = await db._get_conn()
        cursor = await conn.execute(
            "SELECT aweme_id, author_sec_uid FROM aweme WHERE aweme_id = ?",
            ("legacy_1",),
        )
        row = await cursor.fetchone()
        assert row == ("legacy_1", None)
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# 2. Idempotent migration
# ---------------------------------------------------------------------------
async def test_initialize_is_idempotent_on_same_instance(tmp_path):
    db = Database(db_path=str(tmp_path / "test.db"))
    try:
        await db.initialize()
        # Second call on the same instance must not raise and must leave the
        # schema intact.
        await db.initialize()
        cols = await _table_columns(db.db_path, "aweme")
        assert "author_sec_uid" in cols
    finally:
        await db.close()


async def test_initialize_is_idempotent_across_instances(tmp_path):
    path = str(tmp_path / "test.db")

    db1 = Database(db_path=path)
    await db1.initialize()
    await db1.close()

    # A brand-new Database instance pointing at an already-migrated file must
    # also complete initialize() without error (no duplicate ALTER TABLE, etc.).
    db2 = Database(db_path=path)
    try:
        await db2.initialize()
        cols = await _table_columns(path, "aweme")
        assert "author_sec_uid" in cols
    finally:
        await db2.close()


# ---------------------------------------------------------------------------
# 3. add_aweme persists author_sec_uid (kwarg, payload-key, or NULL)
# ---------------------------------------------------------------------------
def _base_payload(aweme_id: str):
    return {
        "aweme_id": aweme_id,
        "aweme_type": "video",
        "title": f"title-{aweme_id}",
        "author_id": "u1",
        "author_name": "Alice",
        "create_time": 1700000000,
        "file_path": f"/tmp/{aweme_id}",
        "metadata": "{}",
    }


async def _fetch_sec_uid(db: Database, aweme_id: str):
    conn = await db._get_conn()
    cursor = await conn.execute("SELECT author_sec_uid FROM aweme WHERE aweme_id = ?", (aweme_id,))
    row = await cursor.fetchone()
    return None if row is None else row[0]


async def test_add_aweme_persists_explicit_author_sec_uid(tmp_path):
    db = Database(db_path=str(tmp_path / "test.db"))
    await db.initialize()
    try:
        await db.add_aweme(_base_payload("id1"), author_sec_uid="SEC_X")
        assert await _fetch_sec_uid(db, "id1") == "SEC_X"
    finally:
        await db.close()


async def test_add_aweme_persists_null_when_nothing_provided(tmp_path):
    db = Database(db_path=str(tmp_path / "test.db"))
    await db.initialize()
    try:
        await db.add_aweme(_base_payload("id2"))  # no kwarg, no payload key
        assert await _fetch_sec_uid(db, "id2") is None
    finally:
        await db.close()


async def test_add_aweme_falls_back_to_payload_key(tmp_path):
    """When no kwarg is given, the value from the payload dict is used."""
    db = Database(db_path=str(tmp_path / "test.db"))
    await db.initialize()
    try:
        payload = _base_payload("id3")
        payload["author_sec_uid"] = "SEC_FROM_PAYLOAD"
        await db.add_aweme(payload)
        assert await _fetch_sec_uid(db, "id3") == "SEC_FROM_PAYLOAD"
    finally:
        await db.close()


async def test_add_aweme_kwarg_wins_over_payload_key(tmp_path):
    """When both are provided, the explicit kwarg takes precedence."""
    db = Database(db_path=str(tmp_path / "test.db"))
    await db.initialize()
    try:
        payload = _base_payload("id4")
        payload["author_sec_uid"] = "SEC_FROM_PAYLOAD"
        await db.add_aweme(payload, author_sec_uid="SEC_FROM_KWARG")
        assert await _fetch_sec_uid(db, "id4") == "SEC_FROM_KWARG"
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# 4. get_aweme_history surfaces author_sec_uid
# ---------------------------------------------------------------------------
async def test_get_aweme_history_returns_author_sec_uid(tmp_path):
    db = Database(db_path=str(tmp_path / "test.db"))
    await db.initialize()
    try:
        await db.add_aweme(_base_payload("with_sec"), author_sec_uid="SEC_ABC")
        await db.add_aweme(_base_payload("without_sec"))  # NULL

        res = await db.get_aweme_history(page=1, size=10)
        assert res["total"] == 2

        by_id = {item["aweme_id"]: item for item in res["items"]}
        assert "author_sec_uid" in by_id["with_sec"]
        assert by_id["with_sec"]["author_sec_uid"] == "SEC_ABC"
        assert by_id["without_sec"]["author_sec_uid"] is None
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# 5. delete_aweme_by_ids — happy path
# ---------------------------------------------------------------------------
async def test_delete_aweme_by_ids_removes_only_matching_rows(tmp_path):
    db = Database(db_path=str(tmp_path / "test.db"))
    await db.initialize()
    try:
        for aid in ("a", "b", "c"):
            await db.add_aweme(_base_payload(aid))

        deleted = await db.delete_aweme_by_ids(["a", "b"])
        assert deleted == 2

        assert await db.is_downloaded("a") is False
        assert await db.is_downloaded("b") is False
        assert await db.is_downloaded("c") is True
    finally:
        await db.close()


async def test_delete_aweme_by_ids_ignores_unknown_ids(tmp_path):
    """Unknown ids simply contribute 0 to the count; known ones are removed."""
    db = Database(db_path=str(tmp_path / "test.db"))
    await db.initialize()
    try:
        await db.add_aweme(_base_payload("a"))
        deleted = await db.delete_aweme_by_ids(["a", "does-not-exist"])
        assert deleted == 1
        assert await db.is_downloaded("a") is False
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# 6. delete_aweme_by_ids — empty list / duplicate ids
# ---------------------------------------------------------------------------
async def test_delete_aweme_by_ids_empty_list_is_noop(tmp_path):
    db = Database(db_path=str(tmp_path / "test.db"))
    await db.initialize()
    try:
        await db.add_aweme(_base_payload("a"))

        deleted = await db.delete_aweme_by_ids([])
        assert deleted == 0
        # The previously inserted row must still be present.
        assert await db.is_downloaded("a") is True
    finally:
        await db.close()


async def test_delete_aweme_by_ids_dedupes_duplicate_ids(tmp_path):
    """Passing the same id twice must not inflate the deleted count."""
    db = Database(db_path=str(tmp_path / "test.db"))
    await db.initialize()
    try:
        await db.add_aweme(_base_payload("a"))
        deleted = await db.delete_aweme_by_ids(["a", "a"])
        assert deleted == 1
        assert await db.is_downloaded("a") is False
    finally:
        await db.close()
