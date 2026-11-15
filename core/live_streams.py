"""直播流候选归一化，不持久化签名地址。"""

import json
import re
from urllib.parse import urlsplit

# 直播页 RSC 流的一行：``id:`` 开头；文本行是 ``id:T<十六进制字节数>,<正文>``，正文后直接接下一行。
FLIGHT_ROW = re.compile(r"([0-9a-fA-F]+):(?:T([0-9a-fA-F]+),)?")

QUALITY = {
    "origin": 100,
    "uhd": 95,
    "full_hd1": 90,
    "full_hd": 90,
    "hd1": 70,
    "hd": 70,
    "sd1": 50,
    "sd2": 50,
    "sd": 50,
    "ld": 30,
}


def flight_text_rows(stream):
    """取出 RSC 流里的文本行 {id: 正文}；其余行按换行跳过。"""
    rows, pos = {}, 0
    while pos < len(stream):
        match = FLIGHT_ROW.match(stream, pos)
        if match and match.group(2):
            size = int(match.group(2), 16)
            # 长度按 UTF-8 字节计；正文字符数不会超过字节数，先按字符截够再按字节裁。
            raw = stream[match.end() : match.end() + size].encode("utf-8")[:size]
            text = raw.decode("utf-8", errors="ignore")
            rows[match.group(1)] = text
            pos = match.end() + len(text)
            continue
        newline = stream.find("\n", pos)
        if newline < 0:
            break
        pos = newline + 1
    return rows


def inline_stream_data(room, rows):
    """直播页把大段 stream_data 写成 "$<id>" 引用：换回正文，原画档位才选得到。"""
    stream = room.get("stream_url")
    sdk = stream.get("live_core_sdk_data") if isinstance(stream, dict) else None
    pull = sdk.get("pull_data") if isinstance(sdk, dict) else None
    value = pull.get("stream_data") if isinstance(pull, dict) else None
    if isinstance(value, str) and value.startswith("$") and value[1:] in rows:
        pull["stream_data"] = rows[value[1:]]


def select_stream(room):
    stream = room.get("stream_url")
    if not isinstance(stream, dict):
        return None, ""
    candidates = []
    sdk_data = stream.get("live_core_sdk_data")
    pull = sdk_data.get("pull_data") if isinstance(sdk_data, dict) else {}
    pull = pull if isinstance(pull, dict) else {}
    try:
        sdk = json.loads(pull.get("stream_data") or "{}").get("data") or {}
    except (ValueError, TypeError, AttributeError):
        sdk = {}
    sdk = sdk if isinstance(sdk, dict) else {}
    for quality, value in sdk.items():
        main = value.get("main") if isinstance(value, dict) else None
        for protocol in ("flv", "hls"):
            if isinstance(main, dict):
                candidates.append((quality, main.get(protocol), protocol == "flv"))
    for field in ("flv_pull_url", "hls_pull_url_map"):
        mapping = stream.get(field)
        if isinstance(mapping, dict):
            candidates.extend((key, url, field == "flv_pull_url") for key, url in mapping.items())
        elif isinstance(mapping, str):
            candidates.append((field, mapping, True))
    candidates.extend(
        (key, stream.get(key), key != "hls_pull_url") for key in ("hls_pull_url", "rtmp_pull_url")
    )
    valid = [
        c
        for c in candidates
        if isinstance(c[1], str)
        and urlsplit(c[1]).scheme in ("http", "https")
        and urlsplit(c[1]).hostname
    ]
    if not valid:
        return None, ""
    quality, url, _ = max(valid, key=lambda c: (QUALITY.get(str(c[0]).lower(), 0), c[2]))
    return url, str(quality)
