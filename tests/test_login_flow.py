"""Interactive relogin orchestration (CLI-only)."""

import json
from pathlib import Path

import pytest

import cli.login_flow as lf


def test_can_interactive_login_requires_tty(monkeypatch):
    monkeypatch.setattr(lf.sys.stdin, "isatty", lambda: True)
    assert lf.can_interactive_login(serve=False) is True
    assert lf.can_interactive_login(serve=True) is False
    monkeypatch.setattr(lf.sys.stdin, "isatty", lambda: False)
    assert lf.can_interactive_login(serve=False) is False


def test_can_interactive_login_handles_isatty_error(monkeypatch):
    def _boom():
        raise ValueError("I/O operation on closed file")

    monkeypatch.setattr(lf.sys.stdin, "isatty", _boom)
    assert lf.can_interactive_login(serve=False) is False


@pytest.mark.asyncio
async def test_interactive_relogin_success(monkeypatch, tmp_path):
    cookies_path = tmp_path / "cookies.json"

    async def fake_fetch(*, output, **kwargs):
        Path(output).write_text(
            json.dumps({"sessionid": "fresh", "ttwid": "t"}), encoding="utf-8"
        )
        return 0

    monkeypatch.setattr(lf, "fetch_cookies", fake_fetch)

    result = await lf.interactive_relogin(cookies_path=cookies_path)
    assert result is not None
    assert result["sessionid"] == "fresh"  # spec contract: dict with a sessionid
    assert result == {"sessionid": "fresh", "ttwid": "t"}  # full passthrough of sanitized cookies


@pytest.mark.asyncio
async def test_interactive_relogin_failure_returns_none(monkeypatch, tmp_path):
    cookies_path = tmp_path / "cookies.json"

    async def fake_fetch(*, output, **kwargs):
        return 1  # playwright missing / user aborted

    monkeypatch.setattr(lf, "fetch_cookies", fake_fetch)
    assert await lf.interactive_relogin(cookies_path=cookies_path) is None


@pytest.mark.asyncio
async def test_interactive_relogin_no_session_cookie_returns_none(monkeypatch, tmp_path):
    cookies_path = tmp_path / "cookies.json"

    async def fake_fetch(*, output, **kwargs):
        Path(output).write_text(json.dumps({"ttwid": "t"}), encoding="utf-8")
        return 0

    monkeypatch.setattr(lf, "fetch_cookies", fake_fetch)
    assert await lf.interactive_relogin(cookies_path=cookies_path) is None
