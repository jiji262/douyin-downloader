from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from auth.ms_token_manager import MsTokenManager


@pytest.fixture(autouse=True)
def _reset_generation_cache():
    MsTokenManager._generation_retry_after = 0.0
    MsTokenManager._generated_tokens = {}
    yield
    MsTokenManager._generation_retry_after = 0.0
    MsTokenManager._generated_tokens = {}


def test_gen_false_ms_token_format():
    token = MsTokenManager.gen_false_ms_token()
    assert isinstance(token, str)
    assert token.endswith("==")
    assert len(token) == 184


def test_extract_ms_token_from_headers():
    class _Headers:
        def get_all(self, key):
            if key != "Set-Cookie":
                return []
            return [
                "foo=bar; Path=/",
                "msToken=abc123; expires=Wed, 25 Feb 2026 00:00:00 GMT; Path=/",
            ]

    token = MsTokenManager._extract_ms_token_from_headers(_Headers())
    assert token == "abc123"


def test_default_remote_probe_budget_is_bounded():
    manager = MsTokenManager("test-agent")
    assert manager.timeout_seconds == 3.0


def test_failed_generation_uses_backoff_for_later_clients(monkeypatch):
    calls = {"count": 0}

    def fail_generation(self):  # noqa: ARG001
        calls["count"] += 1
        return None

    monkeypatch.setattr(MsTokenManager, "gen_real_ms_token", fail_generation)

    first = MsTokenManager("agent-a").ensure_ms_token({})
    second = MsTokenManager("agent-b").ensure_ms_token({})

    assert calls["count"] == 1
    assert len(first) == 184
    assert len(second) == 184


def test_successful_generation_is_reused_within_cookie_scope(monkeypatch):
    calls = {"count": 0}

    def generate(self):  # noqa: ARG001
        calls["count"] += 1
        return str(calls["count"]) * 164

    monkeypatch.setattr(MsTokenManager, "gen_real_ms_token", generate)

    first = MsTokenManager("same-agent").ensure_ms_token({"sessionid": "account-a"})
    second = MsTokenManager("same-agent").ensure_ms_token({"sessionid": "account-a"})
    other_account = MsTokenManager("same-agent").ensure_ms_token({"sessionid": "account-b"})

    assert first == second
    assert other_account != first
    assert calls["count"] == 2


def test_concurrent_clients_singleflight_failed_generation(monkeypatch):
    started = Event()
    release = Event()
    calls = {"count": 0}

    def fail_generation(self):  # noqa: ARG001
        calls["count"] += 1
        started.set()
        assert release.wait(timeout=1)
        return None

    monkeypatch.setattr(MsTokenManager, "gen_real_ms_token", fail_generation)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(MsTokenManager("agent-a").ensure_ms_token, {})
        assert started.wait(timeout=1)
        second = pool.submit(MsTokenManager("agent-b").ensure_ms_token, {})
        release.set()
        tokens = (first.result(timeout=1), second.result(timeout=1))

    assert calls["count"] == 1
    assert all(len(token) == 184 for token in tokens)
