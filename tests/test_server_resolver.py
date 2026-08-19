"""Resolve-only REST API tests. External Douyin and CDN requests are fully mocked."""

from typing import Any, Dict

import pytest

try:
    from fastapi.testclient import TestClient  # type: ignore
except ImportError:  # pragma: no cover
    pytest.skip("fastapi not installed", allow_module_level=True)

from config import ConfigLoader
from server import resolver
from server.app import build_app


class _FakeResponse:
    def __init__(self, url: str, status: int = 206, content_type: str = "video/mp4"):
        self.url = url
        self.status = status
        self.headers = {"Content-Type": content_type}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _FakeSession:
    def __init__(self, final_url: str):
        self.final_url = final_url
        self.requests = []

    def get(self, url: str, **kwargs):
        self.requests.append((url, kwargs))
        return _FakeResponse(self.final_url)


class _FakeAPIClient:
    detail: Dict[str, Any] = {}
    final_work_url = "https://www.douyin.com/video/1234567890123456789"
    session = _FakeSession("https://v3-web.douyinvod.com/final.mp4")

    def __init__(self, cookies, proxy=None):
        self.cookies = cookies
        self.proxy = proxy

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def resolve_short_url(self, url: str):
        return self.final_work_url

    async def get_video_detail(self, aweme_id: str):
        return self.detail

    async def get_session(self):
        return self.session


class _FakeVideoDownloader:
    def _detect_media_type(self, aweme_data):
        return "video"

    def _build_video_url_candidates(self, aweme_data):
        return [("https://www.douyin.com/aweme/v1/play/?signed=1", {"User-Agent": "ua"})]

    async def _maybe_promote_original_candidate(self, aweme_data, candidates, session):
        return candidates


class _FakeGalleryDownloader:
    def _detect_media_type(self, aweme_data):
        return "gallery"

    def _download_headers(self):
        return {"Referer": "https://www.douyin.com/"}

    def _collect_image_url_candidates(self, aweme_data):
        return [["https://img/one-a", "https://img/one-b"], ["https://img/two"]]

    def _collect_image_live_urls(self, aweme_data):
        return ["https://video/live"]


def _build_deps(tmp_path):
    config = ConfigLoader(None)
    config.update(path=str(tmp_path), rate_limit=1000)
    return build_app(config).state.deps


@pytest.mark.asyncio
async def test_candidate_probe_returns_final_redirect_without_reading_body():
    session = _FakeSession("https://v3-web.douyinvod.com/final.mp4")

    result = await resolver._resolve_candidate_url(
        session,
        "https://www.douyin.com/aweme/v1/play/?signed=1",
        {"User-Agent": "signed-agent"},
        "",
    )

    assert result == "https://v3-web.douyinvod.com/final.mp4"
    _, kwargs = session.requests[0]
    assert kwargs["headers"]["Range"] == "bytes=0-0"
    assert kwargs["headers"]["User-Agent"] == "signed-agent"
    assert kwargs["allow_redirects"] is True


@pytest.mark.asyncio
async def test_resolve_video_returns_one_final_cdn_url_without_files(tmp_path, monkeypatch):
    _FakeAPIClient.detail = {
        "aweme_id": "1234567890123456789",
        "desc": "测试视频",
        "video": {},
    }
    _FakeAPIClient.session = _FakeSession("https://v3-web.douyinvod.com/final.mp4")
    monkeypatch.setattr(resolver, "DouyinAPIClient", _FakeAPIClient)
    monkeypatch.setattr(
        resolver.DownloaderFactory,
        "create",
        lambda *args, **kwargs: _FakeVideoDownloader(),
    )

    result = await resolver.resolve_media_urls("https://v.douyin.com/short/", _build_deps(tmp_path))

    assert result == {
        "aweme_id": "1234567890123456789",
        "title": "测试视频",
        "media_type": "video",
        "urls": ["https://v3-web.douyinvod.com/final.mp4"],
    }
    assert list(tmp_path.rglob("*")) == []


@pytest.mark.asyncio
async def test_resolve_gallery_returns_one_url_per_asset(tmp_path, monkeypatch):
    _FakeAPIClient.detail = {
        "aweme_id": "1234567890123456789",
        "desc": "测试图集",
        "image_post_info": {"images": [{}]},
    }
    monkeypatch.setattr(resolver, "DouyinAPIClient", _FakeAPIClient)
    monkeypatch.setattr(
        resolver.DownloaderFactory,
        "create",
        lambda *args, **kwargs: _FakeGalleryDownloader(),
    )

    async def fake_resolve(session, url, headers, proxy):
        return {
            "https://img/one-a": None,
            "https://img/one-b": "https://cdn/image-1.jpeg",
            "https://img/two": "https://cdn/image-2.jpeg",
            "https://video/live": "https://cdn/live.mp4",
        }[url]

    monkeypatch.setattr(resolver, "_resolve_candidate_url", fake_resolve)
    result = await resolver.resolve_media_urls(
        "https://www.douyin.com/note/1234567890123456789", _build_deps(tmp_path)
    )

    assert result["media_type"] == "gallery"
    assert result["urls"] == [
        "https://cdn/image-1.jpeg",
        "https://cdn/image-2.jpeg",
        "https://cdn/live.mp4",
    ]
    assert list(tmp_path.rglob("*")) == []


def test_resolve_endpoint_returns_media_urls(tmp_path, monkeypatch):
    config = ConfigLoader(None)
    config.update(path=str(tmp_path))
    app = build_app(config)

    async def fake_resolve(url, deps):
        assert url == "https://v.douyin.com/example/"
        return {
            "aweme_id": "123",
            "title": "快捷指令测试",
            "media_type": "video",
            "urls": ["https://cdn/video.mp4"],
        }

    monkeypatch.setattr("server.app.resolve_media_urls", fake_resolve)
    with TestClient(app) as client:
        response = client.post("/api/v1/resolve", json={"url": "https://v.douyin.com/example/"})

    assert response.status_code == 200
    assert response.json()["urls"] == ["https://cdn/video.mp4"]


def test_resolve_endpoint_maps_domain_error(tmp_path, monkeypatch):
    config = ConfigLoader(None)
    config.update(path=str(tmp_path))
    app = build_app(config)

    async def fail_resolve(url, deps):
        raise resolver.MediaResolveError(422, "Only a single video or gallery URL is supported")

    monkeypatch.setattr("server.app.resolve_media_urls", fail_resolve)
    with TestClient(app) as client:
        response = client.post("/api/v1/resolve", json={"url": "https://www.douyin.com/user/abc"})

    assert response.status_code == 422
    assert "single video or gallery" in response.json()["detail"]


def test_resolve_endpoint_rejects_empty_url(tmp_path):
    config = ConfigLoader(None)
    config.update(path=str(tmp_path))
    app = build_app(config)
    with TestClient(app) as client:
        response = client.post("/api/v1/resolve", json={"url": ""})
    assert response.status_code == 400
