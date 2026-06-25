"""Login-required detection in the shared API client."""

import pytest

from core.api_client import LoginRequiredError, _is_login_required


@pytest.mark.parametrize(
    "data,expected",
    [
        ({"status_code": 2483, "status_msg": "请先登录，再继续搜索吧"}, True),
        ({"status_code": 0, "status_msg": "请先登录后再试"}, True),  # msg match
        ({"status_code": 2483}, True),
        ({"status_code": 0, "status_msg": "ok"}, False),
        ({"status_code": 10000, "status_msg": "rate limited"}, False),
        ({}, False),
        ({"data": [{"aweme_info": {}}], "status_code": 0}, False),
        ("not-a-dict", False),
    ],
)
def test_is_login_required(data, expected):
    assert _is_login_required(data) is expected


def test_login_required_error_fields():
    err = LoginRequiredError(2483, "请先登录", "/aweme/v1/web/general/search/single/")
    assert err.status_code == 2483
    assert err.status_msg == "请先登录"
    assert err.path == "/aweme/v1/web/general/search/single/"
    assert "2483" in str(err)


def test_login_required_error_exported_from_core():
    from core import LoginRequiredError as Exported

    assert Exported is LoginRequiredError
