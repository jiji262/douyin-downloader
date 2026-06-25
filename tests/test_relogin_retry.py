"""_run_with_relogin: detect LoginRequiredError, relogin, retry once."""

import pytest

import cli.main as m
from auth import CookieManager
from config import ConfigLoader
from core import LoginRequiredError


def _mk():
    config = ConfigLoader(None)
    cm = CookieManager()
    cm.set_cookies({"sessionid": "old"})
    return config, cm


@pytest.mark.asyncio
async def test_retries_once_after_relogin(monkeypatch):
    config, cm = _mk()
    calls = {"n": 0}

    async def make_coro():
        calls["n"] += 1
        if calls["n"] == 1:
            raise LoginRequiredError(2483, "请先登录", "/search")
        return "done"

    async def fake_relogin(cookies_path=None):
        return {"sessionid": "fresh"}

    monkeypatch.setattr(m, "can_interactive_login", lambda *, serve=False: True)
    monkeypatch.setattr(m, "interactive_relogin", fake_relogin)

    result = await m._run_with_relogin(make_coro, config, cm)

    assert result == "done"
    assert calls["n"] == 2
    assert cm.get_cookies().get("sessionid") == "fresh"
    assert config.get_cookies().get("sessionid") == "fresh"


@pytest.mark.asyncio
async def test_non_interactive_does_not_relogin(monkeypatch):
    config, cm = _mk()
    called = {"relogin": False}

    async def make_coro():
        raise LoginRequiredError(2483, "请先登录", "/search")

    async def fake_relogin(cookies_path=None):
        called["relogin"] = True
        return {"sessionid": "fresh"}

    monkeypatch.setattr(m, "can_interactive_login", lambda *, serve=False: False)
    monkeypatch.setattr(m, "interactive_relogin", fake_relogin)

    with pytest.raises(LoginRequiredError):
        await m._run_with_relogin(make_coro, config, cm)
    assert called["relogin"] is False


@pytest.mark.asyncio
async def test_gives_up_when_relogin_fails(monkeypatch):
    config, cm = _mk()

    async def make_coro():
        raise LoginRequiredError(2483, "请先登录", "/search")

    async def fake_relogin(cookies_path=None):
        return None

    monkeypatch.setattr(m, "can_interactive_login", lambda *, serve=False: True)
    monkeypatch.setattr(m, "interactive_relogin", fake_relogin)

    with pytest.raises(LoginRequiredError):
        await m._run_with_relogin(make_coro, config, cm)
