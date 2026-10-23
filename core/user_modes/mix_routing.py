"""合集目录归属与作品模式候选过滤，不改变作品元数据。"""

from typing import Any, Dict, List, Optional

from core.metadata import derive_mix_collection_dir


def _mode_selected(config: Any, mode: str) -> bool:
    # 判据必须与 UserDownloader._configured_modes() 一致：那边 strip 过的模式名照样会跑，
    # 这边不 strip 就会把「post 同选」判成未选，合集展开按只勾合集截断，成员漏进 post 目录。
    modes = config.get("mode", [])
    if isinstance(modes, str):
        modes = [modes]
    if not isinstance(modes, (list, tuple, set)):
        return False
    return any(str(entry).strip() == mode for entry in modes)


def mix_selected(config: Any) -> bool:
    return _mode_selected(config, "mix")


def post_selected(config: Any) -> bool:
    return _mode_selected(config, "post")


def collection_directory(item: Dict[str, Any]) -> Optional[str]:
    info = item.get("mix_info") or item.get("series_info") or item
    if not isinstance(info, dict):
        return None
    mix_id = info.get("mix_id") or info.get("mixId") or info.get("series_id")
    if not mix_id:
        return None
    detail = {**info, "mix_name": info.get("mix_name") or info.get("series_name")}
    return derive_mix_collection_dir(detail, mix_id)


def collection_directories(downloader: Any) -> Dict[str, str]:
    strategy = getattr(downloader, "_mode_strategy_cache", {}).get("mix")
    return getattr(strategy, "collection_dirs", {})


def non_mix_posts(items: List[Dict[str, Any]], downloader: Any) -> List[Dict[str, Any]]:
    if not mix_selected(downloader.config):
        return items
    # 只认本轮合集扫描得到的归属表：作品自带的 mix_info 是可选字段，
    # 它指向的合集若不在扫描结果里，排除后该作品既进不了 post 也进不了 mix。
    directories = collection_directories(downloader)
    return [item for item in items if str(item.get("aweme_id") or "") not in directories]
