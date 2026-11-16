"""search 空结果的两种含义、user 的裸 id 校验、与 core 常量的漂移检查。"""

import pytest

from agent_mode import discovery_cmds, discovery_search
from tests.test_agent_discovery import (
    _calls,
    _fail,
    _login,
    _page,
    _run,
    client,  # noqa: F401 — pytest fixture
)

NIL_TYPES = discovery_search.BLOCKED_NIL_TYPES


def _raw_page(raw):
    return dict(_page([]), raw=raw)


def test_search_with_no_matches_is_ok_and_empty(client, capsys):  # noqa: F811
    _login()
    client.pages = [_page([])]
    code, result = _run(capsys, ["search", "kw"])
    assert (code, result["ok"], result["error"]) == (0, True, None)
    assert result["data"] == {"keyword": "kw", "items": [], "count": 0, "has_more": False}
    assert len(_calls(client, "search")) == 1


@pytest.mark.parametrize(
    "raw",
    [{"data": []}, {"status_code": None, "data": []}]
    + [
        {"status_code": 0, "data": [], "search_nil_info": {"search_nil_type": n}} for n in NIL_TYPES
    ],
)
def test_search_empty_without_clear_success_is_upstream_empty(client, capsys, raw):  # noqa: F811
    _login()
    client.pages = [_raw_page(raw)]
    result = _fail(capsys, ["search", "kw"], (5, "UPSTREAM_EMPTY"))
    assert result["error"]["retryable"] is True
    assert result["error"]["action"]["retry_after_s"] == discovery_search.SEARCH_RETRY_AFTER_S


def test_search_unexpected_error_without_items_is_internal(client, capsys):  # noqa: F811
    _login()
    client.pages = [ValueError("bad")]
    _fail(capsys, ["search", "kw"], (1, "INTERNAL"))


@pytest.mark.parametrize("target", ["douyinhao_123", "88886666", "MS4wLjA"])
def test_user_rejects_bare_ids_that_are_not_sec_uids(client, capsys, target):  # noqa: F811
    _login()
    result = _fail(capsys, ["user", target], (2, "USAGE"))
    assert result["error"]["retryable"] is False
    assert result["error"]["action"] == {
        "kind": "fix_usage",
        "hint": "pass the profile URL or a sec_uid (starts with MS4wLjAB)",
    }
    assert client.calls == []


def test_gallery_aweme_types_match_core():
    from core.downloader_base import BaseDownloader

    assert set(discovery_search.GALLERY_AWEME_TYPES) == set(BaseDownloader._GALLERY_AWEME_TYPES)
    assert discovery_cmds.SEC_UID_PREFIX == "MS4wLjAB"
