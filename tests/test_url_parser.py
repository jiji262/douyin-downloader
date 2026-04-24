from core.url_parser import URLParser


def test_parse_video_url():
    url = "https://www.douyin.com/video/7320876060210373923"
    parsed = URLParser.parse(url)

    assert parsed is not None
    assert parsed['type'] == 'video'
    assert parsed['aweme_id'] == '7320876060210373923'


def test_parse_gallery_url_sets_aweme_id():
    url = "https://www.douyin.com/note/7320876060210373923"
    parsed = URLParser.parse(url)

    assert parsed is not None
    assert parsed['type'] == 'gallery'
    assert parsed['aweme_id'] == '7320876060210373923'
    assert parsed['note_id'] == '7320876060210373923'


def test_parse_collection_url_sets_mix_id():
    url = "https://www.douyin.com/collection/7320876060210373923"
    parsed = URLParser.parse(url)

    assert parsed is not None
    assert parsed["type"] == "collection"
    assert parsed["mix_id"] == "7320876060210373923"


def test_parse_music_url_sets_music_id():
    url = "https://www.douyin.com/music/7320876060210373923"
    parsed = URLParser.parse(url)

    assert parsed is not None
    assert parsed["type"] == "music"
    assert parsed["music_id"] == "7320876060210373923"


def test_parse_unsupported_url_returns_none():
    url = "https://www.douyin.com/hashtag/123456"
    assert URLParser.parse(url) is None


def test_parse_short_url_marks_as_short():
    # 短链在 parser 层统一标记为 'short'，交由 CLI 预先解析真实链接。
    for url in (
        "https://v.douyin.com/ab12cd/",
        "http://v.douyin.com/ab12cd",
        "v.douyin.com/ab12cd",
        "https://v.iesdouyin.com/xyz789/",
    ):
        parsed = URLParser.parse(url)
        assert parsed is not None, url
        assert parsed["type"] == "short", url


def test_parse_live_url():
    parsed = URLParser.parse("https://live.douyin.com/123456789")
    assert parsed is not None
    assert parsed["type"] == "live"
    assert parsed["room_id"] == "123456789"

    parsed = URLParser.parse("https://www.douyin.com/follow/live/987654321")
    assert parsed is not None
    assert parsed["type"] == "live"
    assert parsed["room_id"] == "987654321"
