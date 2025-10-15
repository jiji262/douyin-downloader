from auth import CookieManager


def test_cookie_manager_validation_requires_all_keys(tmp_path):
    cookie_file = tmp_path / '.cookies.json'
    manager = CookieManager(str(cookie_file))

    manager.set_cookies({'msToken': 'token', 'ttwid': 'id'})
    assert manager.validate_cookies() is False

    manager.set_cookies({
        'msToken': 'token',
        'ttwid': 'id',
        'odin_tt': 'odin',
        'passport_csrf_token': 'csrf',
    })

    assert manager.validate_cookies() is True
