"""合集归属表不完整时，混合任务不能把未知成员放进作品目录。"""

import pytest
from test_user_mix_routing import IN_A, IN_B, PLAIN, RoutingAPI, aweme, build, page

import core.api_client as api_module
from core.api_client import DouyinAPIClient


@pytest.mark.parametrize("failure", ["mix", "series", "exception", "series_stall", "series_cap"])
async def test_partial_mix_sources_skip_post_mode(tmp_path, monkeypatch, failure):
    client = DouyinAPIClient({"msToken": "test-token"})
    api = RoutingAPI()
    client.get_user_info = api.get_user_info
    client.get_user_post = api.get_user_post
    client.get_mix_aweme = api.get_mix_aweme
    monkeypatch.setattr(api_module, "_SERIES_LIST_MAX_PAGES", 1)

    async def request(path, params, **_kwargs):
        if path.endswith("mix/list/"):
            return (
                {}
                if failure == "mix"
                else {
                    "status_code": 0,
                    "mix_infos": [{"mix_id": "mix-a", "mix_name": "合集甲"}],
                    "has_more": 0,
                    "cursor": 0,
                }
            )
        assert path.endswith("series/list/")
        if failure == "series":
            return {}
        if failure == "exception":
            raise RuntimeError("series transport failed")
        return {
            "status_code": 0,
            "series_infos": [{"series_id": "mix-b", "series_name": "合集乙"}],
            "has_more": failure in {"series_stall", "series_cap"},
            "cursor": 1 if failure == "series_cap" else 0,
        }

    monkeypatch.setattr(client, "_request_json", request)
    downloader, saved = build(tmp_path, monkeypatch, ["post", "mix"], api=client)
    result = await downloader.download({"sec_uid": "SEC_AUTHOR"})
    assert result.incomplete_reason
    assert PLAIN not in dict(saved)
    assert all(parts[1] == "mix" for _, parts in saved)


@pytest.mark.parametrize("stalled", ["catalog", "members"])
async def test_stalled_mix_pagination_marks_result_incomplete(tmp_path, monkeypatch, stalled):
    api = RoutingAPI()
    if stalled == "catalog":

        async def catalog(*_args, **_kwargs):
            return page(api.mixes, has_more=True, max_cursor=0)

        monkeypatch.setattr(api, "get_user_mix", catalog)
    else:

        async def members(mix_id, **_kwargs):
            return page([aweme(IN_A if mix_id == "mix-a" else IN_B)], has_more=True, max_cursor=0)

        monkeypatch.setattr(api, "get_mix_aweme", members)
    downloader, saved = build(tmp_path, monkeypatch, ["post", "mix"], api=api)
    result = await downloader.download({"sec_uid": "SEC_AUTHOR"})
    assert result.incomplete_reason
    assert PLAIN not in dict(saved)


async def test_mixed_inline_awemes_and_metadata_both_expand(tmp_path, monkeypatch):
    api = RoutingAPI()
    api.mixes = [
        aweme(IN_A, mix_info={"mix_id": "mix-a", "mix_name": "合集甲"}),
        {"mix_id": "mix-b", "mix_name": "合集乙"},
    ]
    downloader, saved = build(tmp_path, monkeypatch, ["post", "mix"], api=api)
    await downloader.download({"sec_uid": "SEC_AUTHOR"})
    assert dict(saved)[IN_A][1:3] == ("mix", "合集甲")
    assert dict(saved)[IN_B][1:3] == ("mix", "合集乙")


@pytest.mark.parametrize("empty", [None, []])
async def test_author_without_collections_still_downloads_posts(tmp_path, monkeypatch, empty):
    client = DouyinAPIClient({"msToken": "test-token"})
    api = RoutingAPI()
    client.get_user_info = api.get_user_info
    client.get_user_post = api.get_user_post

    async def request(path, params, **_kwargs):
        key = "mix_infos" if path.endswith("mix/list/") else "series_infos"
        return {"status_code": 0, key: empty, "has_more": 0, "cursor": 0}

    monkeypatch.setattr(client, "_request_json", request)
    downloader, saved = build(tmp_path, monkeypatch, ["post", "mix"], api=client)
    result = await downloader.download({"sec_uid": "SEC_AUTHOR"})
    assert result.total == result.success == 3
    assert result.incomplete_reason is None
    assert all(parts[1] == "post" for _, parts in saved)
