"""Tests for ``core.silent_audio``.

The bytes are baked in at source-edit time via the recipe in
``core/silent_audio.py``'s docstring; these tests just guard against
accidental corruption (truncation, base64 typos, encoding drift).
"""
from __future__ import annotations

from core.silent_audio import SILENT_1S_MP3_BYTES


def test_silent_mp3_is_nonempty() -> None:
    """The embedded payload must decode to a non-empty byte string."""
    assert isinstance(SILENT_1S_MP3_BYTES, bytes)
    assert len(SILENT_1S_MP3_BYTES) > 0


def test_silent_mp3_starts_with_valid_magic() -> None:
    """The first bytes must be a legal MP3 frame sync (``\\xff\\xfb``…)
    or an ID3v2 tag (``ID3``). Either is what ffmpeg's libmp3lame
    produces; a corrupted base64 literal would land on something else
    and should fail loudly here, not at probe-time on the user's box.
    """
    assert SILENT_1S_MP3_BYTES.startswith(b"ID3") or (
        SILENT_1S_MP3_BYTES[0] == 0xFF and (SILENT_1S_MP3_BYTES[1] & 0xE0) == 0xE0
    ), f"unexpected mp3 magic: {SILENT_1S_MP3_BYTES[:4]!r}"


def test_silent_mp3_size_within_expected_band() -> None:
    """A 1-second 32 kbps mono MP3 is ~4 KB. Guard against accidental
    truncation that would break the connectivity probe payload."""
    assert 1024 <= len(SILENT_1S_MP3_BYTES) <= 16 * 1024, (
        f"unexpected size: {len(SILENT_1S_MP3_BYTES)} bytes "
        "(expected 1-16 KB for 1s mono mp3)"
    )
