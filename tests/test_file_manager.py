from unittest.mock import AsyncMock, MagicMock

import pytest

from storage.file_manager import FileManager


def test_file_exists_returns_false_for_missing(tmp_path):
    fm = FileManager(str(tmp_path))
    assert fm.file_exists(tmp_path / "nope.mp4") is False


def test_file_exists_returns_false_for_empty(tmp_path):
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    fm = FileManager(str(tmp_path))
    assert fm.file_exists(empty) is False


def test_file_exists_returns_true_for_non_empty(tmp_path):
    real = tmp_path / "real.mp4"
    real.write_bytes(b"data")
    fm = FileManager(str(tmp_path))
    assert fm.file_exists(real) is True


def test_get_file_size_returns_0_for_missing(tmp_path):
    fm = FileManager(str(tmp_path))
    assert fm.get_file_size(tmp_path / "nope.mp4") == 0


def test_get_save_path_creates_directories(tmp_path):
    fm = FileManager(str(tmp_path))
    path = fm.get_save_path(
        "Author", mode="post", aweme_title="Title", aweme_id="123", download_date="2024-01-01"
    )
    assert path.exists()
    assert "Author" in str(path)
    assert "post" in str(path)
    assert "123" in str(path)


def test_get_save_path_author_dir_nickname_default(tmp_path):
    """Default (style omitted or ``nickname``) keeps legacy behaviour."""
    fm = FileManager(str(tmp_path))
    path = fm.get_save_path(
        "测试作者",
        mode="post",
        aweme_title="T",
        aweme_id="1",
        author_sec_uid="MS4wLjABAAAA_abc",
        author_dir_style="nickname",
    )
    assert "测试作者" in str(path)
    assert "MS4wLjABAAAA_abc" not in str(path)


def test_get_save_path_author_dir_sec_uid(tmp_path):
    """``sec_uid`` style puts the sec_uid at the author level."""
    fm = FileManager(str(tmp_path))
    path = fm.get_save_path(
        "测试作者",
        mode="post",
        aweme_title="T",
        aweme_id="1",
        author_sec_uid="MS4wLjABAAAA_abc",
        author_dir_style="sec_uid",
    )
    parts = path.parts
    # Author layer is directly under base_path: .../{base}/{author}/{mode}/...
    author_layer = parts[parts.index("post") - 1]
    assert author_layer == "MS4wLjABAAAA_abc"


def test_get_save_path_author_dir_nickname_uid(tmp_path):
    """``nickname_uid`` joins nickname and sec_uid with an underscore."""
    fm = FileManager(str(tmp_path))
    path = fm.get_save_path(
        "测试作者",
        mode="post",
        aweme_title="T",
        aweme_id="1",
        author_sec_uid="MS4wLjABAAAA_abc",
        author_dir_style="nickname_uid",
    )
    parts = path.parts
    author_layer = parts[parts.index("post") - 1]
    assert author_layer == "测试作者_MS4wLjABAAAA_abc"


def test_get_save_path_sec_uid_missing_falls_back_to_nickname(tmp_path, caplog, monkeypatch):
    """sec_uid style with missing uid must degrade to nickname + warning."""
    import logging

    # FileManager uses a namespaced logger with ``propagate=False``; enable
    # propagation temporarily so pytest's ``caplog`` can see the warning.
    monkeypatch.setattr(logging.getLogger("FileManager"), "propagate", True)
    fm = FileManager(str(tmp_path))
    with caplog.at_level(logging.WARNING):
        path = fm.get_save_path(
            "测试作者",
            mode="live",
            aweme_title="T",
            aweme_id="room1",
            author_sec_uid=None,
            author_dir_style="sec_uid",
        )
    parts = path.parts
    author_layer = parts[parts.index("live") - 1]
    assert author_layer == "测试作者"
    assert any("sec_uid is missing" in r.message for r in caplog.records)


def test_get_save_path_nickname_uid_missing_falls_back(tmp_path, caplog, monkeypatch):
    """nickname_uid with missing sec_uid must degrade to nickname + warning."""
    import logging

    monkeypatch.setattr(logging.getLogger("FileManager"), "propagate", True)
    fm = FileManager(str(tmp_path))
    with caplog.at_level(logging.WARNING):
        path = fm.get_save_path(
            "测试作者",
            mode="music",
            aweme_title="T",
            aweme_id="music_1",
            author_sec_uid="   ",  # whitespace counts as missing
            author_dir_style="nickname_uid",
        )
    parts = path.parts
    author_layer = parts[parts.index("music") - 1]
    assert author_layer == "测试作者"
    assert any("sec_uid is missing" in r.message for r in caplog.records)


