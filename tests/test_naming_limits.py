"""长标题只在落盘预算不足时裁短,作品 ID 和附属文件仍可用。"""

import os
from datetime import datetime
from pathlib import Path

import pytest

from storage import FileManager
from tests.test_downloader_naming_templates import _build_downloader, _make_aweme
from utils.naming import DEFAULT_FILE_TEMPLATE, render_template


def test_template_keeps_complete_title_beyond_old_limit():
    title = "a" * 150
    assert (
        render_template(
            DEFAULT_FILE_TEMPLATE,
            {"date": "2026-11-01", "title": title, "id": "7419999999999999999"},
        )
        == "2026-11-01_" + title + "_7419999999999999999"
    )


def test_template_preserves_id_when_author_and_title_overflow():
    name = render_template(
        "{author}_{title}_{id}",
        {"author": "作" * 300, "title": "题" * 300, "id": "7419999999999999999"},
    )
    assert name.endswith("_7419999999999999999")
    assert len(name.encode("utf-8")) <= 239


def _plan(root, *, title, folderstyle=True, suffixes=(".mp4.tmp",), **config):
    from core.output_naming import plan_output_paths

    return plan_output_paths(
        file_manager=FileManager(str(root)),
        config={"folderstyle": folderstyle, **config},
        context={
            "author": "作者",
            "date": "2026-11-01",
            "title": title,
            "id": "7419999999999999999",
        },
        mode="post",
        with_transcript=True,
        suffixes=suffixes,
    )


def test_short_names_keep_existing_layout(tmp_path):
    directory, stem = _plan(tmp_path, title="日常")
    assert directory == tmp_path / "作者" / "post" / "2026-11-01_日常_7419999999999999999"
    assert stem == "2026-11-01_日常_7419999999999999999"


@pytest.mark.parametrize(
    "title",
    ["a" * 150, "中" * 300, "😀" * 200, "e\u0301" * 200],
    ids=["ascii", "cjk", "emoji", "combining"],
)
def test_planned_long_names_and_sidecars_can_be_written(tmp_path, title):
    suffixes = (".mp4.tmp", "_comments.json", ".transcript.json", "_live_1000.webp.tmp")
    directory, stem = _plan(tmp_path, title=title, suffixes=suffixes)
    assert stem.endswith("_7419999999999999999")
    assert directory.name.endswith("_7419999999999999999")
    for suffix in suffixes:
        path = directory / (stem + suffix)
        path.write_bytes(b"media")
        assert path.read_bytes() == b"media"
    if title == "a" * 150:
        assert title in stem


def test_windows_path_budget_counts_folder_filename_and_utf16(tmp_path, monkeypatch):
    from utils import naming_limits

    monkeypatch.setattr(
        naming_limits.PathLimits,
        "for_directory",
        classmethod(lambda cls, _: cls(component_limit=255, path_limit=259, windows=True)),
    )
    directory, stem = _plan(tmp_path, title="😀" * 300, suffixes=("_comments.json",))
    final = str((directory / (stem + "_comments.json")).absolute())
    assert len(final.encode("utf-16-le")) // 2 <= 259
    assert stem.endswith("_7419999999999999999")
    assert "😀" in stem


def test_deep_directory_reports_that_even_id_cannot_fit(tmp_path, monkeypatch):
    from utils import naming_limits

    monkeypatch.setattr(
        naming_limits.PathLimits,
        "for_directory",
        classmethod(lambda cls, _: cls(component_limit=255, path_limit=10, windows=True)),
    )
    with pytest.raises(ValueError, match="保存目录"):
        _plan(tmp_path, title="title")


def test_folder_disabled_leaves_more_budget_for_title(tmp_path, monkeypatch):
    from utils import naming_limits

    monkeypatch.setattr(
        naming_limits.PathLimits,
        "for_directory",
        classmethod(lambda cls, _: cls(component_limit=255, path_limit=259, windows=True)),
    )
    _, nested = _plan(tmp_path, title="a" * 300)
    directory, flat = _plan(tmp_path, title="a" * 300, folderstyle=False)
    assert directory == tmp_path / "作者" / "post"
    assert len(flat) > len(nested)


def test_actual_absolute_parent_is_used_for_relative_root(tmp_path, monkeypatch):
    from utils import naming_limits

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        naming_limits.PathLimits,
        "for_directory",
        classmethod(lambda cls, _: cls(component_limit=255, path_limit=259, windows=True)),
    )
    directory, stem = _plan(Path("Downloaded"), title="a" * 300)
    assert len(str((directory / (stem + ".mp4.tmp")).absolute())) <= 259


def test_custom_transcript_root_is_included_in_path_budget(tmp_path, monkeypatch):
    from utils import naming_limits

    monkeypatch.setattr(
        naming_limits.PathLimits,
        "for_directory",
        classmethod(lambda cls, _: cls(component_limit=255, path_limit=259, windows=True)),
    )
    transcript_root = tmp_path / ("transcripts" * 6)
    directory, stem = _plan(
        tmp_path,
        title="a" * 300,
        transcript={"enabled": True, "output_dir": str(transcript_root)},
    )
    output = transcript_root / directory.relative_to(tmp_path) / (stem + ".transcript.json")
    assert len(str(output.resolve()).encode("utf-16-le")) // 2 <= 259
    assert stem.endswith("7419999999999999999")


