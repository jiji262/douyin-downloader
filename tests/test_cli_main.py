import importlib
from types import SimpleNamespace

import pytest

main_module = importlib.import_module("cli.main")


class _FakeCookieManager:
    def get_cookies(self):
        return {"msToken": "token-1"}


class _FakeAPIClient:
    def __init__(self, _cookies, proxy=None):
        self.proxy = proxy
        self.resolved_urls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def resolve_short_url(self, short_url: str):
        self.resolved_urls.append(short_url)
        return "https://www.douyin.com/video/7604129988555574538"


class _FakeDownloader:
    async def download(self, parsed):
        return SimpleNamespace(total=1, success=1, failed=0, skipped=0, parsed=parsed)


@pytest.mark.asyncio
async def test_download_url_resolves_short_link_before_parsing(monkeypatch, tmp_path):
    config = main_module.ConfigLoader()
    config.update(path=str(tmp_path))

    parsed_inputs = []

    def _fake_parse(url: str):
        parsed_inputs.append(url)
        return {"type": "video", "aweme_id": "7604129988555574538"}

    fake_downloader = _FakeDownloader()

    monkeypatch.setattr(main_module, "DouyinAPIClient", _FakeAPIClient)
    monkeypatch.setattr(main_module.URLParser, "parse", _fake_parse)
    monkeypatch.setattr(
        main_module.DownloaderFactory,
        "create",
        lambda *_args, **_kwargs: fake_downloader,
    )

    result = await main_module.download_url(
        "https://v.douyin.com/short-link/",
        config,
        _FakeCookieManager(),
        database=None,
        progress_reporter=None,
    )

    assert result is not None
    assert result.success == 1
    assert parsed_inputs == ["https://www.douyin.com/video/7604129988555574538"]


@pytest.mark.asyncio
async def test_download_url_passes_proxy_to_api_client(monkeypatch, tmp_path):
    config = main_module.ConfigLoader()
    config.update(path=str(tmp_path), proxy="http://127.0.0.1:8899")

    captured = {}

    class _ProxyAPIClient(_FakeAPIClient):
        def __init__(self, cookies, proxy=None):
            captured["cookies"] = cookies
            captured["proxy"] = proxy
            super().__init__(cookies, proxy=proxy)

    monkeypatch.setattr(main_module, "DouyinAPIClient", _ProxyAPIClient)
    monkeypatch.setattr(
        main_module.URLParser,
        "parse",
        lambda _url: {"type": "video", "aweme_id": "7604129988555574538"},
    )
    monkeypatch.setattr(
        main_module.DownloaderFactory,
        "create",
        lambda *_args, **_kwargs: _FakeDownloader(),
    )

    result = await main_module.download_url(
        "https://www.douyin.com/video/7604129988555574538",
        config,
        _FakeCookieManager(),
        database=None,
        progress_reporter=None,
    )

    assert result is not None
    assert result.success == 1
    assert captured["proxy"] == "http://127.0.0.1:8899"