def test_get_save_path_unknown_style_falls_back(tmp_path, caplog, monkeypatch):
    """An unknown author_dir style warns and falls back to nickname."""
    import logging

    monkeypatch.setattr(logging.getLogger("FileManager"), "propagate", True)
    fm = FileManager(str(tmp_path))
    with caplog.at_level(logging.WARNING):
        path = fm.get_save_path(
            "测试作者",
            mode="post",
            aweme_title="T",
            aweme_id="1",
            author_sec_uid="MS4w_abc",
            author_dir_style="bogus",
        )
    parts = path.parts
    author_layer = parts[parts.index("post") - 1]
    assert author_layer == "测试作者"
    assert any("Unknown author_dir style" in r.message for r in caplog.records)


def test_get_save_path_group_by_mode_false_flattens(tmp_path):
    """group_by_mode=False drops the mode (e.g. ``post``) segment entirely so
    files land directly under the author directory — reproducing the legacy
    ``{base}/{author}/...`` layout (no ``POST`` folder)."""
    fm = FileManager(str(tmp_path))
    path = fm.get_save_path(
        "某作者",
        mode="post",
        aweme_title="T",
        aweme_id="1",
        folderstyle=False,
        group_by_mode=False,
    )
    parts = path.parts
    assert "post" not in parts
    # Author dir is the leaf when folderstyle is also off.
    assert path.name == "某作者"


def test_get_save_path_group_by_mode_false_keeps_leaf_folder(tmp_path):
    """group_by_mode only removes the mode layer; folderstyle's per-aweme
    subfolder is independent and must still apply when enabled."""
    fm = FileManager(str(tmp_path))
    path = fm.get_save_path(
        "某作者",
        mode="post",
        aweme_title="标题",
        aweme_id="123",
        folderstyle=True,
        folder_name="2024-01-01_标题_123",
        group_by_mode=False,
    )
    parts = path.parts
    assert "post" not in parts
    assert path.name == "2024-01-01_标题_123"
    assert path.parent.name == "某作者"


def test_get_save_path_group_by_mode_true_default_keeps_mode(tmp_path):
    """Default (omitted) keeps the mode folder — zero behaviour change."""
    fm = FileManager(str(tmp_path))
    path = fm.get_save_path(
        "某作者", mode="post", aweme_title="T", aweme_id="1", folderstyle=False
    )
    assert path.parts[-1] == "post"
    assert path.parts[-2] == "某作者"


def test_get_save_path_author_dir_user_sec_uid(tmp_path):
    """``user_sec_uid`` style prefixes the sec_uid with ``user_`` to match the
    legacy DouYin-Downloader on-disk layout."""
    fm = FileManager(str(tmp_path))
    path = fm.get_save_path(
        "某作者",
        mode="post",
        aweme_title="T",
        aweme_id="1",
        author_sec_uid="MS4wLjABAAAA_abc",
        author_dir_style="user_sec_uid",
    )
    parts = path.parts
    author_layer = parts[parts.index("post") - 1]
    assert author_layer == "user_MS4wLjABAAAA_abc"


def test_get_save_path_user_sec_uid_preserves_double_underscore(tmp_path):
    """Real sec_uids contain ``__`` (double underscore). ``user_sec_uid`` must
    NOT collapse them — otherwise new downloads land in a different folder than
    the user's existing ``user_MS4wLjABAAAA__Wip...`` directory, defeating the
    whole point of the style. (The legacy tool used the raw, un-collapsed
    sec_uid.)"""
    fm = FileManager(str(tmp_path))
    path = fm.get_save_path(
        "某作者",
        mode="post",
        aweme_title="T",
        aweme_id="1",
        author_sec_uid="MS4wLjABAAAA__WipbB2",
        author_dir_style="user_sec_uid",
    )
    parts = path.parts
    author_layer = parts[parts.index("post") - 1]
    assert author_layer == "user_MS4wLjABAAAA__WipbB2"


