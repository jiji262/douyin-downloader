"""用户主页合集优先归类与跨模式去重。"""

from types import SimpleNamespace

import pytest

from control.queue_manager import QueueManager
from core.user_downloader import UserDownloader
from core.user_modes.base_strategy import PageRequestFailedError
from storage.file_manager import FileManager

AUTHOR = {"nickname": "博主", "sec_uid": "SEC_AUTHOR", "uid": "uid-1"}
IN_A, IN_B, PLAIN = "7000000000000000001", "7000000000000000002", "7000000000000000003"


def aweme(aid, **extra):
    return {
        "aweme_id": aid,
        "desc": "视频",
        "create_time": 1700000000,
        "author": AUTHOR,
        "video": {"play_addr": {"url_list": ["https://example.com/v.mp4"]}},
        **extra,
    }


def page(items, **extra):
    return {"items": items, "has_more": False, "max_cursor": 0, "status_code": 0, **extra}


class RoutingAPI:
    def __init__(self):
        self.mixes = [
            {"mix_info": {"mix_id": "mix-a", "mix_name": "合集甲"}},
            {"mix_id": "mix-b", "mix_name": "合集乙"},
        ]
        self.posts = [aweme(IN_A), aweme(IN_B), aweme(PLAIN)]
        self.pages = None

    async def get_user_info(self, _sec_uid):
        return AUTHOR

    async def get_user_post(self, _sec_uid, max_cursor=0, count=20):
        if self.pages is not None:
            return self.pages[max_cursor]
        return page(self.posts)

    async def get_user_mix(self, _sec_uid, max_cursor=0, count=20):
        return page(self.mixes)

    async def get_mix_aweme(self, mix_id, cursor=0, count=20):
        return page([aweme(IN_A if mix_id == "mix-a" else IN_B)])

    async def get_user_like(self, _sec_uid, max_cursor=0, count=20):
        return page([aweme(IN_A)])


async def noop(*_args, **_kwargs):
    return None


def build(tmp_path, monkeypatch, modes, *, api=None, **settings):
    data = {
        "mode": modes,
        "number": {},
        "increase": {},
        "browser_fallback": {"enabled": False},
        **settings,
    }
    downloader = UserDownloader(
        config=SimpleNamespace(get=lambda key, default=None: data.get(key, default)),
        api_client=api or RoutingAPI(),
        file_manager=FileManager(str(tmp_path)),
        cookie_manager=None,
        database=None,
        rate_limiter=SimpleNamespace(acquire=noop),
        retry_handler=None,
        queue_manager=QueueManager(max_workers=2),
    )
    saved = []

    async def capture(item, author_name, **kwargs):
        ctx = downloader._build_aweme_file_context(
            item,
            author_name,
            kwargs["mode"],
            author_sec_uid=kwargs.get("author_sec_uid"),
            collection_dir=kwargs.get("collection_dir"),
        )
        saved.append((item["aweme_id"], ctx["save_dir"].relative_to(tmp_path).parts))
        return True

    monkeypatch.setattr(downloader, "_download_aweme_assets", capture)
    return downloader, saved


@pytest.mark.parametrize("modes", [["post", "mix"], ["mix", "post"], ["like", "post", "mix"]])
async def test_mix_members_belong_to_named_collections_once(tmp_path, monkeypatch, modes):
    downloader, saved = build(tmp_path, monkeypatch, modes)
    result = await downloader.download({"sec_uid": "SEC_AUTHOR"})
    paths = dict(saved)
    assert len(saved) == result.total == result.success == 3
    assert paths[IN_A][1:3] == ("mix", "合集甲")
    assert paths[IN_B][1:3] == ("mix", "合集乙")
    assert paths[PLAIN][1] == "post"


async def test_post_without_mix_preserves_all_posts(tmp_path, monkeypatch):
    downloader, saved = build(tmp_path, monkeypatch, ["post"])
    result = await downloader.download({"sec_uid": "SEC_AUTHOR"})
    assert result.total == 3
    assert all(parts[1] == "post" for _, parts in saved)


async def test_only_mix_groups_each_collection_without_downloading_plain_posts(tmp_path, monkeypatch):
    downloader, saved = build(tmp_path, monkeypatch, ["mix"])

    async def forbidden_posts(*_args, **_kwargs):
        raise AssertionError("只勾合集时不能抓取普通作品列表")

    monkeypatch.setattr(downloader.api_client, "get_user_post", forbidden_posts)
    result = await downloader.download({"sec_uid": "SEC_AUTHOR"})
    paths = dict(saved)
    assert len(saved) == result.total == result.success == 2
    assert paths[IN_A][1:3] == ("mix", "合集甲")
    assert paths[IN_B][1:3] == ("mix", "合集乙")
    assert PLAIN not in paths


