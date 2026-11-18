import asyncio

from test_user_downloader import _build_downloader, _FakeAPIClient, _make_aweme


def test_collect_mode_passes_each_item_author_sec_uid(tmp_path, monkeypatch):
    # With author_dir styles that use IDs, passing the viewer's sec_uid for
    # every favorite collapses videos from different authors into one folder.
    downloader = _build_downloader(tmp_path, _FakeAPIClient(), browser_enabled=False)
    items = [
        _make_aweme(
            "7412345678901234590",
            author={"nickname": "Alice", "uid": "u1", "sec_uid": "sec-alice"},
        ),
        _make_aweme(
            "7412345678901234591",
            author={"nickname": "Bob", "uid": "u2", "sec_uid": "sec-bob"},
        ),
    ]
    seen = []

    async def _record(item, author_name, **kwargs):
        seen.append((author_name, kwargs.get("author_sec_uid")))
        return True

    monkeypatch.setattr(downloader, "_download_aweme_assets", _record)

    result = asyncio.run(downloader._download_mode_items("collect", items, "self"))
    assert result.success == 2
    assert seen == [("Alice", "sec-alice"), ("Bob", "sec-bob")]
