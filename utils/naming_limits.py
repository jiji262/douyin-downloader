"""模板与真实路径的长度预算;超限时先缩标题,始终保留作品 ID。"""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

from utils.validators import sanitize_filename

COMPONENT_LIMIT = 255
# 无路径上下文的预览为最长常见附属文件后缀留位。
PREVIEW_STEM_BYTES = COMPONENT_LIMIT - len(".transcript.json")
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _render_names(templates, context):
    def replace(match):
        value = context.get(match.group(1))
        return "" if value is None else str(value)

    names = {key: _PLACEHOLDER_RE.sub(replace, value) for key, value in templates.items()}
    return {
        key: sanitize_filename(value, max_length=len(value) + 1) for key, value in names.items()
    }


def _fit_variable(templates, context, fits, *, key):
    value = str(context[key])
    low, high = 0, len(value)
    best = _render_names(templates, {**context, key: ""})
    while low <= high:
        mid = (low + high) // 2
        candidate = _render_names(templates, {**context, key: value[:mid]})
        if fits(candidate):
            best = candidate
            low = mid + 1
        else:
            high = mid - 1
    return best


def fit_names(
    templates: Mapping[str, str],
    context: Mapping[str, Any],
    fits: Callable[[Dict[str, str]], bool],
    *,
    fallback: Optional[str] = None,
) -> Dict[str, str]:
    """共同裁短各模板中的变量,避免目录先耗光文件名的可用长度。"""
    values = dict(context)
    names = _render_names(templates, values)
    if fallback:
        fallback = sanitize_filename(fallback, max_length=len(fallback) + 1)
        names = {key: fallback if value == "untitled" else value for key, value in names.items()}
    if fits(names):
        return names
    keys = ["title"] + [key for key in values if key not in ("title", "id")]
    for key in keys:
        if not values.get(key):
            continue
        candidate = _render_names(templates, {**values, key: ""})
        if fits(candidate):
            return _fit_variable(templates, values, fits, key=key)
        values[key] = ""
    # 过长的静态模板也不能从尾部裁掉 ID;退回纯 ID 名称。
    minimal = str(context.get("id") or fallback or "untitled")
    names = _render_names({key: "{id}" for key in templates}, {"id": minimal})
    if fits(names):
        return names
    raise ValueError("保存目录过深或作品 ID 过长,请缩短保存目录后重试")


def _pathconf(directory: Path, key: str, default: int) -> int:
    try:
        value = os.pathconf(str(directory), key)
        return value if value > 0 else default
    except (AttributeError, OSError, ValueError):
        return default


@dataclass(frozen=True)
class PathLimits:
    component_limit: int = COMPONENT_LIMIT
    path_limit: int = 4095
    windows: bool = False

    @classmethod
    def for_directory(cls, directory: Path) -> "PathLimits":
        if os.name == "nt":
            # 不依赖用户开启 long paths;MAX_PATH 包含末尾 NUL。
            return cls(path_limit=259, windows=True)
        while not directory.exists() and directory != directory.parent:
            directory = directory.parent
        return cls(
            component_limit=_pathconf(directory, "PC_NAME_MAX", COMPONENT_LIMIT),
            path_limit=_pathconf(directory, "PC_PATH_MAX", 1024) - 1,
        )

    def measure(self, value: str) -> int:
        if self.windows:
            return len(value.encode("utf-16-le")) // 2
        return len(value.encode("utf-8"))

    def fits(self, parent: Path, names: Mapping[str, str], *, suffixes) -> bool:
        folder = names.get("folder")
        if folder and self.measure(folder) > self.component_limit:
            return False
        directory = parent / folder if folder else parent
        for suffix in suffixes:
            leaf = names["file"] + suffix
            if self.measure(leaf) > self.component_limit:
                return False
            if self.measure(str(directory / leaf)) > self.path_limit:
                return False
        return True