async def test_mix_cap_does_not_spill_members_into_posts(tmp_path, monkeypatch):
    downloader, saved = build(tmp_path, monkeypatch, ["post", "mix"], number={"mix": 1})
    result = await downloader.download({"sec_uid": "SEC_AUTHOR"})
    assert result.total == 2
    assert {aid for aid, _ in saved} == {IN_A, PLAIN}


async def test_post_limit_counts_non_mix_candidates_across_pages(tmp_path, monkeypatch):
    api = RoutingAPI()
    api.pages = [
        page([aweme(IN_A), aweme(IN_B)], has_more=True, max_cursor=1),
        page([aweme(PLAIN)]),
    ]
    downloader, saved = build(tmp_path, monkeypatch, ["post", "mix"], api=api, number={"post": 1})
    result = await downloader.download({"sec_uid": "SEC_AUTHOR"})
    assert result.total == 3
    assert dict(saved)[PLAIN][1] == "post"


async def test_collection_layer_survives_disabled_mode_and_item_folders(tmp_path, monkeypatch):
    downloader, saved = build(
        tmp_path,
        monkeypatch,
        ["mix", "post"],
        group_by_mode=False,
        folderstyle=False,
    )
    await downloader.download({"sec_uid": "SEC_AUTHOR"})
    assert dict(saved) == {
        IN_A: ("博主", "合集甲"),
        IN_B: ("博主", "合集乙"),
        PLAIN: ("博主",),
    }


async def test_missing_mix_name_falls_back_to_id(tmp_path, monkeypatch):
    api = RoutingAPI()
    api.mixes = [{"mix_info": {"mix_id": "mix-a"}}]
    downloader, saved = build(tmp_path, monkeypatch, ["mix"], api=api)
    await downloader.download({"sec_uid": "SEC_AUTHOR"})
    assert dict(saved)[IN_A][1:3] == ("mix", "mix-a")


async def test_direct_aweme_mix_metadata_names_the_collection(tmp_path, monkeypatch):
    api = RoutingAPI()
    api.mixes = [aweme(IN_A, mix_info={"mix_id": "mix-a", "mix_name": "合集甲"})]
    downloader, saved = build(tmp_path, monkeypatch, ["post", "mix"], api=api)
    await downloader.download({"sec_uid": "SEC_AUTHOR"})
    assert dict(saved)[IN_A][1:3] == ("mix", "合集甲")


async def test_membership_is_reserved_before_mix_filters(tmp_path, monkeypatch):
    downloader, saved = build(tmp_path, monkeypatch, ["post", "mix"])
    strategy = downloader._get_mode_strategy("mix")
    monkeypatch.setattr(strategy, "apply_filters", lambda items: [])
    result = await downloader.download({"sec_uid": "SEC_AUTHOR"})
    assert result.total == 1
    assert saved[0][0] == PLAIN


async def test_failed_mix_scan_does_not_misfile_members_as_posts(tmp_path, monkeypatch):
    downloader, saved = build(tmp_path, monkeypatch, ["post", "mix"])

    async def failed_scan(*_args, **_kwargs):
        raise PageRequestFailedError("合集扫描失败")

    monkeypatch.setattr(downloader.api_client, "get_user_mix", failed_scan)
    with pytest.raises(PageRequestFailedError):
        await downloader.download({"sec_uid": "SEC_AUTHOR"})
    assert saved == []


async def test_same_video_in_two_collections_is_downloaded_once(tmp_path, monkeypatch):
    api = RoutingAPI()

    async def overlapping(mix_id, cursor=0, count=20):
        return page([aweme(IN_A)] if mix_id == "mix-a" else [aweme(IN_A), aweme(IN_B)])

    monkeypatch.setattr(api, "get_mix_aweme", overlapping)
    downloader, saved = build(tmp_path, monkeypatch, ["post", "mix"], api=api)
    result = await downloader.download({"sec_uid": "SEC_AUTHOR"})
    assert len(saved) == result.total == 3
    assert dict(saved)[IN_A][1:3] == ("mix", "合集甲")


async def test_membership_map_resets_when_downloader_is_reused(tmp_path, monkeypatch):
    api = RoutingAPI()
    downloader, saved = build(tmp_path, monkeypatch, ["post", "mix"], api=api)
    await downloader.download({"sec_uid": "SEC_AUTHOR"})
    api.mixes = []
    saved.clear()
    await downloader.download({"sec_uid": "SEC_AUTHOR"})
    assert len(saved) == 3
    assert all(parts[1] == "post" for _, parts in saved)
