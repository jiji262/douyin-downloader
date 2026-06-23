"""Tests for the audio-extraction wiring inside
:class:`core.transcript_manager.TranscriptManager`.

Complements ``tests/test_transcript_manager.py`` (which predates this
spec and exercises the older paths). New behaviour covered here:

- Pre-upload audio extraction (the success path).
- Hard-fail on extraction error: no OpenAI call, DB recorded, return
  dict carries ``reason="audio_extract_failed"``.
- Source-audio passthrough (``.m4a`` / ``.mp3`` / etc.).
- ``upload_audio_only=False`` legacy fallback.
- Tempdir cleanup is best-effort (failure logs WARNING, doesn't change
  the task status).
- ``_resolve_api_key`` priority (env → settings → none).
- Property 2 (api_key never appears in logs).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import pytest

from config import ConfigLoader
from core import transcript_manager as tm_mod
from core.audio_extraction import (
    AudioExtractError,
    FfmpegLocator,
    FfmpegNonZeroExit,
)
from core.transcript_manager import TranscriptManager
from storage import FileManager

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeDatabase:
    """Captures upserts so tests can assert what was written."""

    def __init__(self) -> None:
        self.transcript_jobs: List[Dict[str, Any]] = []

    async def upsert_transcript_job(self, payload: Dict[str, Any]) -> None:
        self.transcript_jobs.append(payload)


def _build_manager(
    tmp_path: Path,
    *,
    transcript: Optional[Dict[str, Any]] = None,
    api_key_env_value: Optional[str] = None,
    monkeypatch: Optional[pytest.MonkeyPatch] = None,
) -> tuple[TranscriptManager, _FakeDatabase, Path]:
    """Build a TranscriptManager with a sandboxed FileManager + DB."""
    download_root = tmp_path / "Downloaded"
    download_root.mkdir(parents=True, exist_ok=True)

    config = ConfigLoader(None)
    config.update(
        path=str(download_root),
        transcript=transcript
        or {
            "enabled": True,
            "output_dir": "",
            "model": "gpt-4o-mini-transcribe",
            "api_url": "https://api.openai.com/v1/audio/transcriptions",
            "api_key_env": "OPENAI_API_KEY",
            "api_key": "",
            "upload_audio_only": True,
        },
    )

    if api_key_env_value is not None and monkeypatch is not None:
        monkeypatch.setenv("OPENAI_API_KEY", api_key_env_value)
    elif monkeypatch is not None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    file_manager = FileManager(str(download_root))
    database = _FakeDatabase()
    manager = TranscriptManager(config, file_manager, database=database)
    return manager, database, download_root


@pytest.fixture(autouse=True)
def _reset_locator() -> None:
    """Each test gets a fresh FfmpegLocator singleton (so an earlier test
    that probed for unavailable ffmpeg can't poison later tests)."""
    FfmpegLocator.reset_for_tests()
    yield
    FfmpegLocator.reset_for_tests()


def _make_video(root: Path, name: str = "demo.mp4") -> Path:
    """Create a fake video file inside the FileManager's base path."""
    video_path = root / "author" / "post" / name
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"\x00" * 16)
    return video_path


# ---------------------------------------------------------------------------
# Property 5: env > settings > none
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "env_val,settings_val,expected",
    [
        ("env-key", "settings-key", "env-key"),
        ("", "settings-key", "settings-key"),
        ("   ", "settings-key", "settings-key"),  # whitespace = empty
        ("env-key", "", "env-key"),
        ("", "", ""),
    ],
)
def test_resolve_api_key_priority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    env_val: str,
    settings_val: str,
    expected: str,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", env_val)
    config = ConfigLoader(None)
    config.update(
        transcript={
            "enabled": True,
            "api_key_env": "OPENAI_API_KEY",
            "api_key": settings_val,
        }
    )
    file_manager = FileManager(str(tmp_path / "Downloaded"))
    manager = TranscriptManager(config, file_manager, database=None)
    assert manager._resolve_api_key() == expected


# ---------------------------------------------------------------------------
# Successful audio extraction + upload
# ---------------------------------------------------------------------------


