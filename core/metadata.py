"""Shared helpers for extracting normalized fields from raw Douyin aweme payloads.

These helpers centralize the payload-shape dereferencing so that callers across
downloaders (``downloader_base``, ``music_downloader``, future strategies, …)
all agree on how to pull fields like ``author.sec_uid`` out of the various
aweme dict shapes returned by the upstream API.
"""

from __future__ import annotations

from typing import Any, List, Mapping, Optional

_VIDEO_COVER_KEYS = (
    "origin_cover",
    "cover_original_scale",
    "cover",
)


def extract_video_cover_urls(aweme: Optional[Mapping[str, Any]]) -> List[str]:
    """Return the best available static cover mirrors for a video aweme.

    Douyin exposes both preview-sized ``video.cover`` and higher-quality
    variants. Prefer ``origin_cover`` (the original static cover), then the
    occasionally returned ``cover_original_scale``, and retain ``cover`` as a
    compatibility fallback for older payloads.
    """

    if not isinstance(aweme, Mapping):
        return []
    video = aweme.get("video")
    if not isinstance(video, Mapping):
        return []

    for key in _VIDEO_COVER_KEYS:
        source = video.get(key)
        if not isinstance(source, Mapping):
            continue
        url_list = source.get("url_list") or source.get("urlList")
        if not isinstance(url_list, list):
            continue
        urls = [item for item in url_list if isinstance(item, str) and item]
        if urls:
            return urls
    return []


def extract_author_sec_uid(aweme: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Return ``aweme["author"]["sec_uid"]`` or ``None`` if unavailable.

    Defensive against every shape variation observed so far:
      * ``aweme`` itself being ``None`` or not a mapping
      * ``aweme["author"]`` being missing, ``None``, or not a mapping
      * ``sec_uid`` being missing, ``None``, or an empty / whitespace string
        (all collapse to ``None`` so downstream consumers can treat NULL and
        empty-string identically).
    """

    if not isinstance(aweme, Mapping):
        return None
    author = aweme.get("author")
    if not isinstance(author, Mapping):
        return None
    sec_uid = author.get("sec_uid")
    if not isinstance(sec_uid, str):
        return None
    sec_uid = sec_uid.strip()
    return sec_uid or None


def extract_author_nickname(aweme: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Return ``aweme["author"]["nickname"]`` or ``None`` if unavailable."""

    if not isinstance(aweme, Mapping):
        return None
    author = aweme.get("author")
    if not isinstance(author, Mapping):
        return None
    nickname = author.get("nickname")
    if not isinstance(nickname, str):
        return None
    nickname = nickname.strip()
    return nickname or None
