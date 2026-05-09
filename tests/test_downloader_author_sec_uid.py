"""Tests for task 2.2: downloader writes `author_sec_uid` (or None).

Covers the spec requirement R12.12:

> When a downloader persists an aweme row, the `author_sec_uid` column must
> carry `aweme.author.sec_uid` when present, and NULL otherwise.

The tests are layered from narrow to broad:

1. ``extract_author_sec_uid`` is exercised directly on many payload shapes
   (the defensive helper that the downloaders call at every `add_aweme`
   site, see ``core/metadata.py``).

2. A ``VideoDownloader`` exercises the full ``_download_aweme_assets`` path
   against a real on-disk SQLite ``Database`` (no mocks at the storage
   boundary) with two aweme payloads — one with ``author.sec_uid`` set and
   one without — and we assert the row in the ``aweme`` table matches.

3. A ``MusicDownloader`` test does the same for the music fallback path,
   which is the other call site modified in task 2.1.

The downloader tests avoid real network and disk media writes via the same
``_download_with_retry`` / ``get_session`` monkeypatch pattern used across
``tests/test_video_downloader.py`` and ``tests/test_music_downloader.py``.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from auth import CookieManager
from config import ConfigLoader
from control import QueueManager, RateLimiter, RetryHandler
from core.api_client import DouyinAPIClient
from core.metadata import extract_author_sec_uid
from core.music_downloader import MusicDownloader
from core.video_downloader import VideoDownloader
from storage import Database, FileManager


# ---------------------------------------------------------------------------
# 1. Pure helper — extract_author_sec_uid
# ---------------------------------------------------------------------------
def test_extract_returns_sec_uid_when_present():
    assert extract_author_sec_uid({"author": {"sec_uid": "SEC_X"}}) == "SEC_X"


def test_extract_strips_whitespace():
    assert extract_author_sec_uid({"author": {"sec_uid": "  SEC_Y  "}}) == "SEC_Y"


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(None, id="input-is-none"),
        pytest.param("not-a-mapping", id="input-is-string"),
        pytest.param({}, id="author-missing"),
        pytest.param({"author": None}, id="author-is-none"),
        pytest.param({"author": "not-a-mapping"}, id="author-is-string"),
        pytest.param({"author": {}}, id="sec-uid-missing"),
        pytest.param({"author": {"sec_uid": None}}, id="sec-uid-is-none"),
        pytest.param({"author": {"sec_uid": 123}}, id="sec-uid-not-string"),
        pytest.param({"author": {"sec_uid": ""}}, id="sec-uid-empty-string"),
        pytest.param({"author": {"sec_uid": "   "}}, id="sec-uid-whitespace"),
    ],
)
def test_extract_returns_none_for_invalid_payloads(payload):
    assert extract_author_sec_uid(payload) is None


# ---------------------------------------------------------------------------
# 2. VideoDownloader end-to-end: the row in `aweme` carries author_sec_uid
# ---------------------------------------------------------------------------
def _build_video_downloader(
    tmp_path, database: Database
) -> tuple[VideoDownloader, DouyinAPIClient]:
    config = ConfigLoader()
    # Disable every optional side-car asset so the test only drives the
    # video + db.add_aweme path.
    config.update(
        path=str(tmp_path),
        music=False,
        cover=False,
        avatar=False,
        json=False,
        folderstyle=True,
        transcript={"enabled": False},
    )
    file_manager = FileManager(str(tmp_path))
    cookie_manager = CookieManager(str(tmp_path / ".cookies.json"))
    api_client = DouyinAPIClient({})

    downloader = VideoDownloader(
        config,
        api_client,
        file_manager,
        cookie_manager,
        database=database,
        rate_limiter=RateLimiter(max_per_second=10),
        retry_handler=RetryHandler(max_retries=1),
        queue_manager=QueueManager(max_workers=1),
    )
    return downloader, api_client


async def _fetch_db_row(db: Database, aweme_id: str) -> Optional[Dict[str, Any]]:
    conn = await db._get_conn()
    cursor = await conn.execute(
        "SELECT aweme_id, author_id, author_sec_uid FROM aweme WHERE aweme_id = ?",
        (aweme_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return {"aweme_id": row[0], "author_id": row[1], "author_sec_uid": row[2]}


async def test_video_downloader_persists_author_sec_uid_when_present(
    tmp_path, monkeypatch
):
    db = Database(db_path=str(tmp_path / "test.db"))
    await db.initialize()
    try:
        downloader, api_client = _build_video_downloader(tmp_path, db)

        async def _fake_get_session():
            return object()

        monkeypatch.setattr(api_client, "get_session", _fake_get_session)

        async def _fake_download_with_retry(self, _url, _save_path, _session, **_kwargs):
            return True

        downloader._download_with_retry = _fake_download_with_retry.__get__(
            downloader, VideoDownloader
        )

        aweme_id = "7600224486650121526"
        aweme_data = {
            "aweme_id": aweme_id,
            "desc": "has sec_uid",
            "create_time": 1707303025,
            "author": {
                "uid": "u1",
                "nickname": "Alice",
                "sec_uid": "SEC_X",
            },
            "video": {"play_addr": {"url_list": ["https://example.com/video.mp4"]}},
        }

        success = await downloader._download_aweme_assets(
            aweme_data, author_name="Alice", mode="post"
        )
        assert success is True

        row = await _fetch_db_row(db, aweme_id)
        assert row is not None, "expected aweme row to be persisted"
        assert row["author_sec_uid"] == "SEC_X"
        # sanity-check that the row is otherwise well-formed
        assert row["author_id"] == "u1"

        await api_client.close()
    finally:
        await db.close()


async def test_video_downloader_persists_null_when_sec_uid_missing(
    tmp_path, monkeypatch
):
    db = Database(db_path=str(tmp_path / "test.db"))
    await db.initialize()
    try:
        downloader, api_client = _build_video_downloader(tmp_path, db)

        async def _fake_get_session():
            return object()

        monkeypatch.setattr(api_client, "get_session", _fake_get_session)

        async def _fake_download_with_retry(self, _url, _save_path, _session, **_kwargs):
            return True

        downloader._download_with_retry = _fake_download_with_retry.__get__(
            downloader, VideoDownloader
        )

        aweme_id = "7600224486650121999"
        aweme_data = {
            "aweme_id": aweme_id,
            "desc": "missing sec_uid",
            "create_time": 1707303025,
            "author": {"uid": "u2", "nickname": "Bob"},  # no sec_uid
            "video": {"play_addr": {"url_list": ["https://example.com/video.mp4"]}},
        }

        success = await downloader._download_aweme_assets(
            aweme_data, author_name="Bob", mode="post"
        )
        assert success is True

        row = await _fetch_db_row(db, aweme_id)
        assert row is not None
        assert row["author_sec_uid"] is None
        assert row["author_id"] == "u2"

        await api_client.close()
    finally:
        await db.close()


async def test_video_downloader_persists_null_when_author_absent(
    tmp_path, monkeypatch
):
    """Entirely missing `author` object ⇒ NULL (not an exception)."""
    db = Database(db_path=str(tmp_path / "test.db"))
    await db.initialize()
    try:
        downloader, api_client = _build_video_downloader(tmp_path, db)

        async def _fake_get_session():
            return object()

        monkeypatch.setattr(api_client, "get_session", _fake_get_session)

        async def _fake_download_with_retry(self, _url, _save_path, _session, **_kwargs):
            return True

        downloader._download_with_retry = _fake_download_with_retry.__get__(
            downloader, VideoDownloader
        )

        aweme_id = "7600224486650122020"
        aweme_data = {
            "aweme_id": aweme_id,
            "desc": "no author object",
            "create_time": 1707303025,
            "video": {"play_addr": {"url_list": ["https://example.com/video.mp4"]}},
        }

        success = await downloader._download_aweme_assets(
            aweme_data, author_name="anon", mode="post"
        )
        assert success is True

        row = await _fetch_db_row(db, aweme_id)
        assert row is not None
        assert row["author_sec_uid"] is None

        await api_client.close()
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# 3. MusicDownloader: covers the other call site touched by task 2.1
# ---------------------------------------------------------------------------
class _MusicAPIClient:
    BASE_URL = "https://www.douyin.com"
    headers = {"User-Agent": "UnitTestAgent/1.0"}

    def __init__(self, detail: Dict[str, Any]):
        self._detail = detail

    async def get_music_detail(self, _music_id: str):
        return self._detail

    async def get_session(self):
        return object()


def _build_music_downloader(
    tmp_path, database: Database, api_client: _MusicAPIClient
) -> MusicDownloader:
    config = ConfigLoader()
    config.update(path=str(tmp_path), cover=False, json=False)
    file_manager = FileManager(str(tmp_path))
    return MusicDownloader(
        config=config,
        api_client=api_client,
        file_manager=file_manager,
        cookie_manager=CookieManager(str(tmp_path / ".cookies.json")),
        database=database,
        rate_limiter=RateLimiter(max_per_second=10),
        retry_handler=RetryHandler(max_retries=1),
        queue_manager=QueueManager(max_workers=1),
    )


async def test_music_downloader_persists_author_sec_uid_when_present(
    tmp_path, monkeypatch
):
    db = Database(db_path=str(tmp_path / "test.db"))
    await db.initialize()
    try:
        detail = {
            "title": "song-a",
            "author_name": "artist-a",
            "author": {"sec_uid": "SEC_MUSIC"},
            "play_url": {"url_list": ["https://example.com/music.mp3"]},
        }
        api_client = _MusicAPIClient(detail)
        downloader = _build_music_downloader(tmp_path, db, api_client)

        async def _fake_download_with_retry(self, _url, _save_path, _session, **_kwargs):
            return True

        monkeypatch.setattr(
            downloader,
            "_download_with_retry",
            _fake_download_with_retry.__get__(downloader, MusicDownloader),
        )

        result = await downloader.download({"music_id": "7600"})
        assert result.success == 1

        row = await _fetch_db_row(db, "music_7600")
        assert row is not None
        assert row["author_sec_uid"] == "SEC_MUSIC"
    finally:
        await db.close()


async def test_music_downloader_persists_null_when_sec_uid_missing(
    tmp_path, monkeypatch
):
    db = Database(db_path=str(tmp_path / "test.db"))
    await db.initialize()
    try:
        detail = {
            "title": "song-b",
            "author_name": "artist-b",
            # no author.sec_uid
            "play_url": {"url_list": ["https://example.com/music.mp3"]},
        }
        api_client = _MusicAPIClient(detail)
        downloader = _build_music_downloader(tmp_path, db, api_client)

        async def _fake_download_with_retry(self, _url, _save_path, _session, **_kwargs):
            return True

        monkeypatch.setattr(
            downloader,
            "_download_with_retry",
            _fake_download_with_retry.__get__(downloader, MusicDownloader),
        )

        result = await downloader.download({"music_id": "7601"})
        assert result.success == 1

        row = await _fetch_db_row(db, "music_7601")
        assert row is not None
        assert row["author_sec_uid"] is None
    finally:
        await db.close()
