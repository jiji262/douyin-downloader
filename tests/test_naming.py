"""Tests for utils.naming — custom filename/folder templates.

Validates the render + validation contract used by both the downloaders
(BaseDownloader._download_aweme_assets, MusicDownloader, LiveDownloader) and
the desktop settings API (PATCH /api/v1/settings).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from utils.naming import (
    ALLOWED_VARIABLES,
    DEFAULT_FILE_TEMPLATE,
    DEFAULT_FOLDER_TEMPLATE,
    MAX_TEMPLATE_LENGTH,
    TemplateValidationError,
    build_aweme_context,
    build_live_context,
    build_music_context,
    render_template,
    validate_template,
)

# ---------------------------------------------------------------------------
# validate_template
# ---------------------------------------------------------------------------


def test_validate_template_accepts_defaults():
    validate_template(DEFAULT_FILE_TEMPLATE)
    validate_template(DEFAULT_FOLDER_TEMPLATE)


@pytest.mark.parametrize(
    "bad,needle",
    [
        ("", "empty"),
        ("   ", "empty"),
        ("a" * (MAX_TEMPLATE_LENGTH + 1) + "{id}", "<="),
        ("foo/bar_{id}", "path separators"),
        ("foo\\bar_{id}", "path separators"),
        ("{date}_{title}", "{id}"),
        ("{unknown}_{id}", "unknown"),
        ("static_prefix", "at least one variable"),
    ],
)
def test_validate_template_rejects(bad: str, needle: str):
    with pytest.raises(TemplateValidationError) as exc:
        validate_template(bad)
    assert needle in str(exc.value)


def test_validate_template_uses_field_name_in_error():
    with pytest.raises(TemplateValidationError) as exc:
        validate_template("{date}", field_name="filename_template")
    assert "filename_template" in str(exc.value)


# ---------------------------------------------------------------------------
# render_template
# ---------------------------------------------------------------------------


def test_render_template_replaces_all_known_vars():
    ctx = {v: v for v in ALLOWED_VARIABLES}
    # Use a minimal template to stay under sanitize_filename's 80-char cap.
    tpl = "{id}_{title}_{author}_{date}_{type}_{mode}"
    out = render_template(tpl, ctx)
    for v in ("id", "title", "author", "date", "type", "mode"):
        assert v in out


def test_render_template_unknown_keys_render_as_empty():
    out = render_template("{title}_{totally_unknown}_{id}", {"title": "hi", "id": "42"})
    assert out == "hi_42"


def test_render_template_missing_context_value_renders_empty():
    out = render_template("{date}_{title}_{id}", {"date": "", "title": "", "id": "42"})
    assert out == "42"


def test_render_template_falls_back_when_result_blank():
    out = render_template("{title}", {"title": ""}, fallback="2024-01-01_42")
    assert out == "2024-01-01_42"


def test_render_template_sanitizes_illegal_chars():
    out = render_template(
        "{date}_{title}_{id}",
        {"date": "2024-01-01", "title": "bad/name?*", "id": "42"},
    )
    # Slashes, stars, question marks collapse into underscores via
    # sanitize_filename, then consecutive underscores collapse.
    assert "/" not in out and "?" not in out and "*" not in out
    assert "42" in out
    assert "2024-01-01" in out


# ---------------------------------------------------------------------------
# context builders
# ---------------------------------------------------------------------------


def test_build_aweme_context_minimum_fields():
    ts = int(datetime(2024, 3, 15, 18, 30).timestamp())
    ctx = build_aweme_context(
        aweme_id="7412345678901234567",
        title="山里的秋天",
        author_name="某作者",
        author_sec_uid="MS4wLjABAAA",
        publish_date="2024-03-15",
        publish_ts=ts,
        media_type="video",
        mode="post",
    )
    assert ctx["id"] == "7412345678901234567"
    assert ctx["title"] == "山里的秋天"
    assert ctx["author"] == "某作者"
    assert ctx["author_id"] == "MS4wLjABAAA"
    assert ctx["date"] == "2024-03-15"
    assert ctx["year"] == "2024"
    assert ctx["month"] == "03"
    assert ctx["day"] == "15"
    assert ctx["type"] == "video"
    assert ctx["mode"] == "post"
    assert ctx["time"] == "1830"
    assert ctx["timestamp"] == str(ts)


def test_build_aweme_context_defaults_title_when_blank():
    ctx = build_aweme_context(
        aweme_id="42",
        title="",
        author_name="a",
        author_sec_uid=None,
        publish_date="2024-01-01",
        publish_ts=None,
        media_type="video",
    )
    assert ctx["title"] == "no_title"
    assert ctx["author_id"] == ""
    assert ctx["timestamp"] == ""
    assert ctx["time"] == ""


def test_build_music_context_prefixes_music_id():
    ctx = build_music_context(
        music_id="999",
        title="某 BGM",
        author_name="作曲人",
        publish_date="2024-01-02",
    )
    assert ctx["id"] == "music_999"
    assert ctx["type"] == "music"
    assert ctx["date"] == "2024-01-02"


def test_build_live_context_sets_time():
    started_at = datetime(2024, 5, 10, 21, 3, 45)
    ctx = build_live_context(
        room_id="7400000000000000000",
        title="直播中",
        author_name="主播",
        started_at=started_at,
    )
    assert ctx["id"] == "7400000000000000000"
    # `date` intentionally includes HHMM for live streams so the default
    # template preserves the legacy `YYYY-MM-DD_HHMM_{title}_{id}` layout.
    assert ctx["date"] == "2024-05-10_2103"
    assert ctx["year"] == "2024"
    assert ctx["month"] == "05"
    assert ctx["day"] == "10"
    assert ctx["time"] == "2103"
    assert ctx["type"] == "live"
    assert ctx["timestamp"] == str(int(started_at.timestamp()))


# ---------------------------------------------------------------------------
# end-to-end render via the default template
# ---------------------------------------------------------------------------


def test_default_template_matches_legacy_layout():
    """The default template must produce output identical to the pre-template
    f-string (minus sanitize_filename collapsing). This is the compatibility
    anchor: users who never touch the setting keep the exact same paths.
    """
    ctx = build_aweme_context(
        aweme_id="7412345678901234567",
        title="今天去爬山啦",
        author_name="ignored_here",
        author_sec_uid=None,
        publish_date="2026-04-10",
        publish_ts=None,
        media_type="video",
        mode="post",
    )
    assert render_template(DEFAULT_FILE_TEMPLATE, ctx) == (
        "2026-04-10_今天去爬山啦_7412345678901234567"
    )
