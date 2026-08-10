"""collect_use_aweme_author_dir: save under original author, not feed owner."""

from control.queue_manager import QueueManager
from core.user_downloader import UserDownloader
from storage.file_manager import FileManager


class _FakeConfig:
    def __init__(self, data):
        self._data = data

    def get(self, key, default=None):
        return self._data.get(key, default)


def _downloader(tmp_path, **cfg_extra):
    data = {
        "mode": ["collect"],
        "thread": 1,
        "collect_use_aweme_author_dir": True,
        "author_dir": "nickname",
        "group_by_mode": True,
        "folderstyle": True,
    }
    data.update(cfg_extra)
    return UserDownloader(
        config=_FakeConfig(data),
        api_client=object(),
        file_manager=FileManager(str(tmp_path / "Downloaded")),
        cookie_manager=object(),
        database=None,
        rate_limiter=None,
        retry_handler=None,
        queue_manager=QueueManager(max_workers=1),
    )


def test_resolve_save_author_collect_uses_aweme_nickname(tmp_path):
    dl = _downloader(tmp_path)
    item = {
        "aweme_id": "123",
        "author": {"nickname": "刘小排", "sec_uid": "sec_liu"},
    }
    name, sec = dl._resolve_save_author(
        item,
        mode="collect",
        feed_author_name="self",
        feed_author_sec_uid="self",
    )
    assert name == "刘小排"
    assert sec == "sec_liu"


def test_resolve_save_author_collect_can_keep_feed_owner(tmp_path):
    dl = _downloader(tmp_path, collect_use_aweme_author_dir=False)
    item = {"aweme_id": "123", "author": {"nickname": "刘小排", "sec_uid": "sec_liu"}}
    name, sec = dl._resolve_save_author(
        item,
        mode="collect",
        feed_author_name="self",
        feed_author_sec_uid="self",
    )
    assert name == "self"
    assert sec == "self"


def test_resolve_save_author_post_keeps_feed_owner(tmp_path):
    dl = _downloader(tmp_path)
    item = {"aweme_id": "123", "author": {"nickname": "别人", "sec_uid": "sec_x"}}
    name, sec = dl._resolve_save_author(
        item,
        mode="post",
        feed_author_name="博主A",
        feed_author_sec_uid="sec_a",
    )
    assert name == "博主A"
    assert sec == "sec_a"
