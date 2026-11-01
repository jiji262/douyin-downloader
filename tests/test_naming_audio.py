"""长媒体名不会进入系统临时目录,上传和转写成品仍保留作品名。"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from config import ConfigLoader
from core import audio_extraction
from core.transcript_manager import TranscriptManager
from storage import FileManager
from tests.test_audio_extraction import _FakeProcess


@pytest.mark.asyncio
async def test_long_title_transcription_uses_short_temporary_audio_name(tmp_path, monkeypatch):
    root = tmp_path / "media"
    file_manager = FileManager(str(root))
    video = root / ("a" * 200 + "_7419999999999999999.mp4")
    video.write_bytes(b"video")
    config = ConfigLoader(None)
    config.update(transcript={"enabled": True, "api_key": "test-key"})
    manager = TranscriptManager(config, file_manager, database=None)
    monkeypatch.setattr(audio_extraction.FfmpegLocator, "locate", AsyncMock(return_value="ffmpeg"))

    async def ffmpeg(*args, **kwargs):
        Path(args[-1]).write_bytes(b"audio")
        return _FakeProcess(returncode=0)

    async def upload(**kwargs):
        path = kwargs["file_path"]
        assert path.name == "audio.mp3"
        assert path.read_bytes() == b"audio"
        assert kwargs["filename"] == video.stem + ".mp3"
        return {"text": "transcript"}

    monkeypatch.setattr(audio_extraction.asyncio, "create_subprocess_exec", ffmpeg)
    monkeypatch.setattr(manager, "_call_openai_transcription", upload)
    result = await manager.process_video(video, "7419999999999999999")
    assert result["status"] == "success"
    assert video.with_suffix(".transcript.txt").read_text() == "transcript"
    assert video.read_bytes() == b"video"
