from concurrent.futures import ThreadPoolExecutor
from threading import Event
from urllib.error import URLError

import pytest
import yaml

from auth.ms_token_conf import BUNDLED_MS_TOKEN_CONF
from auth.ms_token_manager import MsTokenManager


def _reset_class_state():
    MsTokenManager._generation_retry_after = 0.0
    MsTokenManager._generated_tokens = {}
    MsTokenManager._cached_conf = None
    MsTokenManager._cached_at = 0
    MsTokenManager._remote_conf_retry_after = 0.0


@pytest.fixture(autouse=True)
def _reset_generation_cache():
    _reset_class_state()
    yield
    _reset_class_state()


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


# ---------------------------------------------------------------------------
# Bundled config fallback: raw.githubusercontent.com is unreachable for many
# users inside the 3s probe budget. A random fallback token makes Douyin answer
# 403 to /aweme/post/ after a few pages, so generation must still succeed then.
# ---------------------------------------------------------------------------

_MSSDK_TOKEN = "m" * 182 + "=="


class _Headers:
    def __init__(self, set_cookies):
        self._set_cookies = list(set_cookies)

    def get_all(self, key):
        return self._set_cookies if key == "Set-Cookie" else []


class _FakeResponse:
    def __init__(self, body: bytes = b"", set_cookies=()):
        self._body = body
        self.headers = _Headers(set_cookies)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _fake_urlopen(*, conf_error=None, conf_yaml="", mssdk_token=_MSSDK_TOKEN, calls):
    def urlopen(request, timeout=None):  # noqa: ARG001
        url = request if isinstance(request, str) else request.full_url
        calls.append(url)
        if url == MsTokenManager.F2_CONF_URL:
            if conf_error is not None:
                raise conf_error
            return _FakeResponse(body=conf_yaml.encode("utf-8"))
        if mssdk_token is None:
            return _FakeResponse()
        return _FakeResponse(set_cookies=[f"msToken={mssdk_token}; Path=/"])

    return urlopen


def test_bundled_conf_matches_generation_contract():
    assert MsTokenManager._REQUIRED_CONF_KEYS.issubset(BUNDLED_MS_TOKEN_CONF)
    assert BUNDLED_MS_TOKEN_CONF["url"].startswith("https://mssdk.bytedance.com/")
    assert len(BUNDLED_MS_TOKEN_CONF["strData"]) > 1000


def test_unreachable_github_falls_back_to_bundled_conf(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _fake_urlopen(conf_error=URLError("blocked"), calls=calls),
    )

    conf = MsTokenManager("agent")._load_f2_ms_token_conf()

    assert conf == BUNDLED_MS_TOKEN_CONF
    assert calls == [MsTokenManager.F2_CONF_URL]


def test_remote_conf_failure_is_not_retried_within_backoff(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _fake_urlopen(conf_error=TimeoutError("slow"), calls=calls),
    )

    MsTokenManager("agent-a")._load_f2_ms_token_conf()
    conf = MsTokenManager("agent-b")._load_f2_ms_token_conf()

    assert conf == BUNDLED_MS_TOKEN_CONF
    assert calls == [MsTokenManager.F2_CONF_URL]


def test_real_token_is_generated_when_github_is_unreachable(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _fake_urlopen(conf_error=URLError("blocked"), calls=calls),
    )

    token = MsTokenManager("agent").ensure_ms_token({})

    assert token == _MSSDK_TOKEN
    assert calls == [MsTokenManager.F2_CONF_URL, BUNDLED_MS_TOKEN_CONF["url"]]


def test_reachable_remote_conf_wins_and_is_shared_across_instances(monkeypatch):
    calls = []
    remote = dict(BUNDLED_MS_TOKEN_CONF, magic=1)
    conf_yaml = yaml.safe_dump({"f2": {"douyin": {"msToken": remote}}})
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(conf_yaml=conf_yaml, calls=calls))

    first = MsTokenManager("agent-a")._load_f2_ms_token_conf()
    second = MsTokenManager("agent-b")._load_f2_ms_token_conf()

    assert first["magic"] == 1
    assert second["magic"] == 1
    assert calls == [MsTokenManager.F2_CONF_URL]


def test_reachable_but_incomplete_remote_conf_falls_back_to_bundled(monkeypatch):
    calls = []
    conf_yaml = yaml.safe_dump({"f2": {"douyin": {"msToken": {"url": "https://x.invalid"}}}})
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(conf_yaml=conf_yaml, calls=calls))

    conf = MsTokenManager("agent")._load_f2_ms_token_conf()

    assert conf == BUNDLED_MS_TOKEN_CONF
    assert calls == [MsTokenManager.F2_CONF_URL]


def test_stale_remote_conf_beats_bundled_when_refresh_fails(monkeypatch):
    calls = []
    remote = dict(BUNDLED_MS_TOKEN_CONF, magic=1)
    conf_yaml = yaml.safe_dump({"f2": {"douyin": {"msToken": remote}}})
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(conf_yaml=conf_yaml, calls=calls))
    MsTokenManager("agent")._load_f2_ms_token_conf()
    MsTokenManager._cached_at = 0  # expire the 1h TTL
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _fake_urlopen(conf_error=URLError("blocked"), calls=calls),
    )

    conf = MsTokenManager("agent")._load_f2_ms_token_conf()

    assert conf["magic"] == 1
    assert calls == [MsTokenManager.F2_CONF_URL, MsTokenManager.F2_CONF_URL]


def test_mssdk_rejection_degrades_to_random_token_with_backoff(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _fake_urlopen(conf_error=URLError("blocked"), mssdk_token=None, calls=calls),
    )

    token = MsTokenManager("agent").ensure_ms_token({})

    assert len(token) == 184
    assert token != _MSSDK_TOKEN
    assert MsTokenManager._generation_retry_after > 0
