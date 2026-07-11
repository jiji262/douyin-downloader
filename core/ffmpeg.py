"""Resolve the ffmpeg binary bundled with the packaged sidecar."""

from __future__ import annotations

import os
import shutil


def resolve_ffmpeg_path(*, fallback_to_path: bool = True) -> str:
    """Prefer imageio-ffmpeg's bundled binary, then optionally search PATH."""
    try:
        import imageio_ffmpeg

        bundled = str(imageio_ffmpeg.get_ffmpeg_exe() or "")
    except Exception:
        bundled = ""
    if bundled and os.path.isfile(bundled):
        return bundled
    if fallback_to_path:
        return str(shutil.which("ffmpeg") or "")
    return ""
