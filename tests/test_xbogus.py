from utils.xbogus import generate_x_bogus


def test_generate_x_bogus_appends_parameter():
    base_url = "https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=123"
    signed_url, token, ua = generate_x_bogus(base_url)

    assert signed_url.startswith(base_url)
    assert "X-Bogus=" in signed_url
    assert isinstance(token, str) and len(token) > 10
    assert isinstance(ua, str) and "Mozilla" in ua
