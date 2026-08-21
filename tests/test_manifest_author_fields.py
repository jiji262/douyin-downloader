"""Tests: `download_manifest.jsonl` carries the author's identity.

Before this change the manifest recorded only `author_name` (a nickname that
can collide and can be renamed), so a consumer holding just the manifest had
no way back to the creator's Douyin homepage. Every manifest writer now emits
two extra keys with a fixed schema — always present, empty string when the
upstream payload has no `sec_uid`:

* ``author_sec_uid`` — the stable identity, also the join key against the
  ``aweme.author_sec_uid`` column.
* ``author_url``    — the canonical homepage URL derived from it.

Layered narrow to broad:

1. ``build_author_home_url`` exercised directly on hostile inputs.
2. ``VideoDownloader`` end-to-end through ``_download_aweme_assets`` (the
   main path) with and without ``author.sec_uid``.
3. ``MusicDownloader`` and ``LiveReplayDownloader``, the two other writers,
   so the manifest schema stays uniform across media types.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from auth import CookieManager
from config import ConfigLoader
from control import QueueManager, RateLimiter, RetryHandler
from core.api_client import DouyinAPIClient
from core.live_replay_downloader import LiveReplayDownloader
from core.metadata import build_author_home_url
from core.music_downloader import MusicDownloader
from core.video_downloader import VideoDownloader
from storage import FileManager

SEC_UID = "MS4wLjABAAAAtest_sec_uid"


def _manifest_records(base_path: Path) -> List[Dict[str, Any]]:
    manifest = base_path / "download_manifest.jsonl"
    assert manifest.exists(), "expected download_manifest.jsonl to be written"
    return [
        json.loads(line)
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ---------------------------------------------------------------------------
# 1. Pure helper — build_author_home_url
# ---------------------------------------------------------------------------
def test_build_author_home_url_returns_canonical_url():
    assert build_author_home_url(SEC_UID) == f"https://www.douyin.com/user/{SEC_UID}"


def test_build_author_home_url_strips_whitespace():
    assert build_author_home_url(f"  {SEC_UID}  ") == f"https://www.douyin.com/user/{SEC_UID}"


def test_build_author_home_url_percent_encodes_unsafe_characters():
    # Mirrors the renderer's encodeURIComponent defence in
    # desktop/src/renderer/utils/buildAuthorHomeUrl.ts — a malformed sec_uid
    # must not be able to smuggle a path segment or query into the URL.
    assert build_author_home_url("a/b?c") == "https://www.douyin.com/user/a%2Fb%3Fc"


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(None, id="none"),
        pytest.param("", id="empty-string"),
        pytest.param("   ", id="whitespace-only"),
        pytest.param(123, id="not-a-string"),
    ],
)
def test_build_author_home_url_returns_none_for_unusable_input(value):
    assert build_author_home_url(value) is None


# ---------------------------------------------------------------------------
# 2. VideoDownloader — the main manifest writer
# ---------------------------------------------------------------------------
def _build_video_downloader(tmp_path) -> tuple[VideoDownloader, DouyinAPIClient]:
    config = ConfigLoader()
    config.update(
        path=str(tmp_path),
        music=False,
        cover=False,
        avatar=False,
        json=False,
        folderstyle=True,
        transcript={"enabled": False},
    )
    api_client = DouyinAPIClient({})
    downloader = VideoDownloader(
        config,
        api_client,
        FileManager(str(tmp_path)),
        CookieManager(str(tmp_path / ".cookies.json")),
        database=None,
        rate_limiter=RateLimiter(max_per_second=10),
        retry_handler=RetryHandler(max_retries=1),
        queue_manager=QueueManager(max_workers=1),
    )
    return downloader, api_client


def _stub_network(downloader: VideoDownloader, api_client, monkeypatch) -> None:
    async def _fake_get_session():
        return object()

    monkeypatch.setattr(api_client, "get_session", _fake_get_session)

    async def _fake_download_with_retry(self, _url, _save_path, _session, **_kwargs):
        return True

    downloader._download_with_retry = _fake_download_with_retry.__get__(
        downloader, VideoDownloader
    )


def _video_payload(aweme_id: str, author: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "aweme_id": aweme_id,
        "desc": "manifest author fields",
        "create_time": 1707303025,
        "author": author,
        "video": {"play_addr": {"url_list": ["https://example.com/video.mp4"]}},
    }


async def test_video_manifest_carries_sec_uid_and_home_url(tmp_path, monkeypatch):
    downloader, api_client = _build_video_downloader(tmp_path)
    _stub_network(downloader, api_client, monkeypatch)

    payload = _video_payload("7600224486650121526", {"uid": "u1", "nickname": "Alice", "sec_uid": SEC_UID})
    assert await downloader._download_aweme_assets(payload, author_name="Alice", mode="post")

    record = _manifest_records(tmp_path)[-1]
    assert record["author_sec_uid"] == SEC_UID
    assert record["author_url"] == f"https://www.douyin.com/user/{SEC_UID}"

    await api_client.close()


async def test_video_manifest_uses_empty_strings_when_sec_uid_missing(tmp_path, monkeypatch):
    downloader, api_client = _build_video_downloader(tmp_path)
    _stub_network(downloader, api_client, monkeypatch)

    payload = _video_payload("7600224486650121999", {"uid": "u2", "nickname": "Bob"})
    assert await downloader._download_aweme_assets(payload, author_name="Bob", mode="post")

    record = _manifest_records(tmp_path)[-1]
    # Fixed schema: the keys are always present so downstream consumers
    # (jq / pandas) never have to branch on a missing column.
    assert record["author_sec_uid"] == ""
    assert record["author_url"] == ""

    await api_client.close()


async def test_video_manifest_prefers_explicit_sec_uid_over_payload(tmp_path, monkeypatch):
    """A caller that already knows whose profile it is walking wins.

    `user_downloader` passes the sec_uid it resolved from the profile URL;
    that is more authoritative than a payload whose author block may be
    trimmed by the upstream API.
    """
    downloader, api_client = _build_video_downloader(tmp_path)
    _stub_network(downloader, api_client, monkeypatch)

    payload = _video_payload("7600224486650122777", {"uid": "u3", "nickname": "Carol"})
    assert await downloader._download_aweme_assets(
        payload, author_name="Carol", mode="post", author_sec_uid=SEC_UID
    )

    record = _manifest_records(tmp_path)[-1]
    assert record["author_sec_uid"] == SEC_UID
    assert record["author_url"] == f"https://www.douyin.com/user/{SEC_UID}"

    await api_client.close()


# ---------------------------------------------------------------------------
# 3. The other two writers keep the schema uniform
# ---------------------------------------------------------------------------
class _FakeMusicAPIClient:
    BASE_URL = "https://www.douyin.com"
    headers = {"User-Agent": "UnitTestAgent/1.0"}

    async def get_music_detail(self, _music_id: str):
        return {
            "title": "test-music",
            "author_name": "test-author",
            "author": {"sec_uid": SEC_UID},
            "play_url": {"url_list": ["https://example.com/music.mp3"]},
        }

    async def get_session(self):
        return object()


@pytest.mark.asyncio
async def test_music_manifest_carries_sec_uid_and_home_url(tmp_path, monkeypatch):
    config = ConfigLoader()
    config.update(path=str(tmp_path), cover=False, json=False)
    downloader = MusicDownloader(
        config=config,
        api_client=_FakeMusicAPIClient(),
        file_manager=FileManager(str(tmp_path)),
        cookie_manager=CookieManager(str(tmp_path / ".cookies.json")),
        database=None,
        rate_limiter=RateLimiter(max_per_second=10),
        retry_handler=RetryHandler(max_retries=1),
        queue_manager=QueueManager(max_workers=1),
    )

    async def _fake_download_with_retry(self, _url, _save_path, _session, **_kwargs):
        return True

    monkeypatch.setattr(
        downloader,
        "_download_with_retry",
        _fake_download_with_retry.__get__(downloader, MusicDownloader),
    )

    result = await downloader.download({"music_id": "7600224486650121999"})
    assert result.success == 1

    record = _manifest_records(tmp_path)[-1]
    assert record["author_sec_uid"] == SEC_UID
    assert record["author_url"] == f"https://www.douyin.com/user/{SEC_UID}"


async def test_live_replay_manifest_carries_sec_uid_and_home_url(tmp_path):
    config = ConfigLoader()
    config.update(path=str(tmp_path))
    api_client = DouyinAPIClient({})
    downloader = LiveReplayDownloader(
        config,
        api_client,
        FileManager(str(tmp_path)),
        CookieManager(str(tmp_path / ".cookies.json")),
        database=None,
        rate_limiter=RateLimiter(max_per_second=5),
        retry_handler=RetryHandler(max_retries=1),
        queue_manager=QueueManager(max_workers=1),
    )

    output = tmp_path / "replay.mp4"
    output.write_bytes(b"stub")
    episode = {
        "id": "ep-1",
        "title": "回放",
        "start_time": 1707303025,
        "owner": {"nickname": "主播甲", "id": "owner-1", "sec_uid": SEC_UID},
    }

    await downloader._record_outputs(
        episode,
        {"title": "回放"},
        "ep-1",
        "room-1",
        tmp_path,
        [output],
        "ok",
    )

    record = _manifest_records(tmp_path)[-1]
    assert record["author_sec_uid"] == SEC_UID
    assert record["author_url"] == f"https://www.douyin.com/user/{SEC_UID}"

    await api_client.close()


async def test_live_replay_manifest_empty_when_owner_has_no_sec_uid(tmp_path):
    config = ConfigLoader()
    config.update(path=str(tmp_path))
    api_client = DouyinAPIClient({})
    downloader = LiveReplayDownloader(
        config,
        api_client,
        FileManager(str(tmp_path)),
        CookieManager(str(tmp_path / ".cookies.json")),
        database=None,
        rate_limiter=RateLimiter(max_per_second=5),
        retry_handler=RetryHandler(max_retries=1),
        queue_manager=QueueManager(max_workers=1),
    )

    output = tmp_path / "replay.mp4"
    output.write_bytes(b"stub")
    episode = {
        "id": "ep-2",
        "title": "回放",
        "start_time": 1707303025,
        "owner": {"nickname": "主播乙"},
    }

    await downloader._record_outputs(
        episode, {"title": "回放"}, "ep-2", "room-2", tmp_path, [output], "ok"
    )

    record = _manifest_records(tmp_path)[-1]
    assert record["author_sec_uid"] == ""
    assert record["author_url"] == ""

    await api_client.close()
