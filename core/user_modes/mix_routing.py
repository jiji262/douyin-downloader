"""合集目录归属与作品模式候选过滤，不改变作品元数据。"""

from typing import Any, Dict, List, Optional

from core.metadata import derive_mix_collection_dir


def mix_selected(config: Any) -> bool:
    modes = config.get("mode", [])
    if isinstance(modes, str):
        return modes == "mix"
    return isinstance(modes, (list, tuple, set)) and "mix" in modes


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
    directories = collection_directories(downloader)
    return [
        item
        for item in items
        if str(item.get("aweme_id") or "") not in directories and collection_directory(item) is None
    ]
