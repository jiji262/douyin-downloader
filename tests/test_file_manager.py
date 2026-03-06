import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
    path = fm.get_save_path("Author", mode="post", aweme_title="Title", aweme_id="123", download_date="2024-01-01")
    assert path.exists()
    assert "Author" in str(path)
    assert "post" in str(path)
    assert "123" in str(path)


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
