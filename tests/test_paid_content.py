"""付费内容识别与 MP4 加密检测。

样本语义取自实测（作者「酒痴东梦」20 条作品 + aweme 7640058716376583458）：
付费作品 ``charge_info.is_charge_content`` 为 true，免费作品该字段整体为
null；``video_control.allow_download`` 在两类作品上都是 false，没有区分度。
"""

import struct

import pytest

from utils.paid_content import (
    detect_mp4_encryption,
    is_paid_content,
    paid_content_warning,
    paid_preview_seconds,
)


def _box(box_type: bytes, payload: bytes = b"") -> bytes:
    return struct.pack(">I", 8 + len(payload)) + box_type + payload


def _visual_sample_entry(entry_type: bytes, children: bytes = b"") -> bytes:
    # VisualSampleEntry 固定字段共 78 字节，其后才是子 box（avcC / sinf）。
    return _box(entry_type, b"\x00" * 78 + children)


def _audio_sample_entry(entry_type: bytes, children: bytes = b"") -> bytes:
    # AudioSampleEntry 固定字段共 28 字节。
    return _box(entry_type, b"\x00" * 28 + children)


def _sinf(original_format: bytes, scheme: bytes) -> bytes:
    frma = _box(b"frma", original_format)
    schm = _box(b"schm", b"\x00\x00\x00\x00" + scheme + b"\x00\x01\x00\x00")
    tenc = _box(b"tenc", b"\x00" * 6 + b"\x01\x08" + b"\xaa" * 16)
    return _box(b"sinf", frma + schm + _box(b"schi", tenc))


def _mp4(sample_entries: bytes, *, moov_last: bool = False) -> bytes:
    stsd = _box(b"stsd", b"\x00\x00\x00\x00" + struct.pack(">I", 1) + sample_entries)
    stbl = _box(b"stbl", stsd)
    minf = _box(b"minf", stbl)
    mdia = _box(b"mdia", minf)
    trak = _box(b"trak", mdia)
    moov = _box(b"moov", trak)
    ftyp = _box(b"ftyp", b"isom" + b"\x00" * 8)
    mdat = _box(b"mdat", b"\x00" * 512)
    return ftyp + (mdat + moov if moov_last else moov + mdat)


def _write(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return path


# --- is_paid_content ---------------------------------------------------


def test_paid_work_is_detected():
    aweme = {"charge_info": {"is_charge_content": True, "has_paid": False}}
    assert is_paid_content(aweme) is True


def test_free_work_has_null_charge_info():
    assert is_paid_content({"charge_info": None}) is False


def test_missing_charge_info_key_is_free():
    assert is_paid_content({}) is False


def test_charge_info_present_but_flag_false_is_free():
    assert is_paid_content({"charge_info": {"is_charge_content": False}}) is False


def test_allow_download_false_alone_does_not_mean_paid():
    # 实测 20/20 条作品（含全部免费作品）都是 false，不可用作判据。
    aweme = {"charge_info": None, "video_control": {"allow_download": False}}
    assert is_paid_content(aweme) is False


def test_non_dict_input_is_free():
    assert is_paid_content(None) is False
    assert is_paid_content([]) is False


# --- paid_preview_seconds ----------------------------------------------


def test_preview_end_time_converted_to_seconds():
    aweme = {
        "charge_info": {
            "is_charge_content": True,
            "preview_config": {"is_preview": True, "start_time": 0, "end_time": 180000},
        }
    }
    assert paid_preview_seconds(aweme) == pytest.approx(180.0)


def test_paid_work_without_preview_config_returns_none():
    # aweme 7666452465742531840 实测：charge_info 只有 4 个键，无 preview_config。
    assert paid_preview_seconds({"charge_info": {"is_charge_content": True}}) is None


def test_free_work_has_no_preview_window():
    assert paid_preview_seconds({"charge_info": None}) is None


def test_zero_end_time_returns_none():
    aweme = {"charge_info": {"is_charge_content": True, "preview_config": {"end_time": 0}}}
    assert paid_preview_seconds(aweme) is None


# --- detect_mp4_encryption ---------------------------------------------


def test_cenc_video_track_is_detected(tmp_path):
    entry = _visual_sample_entry(b"encv", _sinf(b"avc1", b"cenc"))
    path = _write(tmp_path, "enc.mp4", _mp4(entry))
    assert detect_mp4_encryption(path) == "cenc"


def test_cbcs_audio_track_is_detected(tmp_path):
    entry = _audio_sample_entry(b"enca", _sinf(b"mp4a", b"cbcs"))
    path = _write(tmp_path, "enca.mp4", _mp4(entry))
    assert detect_mp4_encryption(path) == "cbcs"


def test_plain_avc1_is_not_encrypted(tmp_path):
    entry = _visual_sample_entry(b"avc1", _box(b"avcC", b"\x01\x64\x00\x1f"))
    path = _write(tmp_path, "plain.mp4", _mp4(entry))
    assert detect_mp4_encryption(path) is None


def test_moov_after_mdat_is_still_scanned(tmp_path):
    entry = _visual_sample_entry(b"encv", _sinf(b"avc1", b"cenc"))
    path = _write(tmp_path, "tail.mp4", _mp4(entry, moov_last=True))
    assert detect_mp4_encryption(path) == "cenc"


def test_truncated_file_does_not_raise(tmp_path):
    entry = _visual_sample_entry(b"encv", _sinf(b"avc1", b"cenc"))
    path = _write(tmp_path, "cut.mp4", _mp4(entry)[:40])
    assert detect_mp4_encryption(path) is None


def test_missing_file_returns_none(tmp_path):
    assert detect_mp4_encryption(tmp_path / "nope.mp4") is None


def test_non_mp4_bytes_return_none(tmp_path):
    path = _write(tmp_path, "junk.bin", b"not an mp4 at all" * 64)
    assert detect_mp4_encryption(path) is None


def test_declared_box_size_beyond_eof_does_not_hang(tmp_path):
    # 截断下载会留下越界的 box size，扫描必须收敛而不是死循环。
    path = _write(
        tmp_path, "bogus.mp4", _box(b"ftyp", b"isom") + struct.pack(">I", 1 << 30) + b"moov"
    )
    assert detect_mp4_encryption(path) is None


# --- paid_content_warning ----------------------------------------------


def test_unpurchased_paid_work_is_warned_with_preview_window():
    aweme = {
        "charge_info": {
            "is_charge_content": True,
            "has_paid": False,
            "preview_config": {"end_time": 180000},
        }
    }
    note = paid_content_warning(aweme)
    assert note is not None
    assert "180" in note


def test_purchased_paid_work_is_not_warned():
    # 已购买时服务端交付完整正片，下载本就该成功，不能说成「无法播放」。
    aweme = {"charge_info": {"is_charge_content": True, "has_paid": True}}
    assert paid_content_warning(aweme) is None


def test_free_work_is_not_warned():
    assert paid_content_warning({"charge_info": None}) is None
