from core.url_parser import URLParser


def test_parse_video_url():
    url = "https://www.douyin.com/video/7320876060210373923"
    parsed = URLParser.parse(url)

    assert parsed is not None
    assert parsed["type"] == "video"
    assert parsed["aweme_id"] == "7320876060210373923"


def test_parse_gallery_url_sets_aweme_id():
    url = "https://www.douyin.com/note/7320876060210373923"
    parsed = URLParser.parse(url)

    assert parsed is not None
    assert parsed["type"] == "gallery"
    assert parsed["aweme_id"] == "7320876060210373923"
    assert parsed["note_id"] == "7320876060210373923"


def test_parse_gallery_path_url_sets_aweme_id():
    url = "https://www.douyin.com/gallery/7320876060210373923"
    parsed = URLParser.parse(url)

    assert parsed is not None
    assert parsed["type"] == "gallery"
    assert parsed["aweme_id"] == "7320876060210373923"
    assert parsed["note_id"] == "7320876060210373923"


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


def test_parse_live_replay_vsdetail_url():
    parsed = URLParser.parse("https://www.douyin.com/vsdetail/7331203341890049058")

    assert parsed is not None
    assert parsed["type"] == "live_replay"
    assert parsed["episode_id"] == "7331203341890049058"


def test_parse_live_replay_reflow_share_url():
    parsed = URLParser.parse(
        "https://webcast.amemv.com/douyin/webcast/reflow/episode/"
        "7331203341890049058?replay_id=7331203341890049058"
    )

    assert parsed is not None
    assert parsed["type"] == "live_replay"
    assert parsed["episode_id"] == "7331203341890049058"


def test_parse_live_replay_uses_replay_id_when_present():
    parsed = URLParser.parse(
        "https://webcast.amemv.com/douyin/webcast/reflow/episode/"
        "7331203341890049058?replay_id=7339999999999999999"
    )

    assert parsed is not None
    assert parsed["type"] == "live_replay"
    assert parsed["episode_id"] == "7331203341890049058"
    assert parsed["replay_id"] == "7339999999999999999"


def test_parse_live_replay_rejects_spoofed_host():
    assert URLParser.parse(
        "https://evil.com/douyin/webcast/reflow/episode/7331203341890049058"
    ) is None


def test_parse_live_replay_rejects_unapproved_amemv_subdomain():
    assert URLParser.parse(
        "https://foo.amemv.com/douyin/webcast/reflow/episode/7331203341890049058"
    ) is None


def test_parse_live_replay_rejects_non_exact_paths():
    assert URLParser.parse(
        "https://webcast.amemv.com/foo/douyin/webcast/reflow/episode/7331203341890049058"
    ) is None
    assert URLParser.parse("https://www.douyin.com/foo/vsdetail/7331203341890049058") is None


def test_parse_webcast_non_replay_path_is_unsupported():
    assert URLParser.parse("https://webcast.amemv.com/video/7331203341890049058") is None
    assert URLParser.parse("https://webcast.amemv.com/?modal_id=7331203341890049058") is None