async def test_process_video_extracts_audio_and_uploads_mp3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, db, download_root = _build_manager(
        tmp_path, api_key_env_value="sk-DEADBEEF12345678", monkeypatch=monkeypatch
    )
    video = _make_video(download_root)

    # Stub extract_audio to return a fake mp3 in a tmp dir.
    async def fake_extract(video_path: Path, out_dir: Path, **kwargs):
        out_dir.mkdir(parents=True, exist_ok=True)
        mp3 = out_dir / f"{video_path.stem}.mp3"
        mp3.write_bytes(b"\xff\xfb\x00fake-mp3-bytes")
        return mp3

    captured: Dict[str, Any] = {}

    async def fake_call(
        *,
        api_key: str,
        file_path: Path,
        filename: str,
        content_type: str,
        model: str,
    ) -> Dict[str, Any]:
        captured.update(
            api_key=api_key,
            file_path=file_path,
            filename=filename,
            content_type=content_type,
            model=model,
            uploaded_size=file_path.stat().st_size,
        )
        return {"text": "hello world"}

    monkeypatch.setattr(tm_mod, "extract_audio", fake_extract)
    monkeypatch.setattr(
        manager, "_call_openai_transcription", fake_call
    )

    result = await manager.process_video(video, aweme_id="aw1")

    assert result["status"] == "success"
    # We uploaded the mp3, NOT the source video.
    assert captured["filename"] == "demo.mp3"
    assert captured["content_type"] == "audio/mpeg"
    assert captured["uploaded_size"] == len(b"\xff\xfb\x00fake-mp3-bytes")
    # Outputs were written to disk.
    assert Path(result["text_path"]).read_text() == "hello world"
    # DB shows success.
    assert db.transcript_jobs[-1]["status"] == "success"


# ---------------------------------------------------------------------------
# Property 3: extraction failure ⇒ no fallback ⇒ no OpenAI call
# ---------------------------------------------------------------------------


