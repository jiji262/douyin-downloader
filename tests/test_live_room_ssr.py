import json

import pytest

from core.api_client import DouyinAPIClient
from core.live_streams import select_stream


def _room(room_id, **extra):
    return {
        "id_str": room_id,
        "status": 2,
        "stream_url": {"flv_pull_url": {"ORIGIN": "https://cdn.invalid/live.flv"}},
        **extra,
    }


def _page(flight):
    data = json.dumps([1, flight])
    return "<script>self.__pace_f.push(" + data + ")</script>"


def _real_shape(prefix="d:"):
    """脱敏后的真实页面结构：web_rid 只在 roomInfo 上，房间对象本身不带 web_rid。"""
    state = {
        "recommendStore": {"rooms": [_room("decoy-before", owner={"web_rid": "other"})]},
        "roomStore": {"roomInfo": {"web_rid": "wanted", "room": _room("internal-1", owner={})}},
        "feedStore": {"cards": [_room("decoy-anonymous")]},
    }
    return _page(prefix + json.dumps(["$", "$L1", None, {"state": state}]))


@pytest.mark.parametrize("prefix", ["c:", "d:", "e:", "1a:"])
def test_ssr_accepts_variable_flight_ids(prefix):
    parsed = DouyinAPIClient._extract_live_room_from_html(_real_shape(prefix), web_rid="wanted")
    assert parsed is not None and parsed["room"]["id_str"] == "internal-1"


def test_ssr_matches_target_through_room_info_web_rid_and_skips_decoys():
    parsed = DouyinAPIClient._extract_live_room_from_html(_real_shape(), web_rid="wanted")
    assert parsed["room"]["id_str"] == "internal-1"
    assert parsed["raw"] == {"source": "live_page_ssr"}


def test_ssr_returns_none_when_only_other_rooms_are_present():
    flight = "d:" + json.dumps(
        {"rooms": [_room("decoy", owner={"web_rid": "other"}), _room("decoy-anonymous")]}
    )
    assert DouyinAPIClient._extract_live_room_from_html(_page(flight), web_rid="wanted") is None


def test_ssr_does_not_inherit_web_rid_from_distant_ancestor():
    flight = "d:" + json.dumps({"web_rid": "wanted", "list": {"items": [_room("decoy-deep")]}})
    assert DouyinAPIClient._extract_live_room_from_html(_page(flight), web_rid="wanted") is None


def test_ssr_owner_identity_wins_over_parent_identity():
    flight = "d:" + json.dumps({"web_rid": "wanted", "room": _room("x", owner={"web_rid": "other"})})
    assert DouyinAPIClient._extract_live_room_from_html(_page(flight), web_rid="wanted") is None


def _text_row(row_id, text):
    return f"{row_id}:T{len(text.encode('utf-8')):x},{text}"


def test_ssr_resolves_stream_data_reference_so_origin_is_selectable():
    """真实页面把 stream_data 写成 "$13"：正文是按字节计长的文本行，可能跨推送、紧跟在别的文本行后。"""
    stream_data = json.dumps(
        {"data": {"origin": {"main": {"flv": "https://cdn.invalid/origin.flv"}}}, "名称": "原画"},
        ensure_ascii=False,
    )
    room = _room("internal-1", owner={"web_rid": "wanted"})
    room["stream_url"] = {
        "flv_pull_url": {"FULL_HD1": "https://cdn.invalid/uhd.flv"},
        "live_core_sdk_data": {"pull_data": {"stream_data": "$13"}},
    }
    rows = "12:" + json.dumps(["x"]) + "\n" + _text_row("1f", "前一段文本") + _text_row("13", stream_data) + "14:[]\n"
    html = _page("d:" + json.dumps({"room": room})) + _page(rows[:30]) + _page(rows[30:])

    parsed = DouyinAPIClient._extract_live_room_from_html(html, web_rid="wanted")

    assert select_stream(parsed["room"]) == ("https://cdn.invalid/origin.flv", "origin")


def test_ssr_keeps_unresolvable_stream_data_reference():
    room = _room("internal-1", owner={"web_rid": "wanted"})
    room["stream_url"]["live_core_sdk_data"] = {"pull_data": {"stream_data": "$99"}}

    parsed = DouyinAPIClient._extract_live_room_from_html(_page("d:" + json.dumps({"room": room})), web_rid="wanted")

    assert parsed["room"]["stream_url"]["live_core_sdk_data"]["pull_data"]["stream_data"] == "$99"
