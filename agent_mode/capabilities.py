"""能力表：哪些端点受门禁、每种 url_type 和每个 verb 需要什么级别。

`tests/test_agent_capabilities_drift.py` 会把这里的表与 `core/api_client.py`、
`utils/validators.py` 的实际情况对照，共享代码变了而这里没跟上时测试会失败。
"""

ANONYMOUS = "anonymous"
COOKIE = "cookie"
LOGIN = "login"
LOCAL = "local"
ARGUS_GATED = "argus_gated"
UNSUPPORTED = "unsupported"
LEVELS = (ANONYMOUS, COOKIE, LOGIN, LOCAL, ARGUS_GATED, UNSUPPORTED)

# core/api_client.py 中经 `_request_json_gated` 发出的全部路径。
# 开源 CLI 没有 page bridge，这些请求会被 ArgusSecurityPlugin 确定性拒绝。
GATED_ENDPOINTS = frozenset(
    {
        "/aweme/v1/web/aweme/detail/",
        "/aweme/v1/web/aweme/post/",
        "/aweme/v1/web/aweme/favorite/",
        "/aweme/v1/web/aweme/listcollection/",
        "/aweme/v1/web/mix/list/",
        "/aweme/v1/web/mix/detail/",
        "/aweme/v1/web/mix/aweme/",
        "/aweme/v1/web/mix/listcollection/",
        "/aweme/v1/web/series/list/",
        "/aweme/v1/web/music/list/",
        "/aweme/v1/web/music/detail/",
        "/aweme/v1/web/music/aweme/",
        "/aweme/v1/web/collects/list/",
        "/aweme/v1/web/collects/video/list/",
    }
)

VERB_CAPABILITIES = {
    "doctor": LOCAL,
    "inspect": ANONYMOUS,
    "hot": ANONYMOUS,
    "search": LOGIN,
    "user": COOKIE,
    "login": LOCAL,
    "live": ANONYMOUS,
    "transcribe": LOCAL,
}

# `parse_url_type` 对未解析短链返回的占位类型；inspect 先解析短链，它不进能力表。
UNRESOLVED_URL_TYPE = "short"

# url_type（取值以 utils/validators.parse_url_type 为准）-> 能力级别
URL_TYPE_CAPABILITIES = {
    "video": ARGUS_GATED,
    "gallery": ARGUS_GATED,
    "collection": ARGUS_GATED,
    "music": ARGUS_GATED,
    "user": COOKIE,
    "live": ANONYMOUS,
    "live_replay": UNSUPPORTED,
    "lvdetail": UNSUPPORTED,
}

URL_TYPE_SUGGESTED_VERB = {
    "user": "user",
    "live": "live",
}

URL_TYPE_NOTES = {
    "video": "Single-video download needs the Argus-gated aweme/detail endpoint.",
    "gallery": "Image-post download needs the Argus-gated aweme/detail endpoint.",
    "collection": "Collection (mix) download needs the Argus-gated mix endpoints.",
    "music": "Music download needs the Argus-gated music endpoints.",
    "user": (
        "Profile info is available via `user`. Downloading this user's posts is "
        "Argus-gated and not offered in agent mode."
    ),
    "live": "A live room can be recorded with `live <url> --max-duration S` while it is on air.",
    "live_replay": "Live replays are not offered in agent mode v1.",
    "lvdetail": "Douyin cinema (lvdetail) content is DRM-encrypted and cannot be downloaded.",
}
UNKNOWN_URL_NOTE = "Not a recognised Douyin URL."


def url_type_level(url_type: object) -> str:
    return URL_TYPE_CAPABILITIES.get(str(url_type or ""), UNSUPPORTED)
