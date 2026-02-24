from auth import CookieManager


def test_cookie_manager_validation_requires_all_keys(tmp_path):
    cookie_file = tmp_path / ".cookies.json"
    manager = CookieManager(str(cookie_file))

    manager.set_cookies({"msToken": "token", "ttwid": "id"})
    assert manager.validate_cookies() is False

    manager.set_cookies(
        {
            "msToken": "token",
            "ttwid": "id",
            "odin_tt": "odin",
            "passport_csrf_token": "csrf",
        }
    )

    assert manager.validate_cookies() is True


def test_cookie_manager_validation_allows_missing_ms_token(tmp_path):
    cookie_file = tmp_path / ".cookies.json"
    manager = CookieManager(str(cookie_file))

    manager.set_cookies(
        {
            "ttwid": "id",
            "odin_tt": "odin",
            "passport_csrf_token": "csrf",
        }
    )

    assert manager.validate_cookies() is True


def test_cookie_manager_filters_illegal_cookie_keys(tmp_path):
    cookie_file = tmp_path / ".cookies.json"
    manager = CookieManager(str(cookie_file))

    manager.set_cookies(
        {
            "": "douyin.com",
            "ttwid": "id",
        }
    )

    cookies = manager.get_cookies()
    assert "" not in cookies
    assert cookies["ttwid"] == "id"
