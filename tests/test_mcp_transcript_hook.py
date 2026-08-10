"""Unit tests for MCP transcript hook (mocked extract_text; no network)."""

from __future__ import annotations

from pathlib import Path

import pytest

from local_pipeline import mcp_transcript as mt


def test_skip_when_transcript_exists(tmp_path: Path):
    stem = "2026-01-01_title_123"
    txt = tmp_path / f"{stem}.transcript.txt"
    txt.write_text("already here\n", encoding="utf-8")

    def boom(*_a, **_k):
        raise AssertionError("extract must not run")

    result = mt.process_aweme_transcript(
        aweme_id="123",
        save_dir=tmp_path,
        file_stem=stem,
        mcp_cfg={"enabled": True},
        extract_fn=boom,
    )
    assert result["status"] == "skipped"
    assert result["reason"] == "existing_transcript"


def test_missing_api_key_writes_err_and_does_not_raise(tmp_path: Path, monkeypatch):
    for name in ("DOUYIN_API_KEY", "API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    stem = "stem_456"
    result = mt.process_aweme_transcript(
        aweme_id="456",
        save_dir=tmp_path,
        file_stem=stem,
        mcp_cfg={"api_key_env": "DOUYIN_API_KEY"},
        extract_fn=lambda **_k: "nope",
    )
    assert result["status"] == "failed"
    assert result["reason"] == "missing_api_key"
    err = tmp_path / f"{stem}.transcript.err.txt"
    assert err.is_file()
    assert "missing API key" in err.read_text(encoding="utf-8")


def test_extract_ok_writes_txt(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DOUYIN_API_KEY", "sk-test-not-real")

    def fake_extract(share_link, **_kwargs):
        assert "789" in share_link
        return "hello transcript"

    stem = "stem_789"
    result = mt.process_aweme_transcript(
        aweme_id="789",
        save_dir=tmp_path,
        file_stem=stem,
        mcp_cfg={},
        extract_fn=fake_extract,
    )
    assert result["status"] == "ok"
    txt = tmp_path / f"{stem}.transcript.txt"
    assert txt.read_text(encoding="utf-8").strip() == "hello transcript"


def test_extract_failure_writes_err(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DOUYIN_API_KEY", "sk-test-not-real")

    def fake_extract(*_args, **_kwargs):
        raise RuntimeError("asr boom")

    stem = "stem_fail"
    result = mt.process_aweme_transcript(
        aweme_id="fail1",
        save_dir=tmp_path,
        file_stem=stem,
        mcp_cfg={},
        extract_fn=fake_extract,
    )
    assert result["status"] == "failed"
    err = tmp_path / f"{stem}.transcript.err.txt"
    assert "asr boom" in err.read_text(encoding="utf-8")


def test_factory_day_key_uses_shanghai_not_utc_prefix():
    # 2026-08-10 03:00+08 == 2026-08-09 19:00Z — must map to local 08-10
    assert mt.factory_day_key("2026-08-09T19:00:40Z") == "2026-08-10"
    assert mt.factory_day_key("2026-08-10T01:00:00+08:00") == "2026-08-10"


def test_factory_append_uses_local_day_key(tmp_path: Path):
    media_root = tmp_path / "media"
    media_root.mkdir()
    record = mt.build_factory_record(
        aweme_id="111",
        author="a",
        title="t",
        media_path=str(media_root / "x.mp4"),
        transcript_path=str(media_root / "x.transcript.txt"),
        transcript_status="ok",
    )
    # Force a UTC evening ts that is next local morning
    record["ts"] = "2026-08-09T19:00:40Z"
    mt.append_factory_records(media_root, record)
    factory = media_root / "_factory"
    assert (factory / "delta-2026-08-10.jsonl").is_file()
    assert not (factory / "delta-2026-08-09.jsonl").is_file()


def test_html_parse_failure_detector():
    assert mt._is_html_parse_failure("KeyError: 'videoInfoRes'")
    assert mt._is_html_parse_failure("ValueError: 从HTML中解析视频信息失败")
    assert not mt._is_html_parse_failure("asr boom")


def test_factory_append(tmp_path: Path):
    media_root = tmp_path / "media"
    media_root.mkdir()
    record = mt.build_factory_record(
        aweme_id="111",
        author="a",
        title="t",
        media_path=str(media_root / "x.mp4"),
        transcript_path=str(media_root / "x.transcript.txt"),
        transcript_status="ok",
    )
    mt.append_factory_records(media_root, record)
    factory = media_root / "_factory"
    assert (factory / "manifest.jsonl").is_file()
    deltas = list(factory.glob("delta-*.jsonl"))
    assert len(deltas) == 1
    line = deltas[0].read_text(encoding="utf-8").strip()
    assert "MED-111" in line
    assert '"transcript_status": "ok"' in line


def test_title_from_stem():
    stem = "2026-06-06_哈喽大家好_7648284266559835402"
    assert mt.title_from_stem(stem, "7648284266559835402") == "哈喽大家好"


@pytest.mark.asyncio
async def test_async_wrapper(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DOUYIN_API_KEY", "sk-test")

    result = await mt.process_aweme_transcript_async(
        aweme_id="async1",
        save_dir=tmp_path,
        file_stem="async_stem",
        mcp_cfg={},
        extract_fn=lambda *_a, **_k: "async text",
    )
    assert result["status"] == "ok"
