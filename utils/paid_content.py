"""付费内容识别与 MP4 加密检测。

抖音付费作品在转码期就生成两份独立资产：``video.play_addr`` 是明文试看
渲染版（``preview_config.end_time`` 之后画面被预渲染成模糊、音轨为数字
静音），``video.download_addr`` 是 CENC 加密的全长正片。密文按 ISO/IEC
23001-7 封装且不带 ``pssh``，密钥只由抖音授权接口下发，落盘后任何播放器
都解不开——因此宁可不下，也不要产出一份看不了的文件。

判据只认 ``charge_info.is_charge_content``：免费作品该字段整体为 null。
``video_control.allow_download`` 实测在免费作品上同样是 false，没有区分度。
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any, Dict, Optional, Union

# CENC 把原始 format 藏进 frma、把 sample entry 换成 encv/enca，ffprobe 会
# 读 frma 静默还原成 h264/avc1，因此加密检测必须直接扫 box，不能问 ffprobe。
_ENCRYPTED_SAMPLE_ENTRIES = {b"encv", b"enca", b"encs", b"enct", b"encm"}
_ENCRYPTION_SCHEMES = {b"cenc", b"cbcs", b"cens", b"cbc1"}
# sample entry 的固定字段长度，其后才是 sinf 等子 box。
_SAMPLE_ENTRY_HEADER = {b"encv": 78, b"avc1": 78, b"hvc1": 78, b"enca": 28, b"mp4a": 28}
_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"sinf", b"schi"}
_MAX_DEPTH = 12


def is_paid_content(aweme_data: Any) -> bool:
    """作品是否为付费/会员内容。"""
    if not isinstance(aweme_data, dict):
        return False
    charge_info = aweme_data.get("charge_info")
    if not isinstance(charge_info, dict):
        return False
    return bool(charge_info.get("is_charge_content"))


def paid_preview_seconds(aweme_data: Any) -> Optional[float]:
    """试看时长（秒）；付费作品未必都带 preview_config。"""
    if not is_paid_content(aweme_data):
        return None
    preview = (aweme_data.get("charge_info") or {}).get("preview_config")
    if not isinstance(preview, dict):
        return None
    try:
        end_ms = float(preview.get("end_time") or 0)
    except (TypeError, ValueError):
        return None
    return end_ms / 1000 if end_ms > 0 else None


def detect_mp4_encryption(path: Union[str, Path]) -> Optional[str]:
    """返回 MP4 的加密方案名（``cenc`` / ``cbcs`` …），未加密或无法解析时 None。

    只读 box 头部并 seek 跳过内容，因此对上百 MB 的文件同样是常数级 IO。
    任何解析异常都退化为 None——检测失败不该阻断正常下载。
    """
    try:
        file_path = Path(path)
        size = file_path.stat().st_size
        with file_path.open("rb") as handle:
            return _scan_boxes(handle, 0, size, 0)
    except (OSError, ValueError, struct.error):
        return None


def _scan_boxes(handle, start: int, end: int, depth: int) -> Optional[str]:
    """在 [start, end) 内遍历同级 box，命中加密声明即返回方案名。"""
    if depth > _MAX_DEPTH:
        return None
    handle.seek(start)
    offset = start
    while offset + 8 <= end:
        header = handle.read(8)
        if len(header) < 8:
            return None
        box_size, box_type = struct.unpack(">I4s", header)
        body_start = offset + 8
        if box_size == 1:
            extended = handle.read(8)
            if len(extended) < 8:
                return None
            box_size = struct.unpack(">Q", extended)[0]
            body_start = offset + 16
        elif box_size == 0:
            box_size = end - offset
        box_end = offset + box_size
        # 截断或损坏的文件会给出越界 size，必须停止而不是继续瞎读。
        if box_size < body_start - offset or box_end > end:
            return None

        scheme = _inspect_box(handle, box_type, body_start, box_end, depth)
        if scheme:
            return scheme

        offset = box_end
        handle.seek(offset)
    return None


def _inspect_box(
    handle, box_type: bytes, body_start: int, box_end: int, depth: int
) -> Optional[str]:
    """按 box 类型决定：直接判定、还是继续下潜。"""
    if box_type == b"schm":
        handle.seek(body_start + 4)  # 跳过 version/flags
        scheme_type = handle.read(4)
        return scheme_type.decode("ascii", "ignore") if scheme_type in _ENCRYPTION_SCHEMES else None

    if box_type == b"stsd":
        # stsd = version/flags(4) + entry_count(4)，其后是 sample entry 列表。
        return _scan_boxes(handle, body_start + 8, box_end, depth + 1)

    if box_type in _ENCRYPTED_SAMPLE_ENTRIES:
        # sample entry 已经是 encv/enca，即便 schm 读不到也足以判定加密。
        header_len = _SAMPLE_ENTRY_HEADER.get(box_type, 0)
        return _scan_boxes(handle, body_start + header_len, box_end, depth + 1) or "cenc"

    if box_type in _SAMPLE_ENTRY_HEADER:
        return _scan_boxes(handle, body_start + _SAMPLE_ENTRY_HEADER[box_type], box_end, depth + 1)

    if box_type in _CONTAINERS:
        return _scan_boxes(handle, body_start, box_end, depth + 1)

    return None


def paid_content_warning(aweme_data: Dict[str, Any]) -> Optional[str]:
    """付费内容的告警文案；非付费或当前账号已购买时返回 None。

    ``has_paid`` 为真时服务端交付的是完整正片，下载本就该成功，再打
    「下载后无法播放」只会把好文件说成坏文件。
    """
    if not is_paid_content(aweme_data):
        return None
    if (aweme_data.get("charge_info") or {}).get("has_paid"):
        return None
    preview = paid_preview_seconds(aweme_data)
    window = f"，仅前 {int(preview)} 秒为正片内容" if preview else ""
    return f"付费/会员内容，完整版为 CENC 加密、下载后无法播放{window}"
