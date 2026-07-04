import pytest

from auth import CookieManager
from config import ConfigLoader
from control import QueueManager, RateLimiter, RetryHandler
from core.mix_downloader import MixDownloader, derive_mix_collection_dir
from storage import FileManager


def test_derive_mix_collection_dir():
    # mix_name preferred; title is the secondary source; both get stripped.
    assert derive_mix_collection_dir({"mix_name": "我的合集"}, "123") == "我的合集"
    assert derive_mix_collection_dir({"title": "标题合集"}, "123") == "标题合集"
    assert derive_mix_collection_dir({"mix_name": "  spaced  "}, "123") == "spaced"
    # Empty / missing / non-dict → fall back to the mix_id (as str).
    assert derive_mix_collection_dir({"mix_name": ""}, 456) == "456"
    assert derive_mix_collection_dir({}, "123") == "123"
    assert derive_mix_collection_dir(None, "123") == "123"


class _FakeAPIClient:
    async def get_mix_aweme(self, _mix_id: str, cursor: int = 0, count: int = 20):
        if cursor > 0:
            return {"items": [], "has_more": False, "max_cursor": cursor, "status_code": 0}
        return {
            "items": [
                {
                    "aweme_id": "7600224486650121888",
                    "desc": "mix-item",
                    "author": {"nickname": "mix-author"},
                    "video": {"play_addr": {"url_list": ["https://example.com/video.mp4"]}},
                }
            ],
            "has_more": False,
            "max_cursor": 0,
            "status_code": 0,
        }

    async def get_mix_detail(self, _mix_id: str):
        return {"author": {"nickname": "mix-author"}}


@pytest.mark.asyncio
async def test_mix_downloader_downloads_mix_items(tmp_path, monkeypatch):
    config = ConfigLoader()
    config.update(path=str(tmp_path), number={"mix": 0})
    file_manager = FileManager(str(tmp_path))
    downloader = MixDownloader(
        config=config,
        api_client=_FakeAPIClient(),
        file_manager=file_manager,
        cookie_manager=CookieManager(str(tmp_path / ".cookies.json")),
        database=None,
        rate_limiter=RateLimiter(max_per_second=10),
        retry_handler=RetryHandler(max_retries=1),
        queue_manager=QueueManager(max_workers=1),
    )

    async def _always_true(*_args, **_kwargs):
        return True

    monkeypatch.setattr(downloader, "_should_download", _always_true)
    monkeypatch.setattr(downloader, "_download_aweme_assets", _always_true)

    result = await downloader.download({"mix_id": "123"})

    assert result.total == 1
    assert result.success == 1
    assert result.failed == 0


class _NamedMixAPIClient(_FakeAPIClient):
    """Mix detail carries a real ``mix_name`` so the collection folder can be
    derived from it."""

    def __init__(self, detail):
        self._detail = detail

    async def get_mix_detail(self, _mix_id: str):
        return self._detail


def _make_mix_downloader(tmp_path, api_client):
    config = ConfigLoader()
    config.update(path=str(tmp_path), number={"mix": 0})
    return MixDownloader(
        config=config,
        api_client=api_client,
        file_manager=FileManager(str(tmp_path)),
        cookie_manager=CookieManager(str(tmp_path / ".cookies.json")),
        database=None,
        rate_limiter=RateLimiter(max_per_second=10),
        retry_handler=RetryHandler(max_retries=1),
        queue_manager=QueueManager(max_workers=1),
    )


@pytest.mark.asyncio
async def test_mix_downloader_passes_mix_name_as_collection_dir(tmp_path, monkeypatch):
    """Each 合集 lands in its own folder: MixDownloader must derive the mix
    name and thread it to ``_download_aweme_assets`` as ``collection_dir``."""
    api = _NamedMixAPIClient({"author": {"nickname": "mix-author"}, "mix_name": "我的合集"})
    downloader = _make_mix_downloader(tmp_path, api)

    captured = {}

    async def _capture(_item, _author, *, mode=None, collection_dir=None, **_kw):
        captured["mode"] = mode
        captured["collection_dir"] = collection_dir
        return True

    async def _always_true(*_a, **_k):
        return True

    monkeypatch.setattr(downloader, "_should_download", _always_true)
    monkeypatch.setattr(downloader, "_download_aweme_assets", _capture)

    await downloader.download({"mix_id": "123"})

    assert captured["mode"] == "mix"
    assert captured["collection_dir"] == "我的合集"


@pytest.mark.asyncio
async def test_mix_downloader_collection_dir_falls_back_to_mix_id(tmp_path, monkeypatch):
    """When the mix detail has no name/title, the collection folder falls back
    to the mix_id so downloads never dump into a bare ``mix`` dir."""
    api = _NamedMixAPIClient({"author": {"nickname": "mix-author"}})  # no name/title
    downloader = _make_mix_downloader(tmp_path, api)

    captured = {}

    async def _capture(_item, _author, *, mode=None, collection_dir=None, **_kw):
        captured["collection_dir"] = collection_dir
        return True

    async def _always_true(*_a, **_k):
        return True

    monkeypatch.setattr(downloader, "_should_download", _always_true)
    monkeypatch.setattr(downloader, "_download_aweme_assets", _capture)

    await downloader.download({"mix_id": "123"})

    assert captured["collection_dir"] == "123"


@pytest.mark.asyncio
async def test_mix_downloader_does_not_apply_redundant_limit_count(tmp_path, monkeypatch):
    config = ConfigLoader()
    config.update(path=str(tmp_path), number={"mix": 0})
    file_manager = FileManager(str(tmp_path))
    downloader = MixDownloader(
        config=config,
        api_client=_FakeAPIClient(),
        file_manager=file_manager,
        cookie_manager=CookieManager(str(tmp_path / ".cookies.json")),
        database=None,
        rate_limiter=RateLimiter(max_per_second=10),
        retry_handler=RetryHandler(max_retries=1),
        queue_manager=QueueManager(max_workers=1),
    )

    async def _always_true(*_args, **_kwargs):
        return True

    call_count = {"limit": 0}

    def _track_limit(items, _mode):
        call_count["limit"] += 1
        return items

    monkeypatch.setattr(downloader, "_should_download", _always_true)
    monkeypatch.setattr(downloader, "_download_aweme_assets", _always_true)
    monkeypatch.setattr(downloader, "_limit_count", _track_limit)

    result = await downloader.download({"mix_id": "123"})

    assert result.total == 1
    assert call_count["limit"] == 0
