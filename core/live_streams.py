"""直播流候选归一化，不持久化签名地址。"""

import json
from urllib.parse import urlsplit

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
