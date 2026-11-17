"""Regression: retry overrides must not leak into the shared config.

ConfigLoader.update() merges dict values in place. retry_failed_awemes used to
snapshot each overridden key with config.get(k), which kept a reference to the
same nested dict; the in-place merge mutated the snapshot too, so the finally
block restored the polluted value. New keys were restored as None instead of
being removed.
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any, Dict, List, Optional

import pytest

from config.config_loader import ConfigLoader


class _RecordingAPIClient:
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


class _StubCookieManager:
    def get_cookies(self):
        return {}


def _run_retry(monkeypatch, tmp_path, overrides: Dict[str, Any], config: ConfigLoader | None = None) -> ConfigLoader:
    from core import retry_executor as retry_mod
    from storage.file_manager import FileManager

    if config is None:
        config = ConfigLoader(None)
        config.update(path=str(tmp_path), number={"post": 0, "like": 0})

    _RecordingAPIClient.reset()
    monkeypatch.setattr(retry_mod, "DouyinAPIClient", _RecordingAPIClient)
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
                overrides=overrides,
            )
        )
    return config


def test_retry_restores_nested_config_and_new_keys(monkeypatch, tmp_path) -> None:
    overrides = {"mode": ["like"], "number": {"post": 7}, "brand_new_key": {"a": 1}}

    from config.config_loader import ConfigLoader

    expected = ConfigLoader(None)
    expected.update(path=str(tmp_path), number={"post": 0, "like": 0})

    config = _run_retry(monkeypatch, tmp_path, overrides)
    assert config.config == expected.config
    assert "brand_new_key" not in config.config


def test_retry_without_overrides_keeps_config(monkeypatch, tmp_path) -> None:
    config = ConfigLoader(None)
    config.update(path=str(tmp_path), number={"post": 0, "like": 0})
    before = copy.deepcopy(config.config)

    _run_retry(monkeypatch, tmp_path, None, config)
    assert config.config == before
