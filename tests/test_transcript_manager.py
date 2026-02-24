import asyncio

import pytest

from config import ConfigLoader
from core.transcript_manager import TranscriptManager
from storage import FileManager


class _FakeDatabase:
    def __init__(self):
        self.rows = []

    async def upsert_transcript_job(self, payload):
        self.rows.append(payload)


def test_transcript_default_disabled():
    loader = ConfigLoader()
    transcript_cfg = loader.get("transcript", {})

    assert transcript_cfg.get("enabled") is False
    assert (
        transcript_cfg.get("api_url")
        == "https://api.openai.com/v1/audio/transcriptions"
    )


def test_transcript_skip_when_missing_api_key(tmp_path):
    config = ConfigLoader()
    config.update(
        transcript={
            "enabled": True,
            "api_key_env": "OPENAI_API_KEY",
            "api_key": "",
            "output_dir": "",
            "response_formats": ["txt", "json"],
        }
    )

    file_manager = FileManager(str(tmp_path / "Downloaded"))
    database = _FakeDatabase()
    manager = TranscriptManager(config, file_manager, database=database)

    video_path = tmp_path / "Downloaded" / "author" / "post" / "demo.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"video")

    result = asyncio.run(manager.process_video(video_path, aweme_id="123"))

    assert result["status"] == "skipped"
    assert result["reason"] == "missing_api_key"
    assert database.rows[-1]["status"] == "skipped"
    assert database.rows[-1]["skip_reason"] == "missing_api_key"


def test_transcript_output_dir_defaults_to_video_dir(tmp_path):
    config = ConfigLoader()
    config.update(transcript={"enabled": True, "output_dir": ""})
    file_manager = FileManager(str(tmp_path / "Downloaded"))
    manager = TranscriptManager(config, file_manager, database=None)

    video_path = tmp_path / "Downloaded" / "a" / "post" / "x.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"video")

    resolved = manager.resolve_output_dir(video_path)
    assert resolved == video_path.parent


def test_transcript_output_dir_mirrors_video_tree(tmp_path):
    config = ConfigLoader()
    output_root = tmp_path / "Transcripts"
    config.update(transcript={"enabled": True, "output_dir": str(output_root)})
    file_manager = FileManager(str(tmp_path / "Downloaded"))
    manager = TranscriptManager(config, file_manager, database=None)

    video_path = tmp_path / "Downloaded" / "a" / "post" / "2026-02-18_demo" / "x.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"video")

    resolved = manager.resolve_output_dir(video_path)
    expected = output_root / "a" / "post" / "2026-02-18_demo"
    assert resolved == expected


def test_transcript_file_names(tmp_path):
    config = ConfigLoader()
    config.update(transcript={"enabled": True, "output_dir": ""})
    file_manager = FileManager(str(tmp_path / "Downloaded"))
    manager = TranscriptManager(config, file_manager, database=None)

    video_path = tmp_path / "Downloaded" / "a" / "post" / "demo.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"video")

    text_path, json_path = manager.build_output_paths(video_path)
    assert text_path.name == "demo.transcript.txt"
    assert json_path.name == "demo.transcript.json"