@pytest.mark.skipif(os.name == "nt", reason="创建测试 symlink 需要 Windows 额外权限")
def test_budget_also_counts_longer_resolved_symlink_path(tmp_path, monkeypatch):
    from utils import naming_limits

    monkeypatch.setattr(
        naming_limits.PathLimits,
        "for_directory",
        classmethod(lambda cls, _: cls(component_limit=255, path_limit=259, windows=True)),
    )
    target = tmp_path / ("target" * 6)
    target.mkdir()
    root = tmp_path / "link"
    root.symlink_to(target, target_is_directory=True)
    directory, stem = _plan(root, title="a" * 300)
    actual = (directory / (stem + ".mp4.tmp")).resolve()
    assert len(str(actual).encode("utf-16-le")) // 2 <= 259


@pytest.mark.skipif(os.name == "nt", reason="创建测试 symlink 需要 Windows 额外权限")
def test_transcript_mirror_falls_back_for_author_symlink_outside_root(tmp_path):
    root = tmp_path / "downloads"
    root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (root / "作者").symlink_to(external, target_is_directory=True)
    directory, stem = _plan(
        root,
        title="日常",
        transcript={"enabled": True, "output_dir": str(tmp_path / "text")},
    )
    assert directory.parent == root / "作者" / "post"
    assert stem == "2026-11-01_日常_7419999999999999999"


def test_disabled_transcript_directory_does_not_restrict_media_names(tmp_path, monkeypatch):
    from utils import naming_limits

    monkeypatch.setattr(
        naming_limits.PathLimits,
        "for_directory",
        classmethod(lambda cls, _: cls(component_limit=255, path_limit=259, windows=True)),
    )
    _, ordinary = _plan(tmp_path, title="a" * 300)
    _, disabled = _plan(
        tmp_path,
        title="a" * 300,
        transcript={"enabled": False, "output_dir": str(tmp_path / ("t" * 200))},
    )
    assert disabled == ordinary


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "title", ["a" * 150, "中" * 300, "😀" * 200], ids=["ascii", "cjk", "emoji"]
)
async def test_download_writes_long_titles_with_complete_id(tmp_path, monkeypatch, title):
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(music=False, cover=False, avatar=False, json=True, folderstyle=True)
    saved = []

    async def download(_url, path, _session, **_):
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(b"media")
        temporary.replace(path)
        saved.append(path)
        return True

    async def session():
        return object()

    monkeypatch.setattr(downloader, "_download_with_retry", download)
    monkeypatch.setattr(api_client, "get_session", session)
    aweme = _make_aweme(int(datetime(2024, 3, 15).timestamp()))
    aweme["desc"] = title
    try:
        assert await downloader._download_aweme_assets(aweme, author_name="作者", mode="post")
        video = saved[0]
        assert video.stem.endswith("_7600000000000000000")
        assert video.parent.name.endswith("_7600000000000000000")
        assert video.with_name(video.stem + "_data.json").exists()
        if title.startswith("a"):
            assert title in video.name
        else:
            assert len(video.stem.encode("utf-8")) > 200
    finally:
        await api_client.close()


@pytest.mark.asyncio
async def test_gallery_ignores_deep_transcript_directory(tmp_path, monkeypatch):
    from utils import naming_limits

    monkeypatch.setattr(
        naming_limits.PathLimits,
        "for_directory",
        classmethod(lambda cls, _: cls(component_limit=255, path_limit=259, windows=True)),
    )
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(
        transcript={"enabled": True, "output_dir": str(tmp_path / ("t" * 200))},
    )
    aweme = _make_aweme(int(datetime(2024, 3, 15).timestamp()))
    aweme.update(aweme_type=68, images=[{"url_list": ["https://example.com/1.jpg"]}])
    try:
        result = downloader._build_aweme_file_context(aweme, "作者", "post")
        assert result["media_type"] == "gallery"
        assert result["file_stem"].endswith("_7600000000000000000")
    finally:
        await api_client.close()


@pytest.mark.asyncio
async def test_download_reserves_windows_path_for_nested_folder(tmp_path, monkeypatch):
    from utils import naming_limits

    monkeypatch.setattr(
        naming_limits.PathLimits,
        "for_directory",
        classmethod(lambda cls, _: cls(component_limit=255, path_limit=259, windows=True)),
    )
    downloader, api_client = _build_downloader(tmp_path)
    aweme = _make_aweme(int(datetime(2024, 3, 15).timestamp()))
    aweme["desc"] = "a" * 300
    try:
        result = downloader._build_aweme_file_context(aweme, "作者", "post")
        path = result["save_dir"] / (result["file_stem"] + "_comments.json")
        assert len(str(path.resolve()).encode("utf-16-le")) // 2 <= 259
        assert result["file_stem"].endswith("_7600000000000000000")
    finally:
        await api_client.close()
