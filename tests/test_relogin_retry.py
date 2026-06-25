"""_run_with_relogin: detect LoginRequiredError, relogin, retry once."""

import pytest

import cli.main as m
from auth import CookieManager
from core import LoginRequiredError


def _mk_cm():
    cm = CookieManager()
    # Seed a stale key that the fresh login does NOT return, to prove the
    # refresh is a clean replace (not a merge that leaves stale keys behind).
    cm.set_cookies({"sessionid": "old", "stale_csrf": "X"})
    return cm


@pytest.mark.asyncio
async def test_retries_once_after_relogin(monkeypatch):
    cm = _mk_cm()
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

    result = await m._run_with_relogin(make_coro, cm)

    assert result == "done"
    assert calls["n"] == 2
    fresh = cm.get_cookies()
    assert fresh.get("sessionid") == "fresh"
    assert "stale_csrf" not in fresh  # clean replace, not a merge


@pytest.mark.asyncio
async def test_non_interactive_does_not_relogin(monkeypatch):
    cm = _mk_cm()
    called = {"relogin": False}

    async def make_coro():
        raise LoginRequiredError(2483, "请先登录", "/search")

    async def fake_relogin(cookies_path=None):
        called["relogin"] = True
        return {"sessionid": "fresh"}

    monkeypatch.setattr(m, "can_interactive_login", lambda *, serve=False: False)
    monkeypatch.setattr(m, "interactive_relogin", fake_relogin)

    with pytest.raises(LoginRequiredError):
        await m._run_with_relogin(make_coro, cm)
    assert called["relogin"] is False


@pytest.mark.asyncio
async def test_gives_up_when_relogin_fails(monkeypatch):
    cm = _mk_cm()

    async def make_coro():
        raise LoginRequiredError(2483, "请先登录", "/search")

    async def fake_relogin(cookies_path=None):
        return None

    monkeypatch.setattr(m, "can_interactive_login", lambda *, serve=False: True)
    monkeypatch.setattr(m, "interactive_relogin", fake_relogin)

    with pytest.raises(LoginRequiredError):
        await m._run_with_relogin(make_coro, cm)