async def test_process_video_audio_extract_failure_does_not_call_openai(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, db, download_root = _build_manager(
        tmp_path, api_key_env_value="sk-test123", monkeypatch=monkeypatch
    )
    video = _make_video(download_root)

    async def failing_extract(video_path: Path, out_dir: Path, **kwargs):
        raise FfmpegNonZeroExit("exit=1; stderr_tail='boom'")

    open_mock = AsyncMock()
    monkeypatch.setattr(tm_mod, "extract_audio", failing_extract)
    monkeypatch.setattr(
        manager, "_call_openai_transcription", open_mock
    )

    result = await manager.process_video(video, aweme_id="aw_fail")

    assert result["status"] == "failed"
    assert result["reason"] == "audio_extract_failed"
    err_msg = result["error"]
    assert err_msg.startswith("audio_extract_failed: nonzero_exit_code")
    assert "exit=1" in err_msg

    # OpenAI must not have been called.
    open_mock.assert_not_called()

    # DB has the same error_message.
    assert db.transcript_jobs[-1]["status"] == "failed"
    assert db.transcript_jobs[-1]["error_message"] == err_msg
    assert db.transcript_jobs[-1]["skip_reason"] is None


async def test_process_video_audio_extract_error_classes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All AudioExtractError subclasses end up classified the same way."""
    manager, db, download_root = _build_manager(
        tmp_path, api_key_env_value="sk-test", monkeypatch=monkeypatch
    )
    video = _make_video(download_root)

    class _CustomError(AudioExtractError):
        cause = "custom_cause"

    async def failing_extract(video_path: Path, out_dir: Path, **kwargs):
        raise _CustomError("trace_id=abc")

    monkeypatch.setattr(tm_mod, "extract_audio", failing_extract)
    open_mock = AsyncMock()
    monkeypatch.setattr(
        manager, "_call_openai_transcription", open_mock
    )

    result = await manager.process_video(video, aweme_id="aw_x")
    assert result["reason"] == "audio_extract_failed"
    assert "audio_extract_failed: custom_cause: trace_id=abc" in result["error"]
    open_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Source-audio passthrough
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "extension,expected_content_type",
    [
        (".m4a", "audio/mp4"),
        (".mp3", "audio/mpeg"),
        (".wav", "audio/wav"),
        (".aac", "audio/aac"),
        (".opus", "audio/ogg"),
        (".flac", "audio/flac"),
        (".ogg", "audio/ogg"),
    ],
)
async def test_process_video_source_audio_passthrough(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extension: str,
    expected_content_type: str,
) -> None:
    """When source is already audio, we skip extraction and upload as-is."""
    manager, db, download_root = _build_manager(
        tmp_path, api_key_env_value="sk-test", monkeypatch=monkeypatch
    )
    audio_path = _make_video(download_root, name=f"clip{extension}")

    extract_mock = AsyncMock()
    monkeypatch.setattr(tm_mod, "extract_audio", extract_mock)

    captured: Dict[str, Any] = {}

    async def fake_call(*, api_key, file_path, filename, content_type, model):
        captured.update(
            file_path=file_path,
            filename=filename,
            content_type=content_type,
        )
        return {"text": "ok"}

    monkeypatch.setattr(manager, "_call_openai_transcription", fake_call)

    result = await manager.process_video(audio_path, aweme_id="aw_audio")

    assert result["status"] == "success"
    extract_mock.assert_not_called()
    assert captured["file_path"] == audio_path
    assert captured["filename"] == audio_path.name
    assert captured["content_type"] == expected_content_type


# ---------------------------------------------------------------------------
# upload_audio_only=False legacy path
# ---------------------------------------------------------------------------


async def test_process_video_legacy_upload_when_flag_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, db, download_root = _build_manager(
        tmp_path,
        transcript={
            "enabled": True,
            "output_dir": "",
            "model": "whisper-1",
            "api_url": "https://api.openai.com/v1/audio/transcriptions",
            "api_key_env": "OPENAI_API_KEY",
            "api_key": "",
            "upload_audio_only": False,  # ← legacy
        },
        api_key_env_value="sk-test",
        monkeypatch=monkeypatch,
    )
    video = _make_video(download_root)

    extract_mock = AsyncMock()
    monkeypatch.setattr(tm_mod, "extract_audio", extract_mock)

    captured: Dict[str, Any] = {}

    async def fake_call(*, api_key, file_path, filename, content_type, model):
        captured.update(
            file_path=file_path, filename=filename, content_type=content_type
        )
        return {"text": "ok"}

    monkeypatch.setattr(manager, "_call_openai_transcription", fake_call)

    result = await manager.process_video(video, aweme_id="aw_legacy")
    assert result["status"] == "success"
    extract_mock.assert_not_called()
    assert captured["file_path"] == video
    assert captured["filename"] == video.name
    assert captured["content_type"] == "video/mp4"


# ---------------------------------------------------------------------------
# Tempdir cleanup is best-effort
# ---------------------------------------------------------------------------


async def test_process_video_tempdir_cleanup_failure_only_warns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    manager, db, download_root = _build_manager(
        tmp_path, api_key_env_value="sk-test", monkeypatch=monkeypatch
    )
    video = _make_video(download_root)

    async def fake_extract(video_path: Path, out_dir: Path, **kwargs):
        out_dir.mkdir(parents=True, exist_ok=True)
        mp3 = out_dir / f"{video_path.stem}.mp3"
        mp3.write_bytes(b"x")
        return mp3

    async def fake_call(*, api_key, file_path, filename, content_type, model):
        return {"text": "ok"}

    monkeypatch.setattr(tm_mod, "extract_audio", fake_extract)
    monkeypatch.setattr(manager, "_call_openai_transcription", fake_call)

    # Patch TemporaryDirectory.cleanup to raise (e.g. Windows file lock).
    real_td = tm_mod.tempfile.TemporaryDirectory

    class _BrokenTD(real_td):  # type: ignore[misc]
        def cleanup(self):
            raise OSError("simulated lock")

    monkeypatch.setattr(tm_mod.tempfile, "TemporaryDirectory", _BrokenTD)

    caplog.set_level(logging.WARNING, logger="TranscriptManager")
    # The TranscriptManager logger is configured with propagate=False
    # (see utils.logger.setup_logger), so we must attach caplog's handler
    # directly to capture its records.
    tm_logger = logging.getLogger("TranscriptManager")
    tm_logger.addHandler(caplog.handler)
    try:
        result = await manager.process_video(video, aweme_id="aw_cleanup")
    finally:
        tm_logger.removeHandler(caplog.handler)

    # Task itself reports success; cleanup error is a WARNING, not an
    # error.
    assert result["status"] == "success"
    assert any(
        "Failed to clean up transcript audio temp dir" in rec.message
        for rec in caplog.records
    )


# ---------------------------------------------------------------------------
# Property 2: api_key never appears in log messages
# ---------------------------------------------------------------------------


async def test_process_video_does_not_log_api_key_plaintext(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinel_key = "sk-PLAINTEXT-SECRET-DO-NOT-LEAK-12345"
    manager, db, download_root = _build_manager(
        tmp_path, api_key_env_value=sentinel_key, monkeypatch=monkeypatch
    )
    video = _make_video(download_root)

    async def fake_extract(video_path: Path, out_dir: Path, **kwargs):
        out_dir.mkdir(parents=True, exist_ok=True)
        mp3 = out_dir / f"{video_path.stem}.mp3"
        mp3.write_bytes(b"x")
        return mp3

    async def fake_call(*, api_key, file_path, filename, content_type, model):
        # Verify the manager DID resolve the env-var key.
        assert api_key == sentinel_key
        return {"text": "ok"}

    monkeypatch.setattr(tm_mod, "extract_audio", fake_extract)
    monkeypatch.setattr(manager, "_call_openai_transcription", fake_call)

    caplog.set_level(logging.DEBUG)
    # TranscriptManager logger has propagate=False (see utils.logger);
    # attach the caplog handler directly to make sure we'd see any leak.
    tm_logger = logging.getLogger("TranscriptManager")
    tm_logger.addHandler(caplog.handler)
    try:
        await manager.process_video(video, aweme_id="aw_secret")
    finally:
        tm_logger.removeHandler(caplog.handler)

    full_log = "\n".join(rec.getMessage() for rec in caplog.records)
    assert sentinel_key not in full_log


async def test_call_openai_transcription_redacts_api_key_in_error_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the upstream returns a 4xx body that echoes the bearer
    token, the resulting RuntimeError must NOT carry the raw key —
    otherwise it lands in ``transcript_jobs.error_message`` and a
    later DB dump leaks it."""
    sentinel = "sk-LEAK-PROBE-1234567890ABCDEF"
    manager, db, download_root = _build_manager(
        tmp_path, api_key_env_value=sentinel, monkeypatch=monkeypatch
    )
    video = _make_video(download_root)

    # Skip extract_audio so we go straight to _call_openai_transcription.
    async def passthrough_extract(video_path, out_dir, **kwargs):
        out_dir.mkdir(parents=True, exist_ok=True)
        mp3 = out_dir / f"{video_path.stem}.mp3"
        mp3.write_bytes(b"\x00")
        return mp3

    monkeypatch.setattr(tm_mod, "extract_audio", passthrough_extract)

    # Stub aiohttp's POST to return a 401 body that echoes the bearer
    # token exactly the way some misbehaving upstreams do.
    class FakeResp:
        status = 401

        async def text(self):
            return f"<html>auth header was: Bearer {sentinel}</html>"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class FakePostCtx:
        async def __aenter__(self):
            return FakeResp()

        async def __aexit__(self, *exc):
            return False

    class FakeSession:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def post(self, *a, **k):
            return FakePostCtx()

    monkeypatch.setattr("aiohttp.ClientSession", FakeSession)

    result = await manager.process_video(video, aweme_id="aw_redact")
    assert result["status"] == "failed"
    err_msg = result["error"]
    assert sentinel not in err_msg, (
        f"raw api_key leaked into error_message: {err_msg!r}"
    )
    # Masked form should be present so the user can still tell which
    # key was used.
    assert "sk-L...CDEF" in err_msg
    # DB record matches.
    db_msg = db.transcript_jobs[-1]["error_message"]
    assert sentinel not in db_msg
    assert "sk-L...CDEF" in db_msg


# ---------------------------------------------------------------------------
# Missing api_key still produces the legacy skip path
# ---------------------------------------------------------------------------


async def test_process_video_missing_api_key_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, db, download_root = _build_manager(
        tmp_path, api_key_env_value=None, monkeypatch=monkeypatch
    )
    video = _make_video(download_root)

    extract_mock = AsyncMock()
    open_mock = AsyncMock()
    monkeypatch.setattr(tm_mod, "extract_audio", extract_mock)
    monkeypatch.setattr(manager, "_call_openai_transcription", open_mock)

    result = await manager.process_video(video, aweme_id="aw_no_key")

    assert result == {"status": "skipped", "reason": "missing_api_key"}
    extract_mock.assert_not_called()
    open_mock.assert_not_called()
    assert db.transcript_jobs[-1]["skip_reason"] == "missing_api_key"
