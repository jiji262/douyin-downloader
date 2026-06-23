"""Shared helpers for extracting normalized fields from raw Douyin aweme payloads.

These helpers centralize the payload-shape dereferencing so that callers across
downloaders (``downloader_base``, ``music_downloader``, future strategies, …)
all agree on how to pull fields like ``author.sec_uid`` out of the various
aweme dict shapes returned by the upstream API.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional


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
