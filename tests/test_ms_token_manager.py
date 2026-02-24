from auth.ms_token_manager import MsTokenManager


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
