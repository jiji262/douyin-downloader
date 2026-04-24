"""Notifier 单元测试。"""

from typing import Any, Dict, List

import pytest

from utils.notifier import (
    BarkProvider,
    Notifier,
    TelegramProvider,
    WebhookProvider,
    build_notifier,
)


class _FakeResponse:
    def __init__(self, status: int = 200):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    def __init__(self, status: int = 200):
        self.status = status
        self.calls: List[Dict[str, Any]] = []

    def get(self, url, params=None):
        self.calls.append({"method": "GET", "url": url, "params": params})
        return _FakeResponse(self.status)

    def post(self, url, json=None, headers=None):
        self.calls.append(
            {"method": "POST", "url": url, "json": json, "headers": headers}
        )
        return _FakeResponse(self.status)


@pytest.mark.asyncio
async def test_bark_provider_sends_request():
    provider = BarkProvider({"url": "https://api.day.app/KEY", "sound": "bell"})
    session = _FakeSession()
    ok = await provider.send(session, title="t", body="b", level="success")
    assert ok is True
    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["method"] == "GET"
    assert call["url"].startswith("https://api.day.app/KEY/")
    assert call["params"] == {"sound": "bell"}


@pytest.mark.asyncio
async def test_bark_provider_skips_without_url():
    provider = BarkProvider({})
    session = _FakeSession()
    ok = await provider.send(session, title="t", body="b", level="info")
    assert ok is False
    assert session.calls == []


@pytest.mark.asyncio
async def test_telegram_provider_uses_bot_api():
    provider = TelegramProvider({"bot_token": "abc", "chat_id": "42"})
    session = _FakeSession()
    ok = await provider.send(session, title="t", body="b", level="info")
    assert ok is True
    call = session.calls[0]
    assert call["url"] == "https://api.telegram.org/botabc/sendMessage"
    assert call["json"]["chat_id"] == "42"
    assert "t" in call["json"]["text"]
    assert "b" in call["json"]["text"]


@pytest.mark.asyncio
async def test_webhook_provider_posts_json():
    provider = WebhookProvider(
        {
            "url": "https://hook.example/endpoint",
            "headers": {"Authorization": "Bearer xyz"},
            "extra_body": {"source": "dy"},
        }
    )
    session = _FakeSession()
    ok = await provider.send(session, title="t", body="b", level="success")
    assert ok is True
    call = session.calls[0]
    assert call["url"] == "https://hook.example/endpoint"
    assert call["headers"] == {"Authorization": "Bearer xyz"}
    assert call["json"]["title"] == "t"
    assert call["json"]["body"] == "b"
    assert call["json"]["level"] == "success"
    assert call["json"]["source"] == "dy"


@pytest.mark.asyncio
async def test_webhook_provider_reports_failure_on_4xx():
    provider = WebhookProvider({"url": "https://hook.example"})
    session = _FakeSession(status=500)
    ok = await provider.send(session, title="t", body="b", level="info")
    assert ok is False


def test_build_notifier_disabled_returns_empty():
    notifier = build_notifier({"notifications": {"enabled": False, "providers": []}})
    assert notifier.enabled is False


def test_build_notifier_rejects_scalar_config():
    """用户误写 `notifications: on` 等 scalar 不应抛 AttributeError。"""
    notifier = build_notifier({"notifications": "on"})
    assert notifier.enabled is False
    notifier = build_notifier({"notifications": True})
    assert notifier.enabled is False
    notifier = build_notifier({"notifications": 42})
    assert notifier.enabled is False


def test_build_notifier_ignores_unknown_provider():
    notifier = build_notifier(
        {
            "notifications": {
                "enabled": True,
                "providers": [
                    {"type": "unknown"},
                    {"type": "bark", "url": "https://api.day.app/KEY"},
                ],
            }
        }
    )
    assert notifier.enabled is True
    assert len(notifier.providers) == 1
    assert isinstance(notifier.providers[0], BarkProvider)


@pytest.mark.asyncio
async def test_notifier_respects_on_success_flag():
    # on_success=False 时 success 级别不应分发
    notifier = Notifier(
        providers=[BarkProvider({"url": "https://api.day.app/KEY"})],
        on_success=False,
        on_failure=True,
    )
    result = await notifier.send(title="t", body="b", level="success")
    assert result == {}


@pytest.mark.asyncio
async def test_notifier_empty_providers_returns_empty():
    notifier = Notifier(providers=[])
    result = await notifier.send(title="t", body="b", level="info")
    assert result == {}
    assert notifier.enabled is False
