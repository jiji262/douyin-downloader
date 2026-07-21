import re
from typing import Optional
from urllib.parse import parse_qs, urlparse

_WINDOWS_RESERVED_STEMS = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _host_matches(host: str, base: str) -> bool:
    return host == base or host.endswith("." + base)


def _is_douyin_web_host(host: str) -> bool:
    return _host_matches(host, "douyin.com") or _host_matches(host, "iesdouyin.com")


def _is_live_replay_path(host: str, path: str) -> bool:
    if _is_douyin_web_host(host):
        return bool(re.fullmatch(r"/vsdetail/\d+/?", path))
    return host == "webcast.amemv.com" and bool(
        re.fullmatch(r"/douyin/webcast/reflow/episode/\d+/?", path)
    )


def _is_live_reflow_path(host: str, path: str) -> bool:
    return host == "webcast.amemv.com" and bool(re.fullmatch(r"/douyin/webcast/reflow/\d+/?", path))


def validate_url(url: str) -> bool:
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except Exception:
        return False


def sanitize_filename(filename: str, max_length: int = 80) -> str:
    # 换行符 → 空格
    filename = filename.replace("\n", " ").replace("\r", " ")
    # Windows 非法字符 + #，逗号 → 下划线
    filename = re.sub(r'[<>:"/\\|?*#\x00-\x1f]', "_", filename)
    # 连续下划线 → 单个下划线（保留空格，不再把空格折叠成下划线）
    filename = re.sub(r"_+", "_", filename)
    # 连续空格 → 单个空格
    filename = re.sub(r" +", " ", filename)
    # 去首尾
    filename = filename.strip("._- ")

    if len(filename) > max_length:
        filename = filename[:max_length].rstrip("._- ")

    if filename.split(".", 1)[0].upper() in _WINDOWS_RESERVED_STEMS:
        filename = f"_{filename}"[:max_length]

    return filename or "untitled"


SHORT_URL_HOSTS = (
    "v.douyin.com",
    "v.iesdouyin.com",
    "iesdouyin.com",
)


def is_short_url(url: str) -> bool:
    """判断是否为需要预先解析的短链。"""
    if not url:
        return False
    # 允许用户粘贴不带 scheme 的短链（例如直接从 App 复制）
    candidate = url.strip()
    lowered = candidate.lower()
    for scheme in ("https://", "http://"):
        if lowered.startswith(scheme):
            lowered = lowered[len(scheme) :]
            break
    for host in SHORT_URL_HOSTS:
        if lowered.startswith(f"{host}/") or lowered == host:
            return True
    return False


def normalize_short_url(url: str) -> str:
    """确保短链带 https:// 前缀，便于传给 aiohttp。"""
    stripped = (url or "").strip()
    if stripped.lower().startswith(("http://", "https://")):
        return stripped
    return f"https://{stripped}"


def parse_url_type(url: str) -> Optional[str]:
    # 短链在调用方（CLI/调度层）统一先解析为真实 URL 后再判断类型；
    # 若仍是短链，返回 'short' 明确提示需要解析，而不是错误地全部落到 'video'。
    if is_short_url(url):
        return "short"

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path

    if _is_live_replay_path(host, path):
        return "live_replay"
    if _is_live_reflow_path(host, path):
        return "live"

    if not _is_douyin_web_host(host):
        return None

    # modal_id 参数表示在任意页面（用户主页、发现页、搜索页等）弹窗查看单个作品，
    # 应优先识别为单作品下载，而非该页面本身的类型。
    qs = parse_qs(parsed.query)
    modal_ids = qs.get("modal_id", [])
    if modal_ids and modal_ids[0].strip():
        return "video"

    if host == "live.douyin.com":
        return "live" if re.fullmatch(r"/\d+/?", path) else None

    if "/video/" in path:
        return "video"
    if "/user/" in path:
        return "user"
    if "/note/" in path or "/gallery/" in path or "/slides/" in path:
        return "gallery"
    if "/collection/" in path or "/mix/" in path:
        return "collection"
    if "/music/" in path:
        return "music"
    if re.fullmatch(r"/(?:follow/|share/)?live/\d+/?", path):
        return "live"
    return None