def test_get_save_path_user_sec_uid_missing_falls_back(tmp_path, caplog, monkeypatch):
    """user_sec_uid with missing sec_uid must degrade to nickname + warning."""
    import logging

    monkeypatch.setattr(logging.getLogger("FileManager"), "propagate", True)
    fm = FileManager(str(tmp_path))
    with caplog.at_level(logging.WARNING):
        path = fm.get_save_path(
            "某作者",
            mode="post",
            aweme_title="T",
            aweme_id="1",
            author_sec_uid=None,
            author_dir_style="user_sec_uid",
        )
    parts = path.parts
    author_layer = parts[parts.index("post") - 1]
    assert author_layer == "某作者"
    assert any("sec_uid is missing" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_download_file_atomic_write(tmp_path):
    """Downloaded file should appear only after successful completion (atomic rename)."""
    fm = FileManager(str(tmp_path))
    save_path = tmp_path / "video.mp4"
    content = b"fake video content"

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.content_length = len(content)

    async def iter_chunked(size):
        yield content

    mock_response.content = MagicMock()
    mock_response.content.iter_chunked = iter_chunked

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_response)
    ctx.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get.return_value = ctx

    result = await fm.download_file("https://example.com/v.mp4", save_path, session=mock_session)
    assert result is True
    assert save_path.exists()
    assert save_path.read_bytes() == content
    assert not save_path.with_suffix(".mp4.tmp").exists()


def _aiohttp_session_returning_status(status):
    """Build a mock aiohttp session whose ``get`` yields a response with the
    given HTTP status (and no body — the non-200 path never reads content)."""
    mock_response = AsyncMock()
    mock_response.status = status

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_response)
    ctx.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get.return_value = ctx
    return mock_session


class _FakeHttpxResponse:
    def __init__(self, status_code, content, headers):
        self.status_code = status_code
        self._content = content
        self.headers = headers

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aiter_bytes(self):
        yield self._content


class _FakeHttpxClient:
    def __init__(self, response, calls):
        self._response = response
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, headers=None):
        self._calls.append((method, url, headers))
        return self._response


@pytest.mark.asyncio
async def test_download_file_falls_back_to_httpx_on_403(tmp_path, monkeypatch):
    """Douyin's image CDN 403s aiohttp's TLS fingerprint for some assets
    (e.g. ``biz_tag=pcweb_cover`` covers) while serving httpx/curl. On a 403
    we must retry the same URL via httpx and persist the result."""
    fm = FileManager(str(tmp_path))
    save_path = tmp_path / "cover.jpg"
    content = b"jpeg-bytes-from-httpx"

    calls = []
    response = _FakeHttpxResponse(
        200, content, {"Content-Type": "image/jpeg", "Content-Length": str(len(content))}
    )
    monkeypatch.setattr(
        "storage.file_manager.httpx.AsyncClient",
        lambda *a, **k: _FakeHttpxClient(response, calls),
    )

    result = await fm.download_file(
        "https://p3-pc-sign.douyinpic.com/cover.jpg",
        save_path,
        session=_aiohttp_session_returning_status(403),
    )

    assert result is True
    assert save_path.exists()
    assert save_path.read_bytes() == content
    assert not save_path.with_suffix(".jpg.tmp").exists()
    assert len(calls) == 1  # httpx was actually used


@pytest.mark.asyncio
async def test_download_file_no_httpx_fallback_on_404(tmp_path, monkeypatch):
    """A genuine 404 (dead/expired asset) must NOT trigger the httpx fallback —
    falling back on every non-200 would double every doomed request."""
    fm = FileManager(str(tmp_path))
    save_path = tmp_path / "gone.jpg"

    calls = []

    def _boom(*a, **k):
        calls.append((a, k))
        raise AssertionError("httpx must not be called on 404")

    monkeypatch.setattr("storage.file_manager.httpx.AsyncClient", _boom)

    result = await fm.download_file(
        "https://example.com/gone.jpg",
        save_path,
        session=_aiohttp_session_returning_status(404),
    )

    assert result is False
    assert not save_path.exists()
    assert calls == []


@pytest.mark.asyncio
async def test_download_file_size_mismatch_cleans_up(tmp_path):
    fm = FileManager(str(tmp_path))
    save_path = tmp_path / "video.mp4"

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.content_length = 999

    async def iter_chunked(size):
        yield b"short"

    mock_response.content = MagicMock()
    mock_response.content.iter_chunked = iter_chunked

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_response)
    ctx.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get.return_value = ctx

    result = await fm.download_file("https://example.com/v.mp4", save_path, session=mock_session)
    assert result is False
    assert not save_path.exists()
    assert not save_path.with_suffix(".mp4.tmp").exists()
