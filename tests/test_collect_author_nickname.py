import asyncio

from test_user_downloader import _build_downloader, _FakeAPIClient, _make_aweme


def test_collect_mode_uses_each_item_author_for_directory(tmp_path, monkeypatch):
    # A favorite-collection feed contains videos from many accounts. The
    # viewer feed has no single author ("self"), so every item must be filed
    # under its own author.nickname instead of the viewer placeholder.
    downloader = _build_downloader(tmp_path, _FakeAPIClient(), browser_enabled=False)
    items = [
        _make_aweme("7412345678901234590", author={"nickname": "Alice", "uid": "u1"}),
        _make_aweme("7412345678901234591", author={"nickname": "Bob", "uid": "u2"}),
    ]
    seen_names = []

    async def _record(item, author_name, **_kwargs):
        seen_names.append(author_name)
        return True

    monkeypatch.setattr(downloader, "_download_aweme_assets", _record)

    result = asyncio.run(downloader._download_mode_items("collect", items, "self"))
    assert result.success == 2
    assert seen_names == ["Alice", "Bob"]
