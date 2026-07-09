"""Regression: real download paths must honour ``config['proxy']``.

The Settings page exposes 代理, and the 网络自检 probes douyin THROUGH that
proxy — but ``server.app._execute_download`` and
``core.retry_executor.retry_failed_awemes`` constructed
``DouyinAPIClient(cookies)`` without it, so desktop downloads always went
direct. In a proxy-required network the panel said "抖音可达 ✓" while every
real download failed, breaking the "可达 ⟹ 可下载" contract.

These tests pin proxy passthrough at both construction sites. The CLI's
``download_url`` (``cli/main.py``) already passed the proxy and is the
reference behaviour.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from config.config_loader import ConfigLoader


class _RecordingAPIClient:
    """Stands in for DouyinAPIClient; records the ``proxy`` kwarg."""

    seen_proxies: List[Optional[str]] = []

    def __init__(self, cookies, proxy: Optional[str] = None, **_kw):
        _RecordingAPIClient.seen_proxies.append(proxy)

    @classmethod
    def reset(cls):
        cls.seen_proxies = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return None

    async def resolve_short_url(self, _url):
        return None


class _FakeDownloader:
    async def download(self, _parsed):
        from core.downloader_base import DownloadResult

        return DownloadResult()


def _run_execute_download(monkeypatch, tmp_path, config_updates: Dict[str, Any]):
    from server import app as server_app
    from server.app import _execute_download, _ServerDeps

    deps = _ServerDeps(ConfigLoader(None))
    deps.config.update(path=str(tmp_path), **config_updates)

    _RecordingAPIClient.reset()
    monkeypatch.setattr(server_app, "DouyinAPIClient", _RecordingAPIClient)
    monkeypatch.setattr(server_app, "is_short_url", lambda _u: False)
    monkeypatch.setattr(
        server_app.URLParser,
        "parse",
        staticmethod(lambda _u: {"type": "video", "aweme_id": "1"}),
    )
    monkeypatch.setattr(
        server_app.DownloaderFactory,
        "create",
        staticmethod(lambda *_a, **_kw: _FakeDownloader()),
    )

    asyncio.run(
        _execute_download("https://www.douyin.com/video/7000000000000000001", deps)
    )
    return _RecordingAPIClient.seen_proxies


def test_execute_download_passes_configured_proxy(monkeypatch, tmp_path):
    seen = _run_execute_download(
        monkeypatch, tmp_path, {"proxy": "http://127.0.0.1:7890"}
    )
    assert seen == ["http://127.0.0.1:7890"]


def test_execute_download_without_proxy_stays_direct(monkeypatch, tmp_path):
    seen = _run_execute_download(monkeypatch, tmp_path, {})
    assert len(seen) == 1
    assert not seen[0]  # None or "" — DouyinAPIClient normalises both


def test_retry_executor_passes_configured_proxy(monkeypatch, tmp_path):
    from core import retry_executor as retry_mod
    from storage.file_manager import FileManager

    class _StubCookieManager:
        def get_cookies(self):
            return {}

    config = ConfigLoader(None)
    config.update(path=str(tmp_path), proxy="http://127.0.0.1:7890")

    _RecordingAPIClient.reset()
    monkeypatch.setattr(retry_mod, "DouyinAPIClient", _RecordingAPIClient)
    # Returning None from the factory aborts right after the client is
    # constructed — the proxy capture is all this test needs.
    monkeypatch.setattr(
        retry_mod.DownloaderFactory,
        "create",
        staticmethod(lambda *_a, **_kw: None),
    )

    with pytest.raises(RuntimeError, match="No downloader available for retry"):
        asyncio.run(
            retry_mod.retry_failed_awemes(
                "https://www.douyin.com/video/7000000000000000001",
                aweme_ids=["7000000000000000001"],
                config=config,
                file_manager=FileManager(str(tmp_path)),
                cookie_manager=_StubCookieManager(),
            )
        )

    assert _RecordingAPIClient.seen_proxies == ["http://127.0.0.1:7890"]
