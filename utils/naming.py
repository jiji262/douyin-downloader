"""文件/目录命名模板渲染。

用户可在设置里自定义 `filename_template` 与 `folder_template`，此处把模板中
``{var}`` 形式的占位符替换成上下文变量，未知变量会被保留成空字符串（而非抛错），
这样即使用户输入轻微笔误也不会导致下载失败。渲染结果最终仍会走
``utils.validators.sanitize_filename``，因此模板里出现的路径分隔符、非法字符会
被统一清洗——模板语言本身不需要做安全校验。

仅允许的变量（详见 ``ALLOWED_VARIABLES``）：
  - ``id``: 作品 ID（视频/图集为 ``aweme_id``，音乐为 ``music_<music_id>``，
    直播为 ``room_id``）
  - ``title``: 作品标题或描述，空时为 ``no_title``
  - ``author``: 作者昵称
  - ``author_id``: 作者 sec_uid（便于同名区分，缺失为空）
  - ``date``: 发布日期 ``YYYY-MM-DD``（缺失时为当前日期）
  - ``year`` / ``month`` / ``day``: ``date`` 的年月日分量
  - ``time``: 发布时间 ``HHMM``（仅当上下文提供时有值）
  - ``timestamp``: Unix 时间戳（秒，整型字符串；缺失为空）
  - ``type``: ``video`` / ``gallery`` / ``music`` / ``live``
  - ``mode``: 下载模式 ``post`` / ``like`` / ``mix`` / ``music`` / ``live`` …
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Mapping, Optional

from utils.validators import sanitize_filename

# 允许用户在模板中使用的变量白名单（必须与文档、桌面 UI 帮助面板保持一致）。
ALLOWED_VARIABLES = (
    "id",
    "title",
    "author",
    "author_id",
    "date",
    "year",
    "month",
    "day",
    "time",
    "timestamp",
    "type",
    "mode",
)

# 默认模板：与历史行为保持一致（`{date}_{title}_{id}`）。作者已经在上级目录，
# 所以这里不重复放作者名。
DEFAULT_FILE_TEMPLATE = "{date}_{title}_{id}"
DEFAULT_FOLDER_TEMPLATE = "{date}_{title}_{id}"

# 模板长度上限。既防用户贴进整段长文案，也给前端做一致校验。
MAX_TEMPLATE_LENGTH = 200

# 匹配 ``{var}`` 形式。不支持格式化说明符（:fmt）以降低心智负担。
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class TemplateValidationError(ValueError):
    """模板语法或变量不合法。"""


def validate_template(template: str, *, field_name: str = "template") -> None:
    """校验模板可用（用于 API 层早退）。

    规则：
      - 长度 ≤ ``MAX_TEMPLATE_LENGTH``
      - 不得包含裸 ``/`` 或 ``\\``（这两种字符会被视为路径分隔符而不再当文件名
        的一部分，极易造成越级写入或层级错乱；清洗函数虽然会替换，但模板层
        显式拒绝更清晰）
      - 不得只包含空白或空串
      - 至少引用一个允许变量（防止用户写成纯静态常量导致不同作品互相覆盖）
      - 引用的变量必须在 ``ALLOWED_VARIABLES`` 白名单内
      - 必须引用 ``{id}`` —— 保证跨作品唯一性（否则同一作者同一天的两条作品
        会因为 stem 相同而彼此覆盖）
    """
    if not isinstance(template, str):
        raise TemplateValidationError(f"{field_name} must be a string")

    stripped = template.strip()
    if not stripped:
        raise TemplateValidationError(f"{field_name} must not be empty")

    if len(template) > MAX_TEMPLATE_LENGTH:
        raise TemplateValidationError(
            f"{field_name} must be <= {MAX_TEMPLATE_LENGTH} characters"
        )

    if "/" in template or "\\" in template:
        raise TemplateValidationError(
            f"{field_name} must not contain path separators ('/' or '\\\\')"
        )

    variables = _PLACEHOLDER_RE.findall(template)
    if not variables:
        raise TemplateValidationError(
            f"{field_name} must reference at least one variable like {{id}}"
        )

    unknown = [v for v in variables if v not in ALLOWED_VARIABLES]
    if unknown:
        raise TemplateValidationError(
            f"{field_name} uses unknown variable(s): "
            + ", ".join(sorted(set(unknown)))
            + f"; allowed: {', '.join(ALLOWED_VARIABLES)}"
        )

    if "id" not in variables:
        raise TemplateValidationError(
            f"{field_name} must reference {{id}} to guarantee uniqueness"
        )


def render_template(
    template: str,
    context: Mapping[str, Any],
    *,
    fallback: Optional[str] = None,
) -> str:
    """根据 ``context`` 渲染模板并清洗最终文件名。

    未知变量或 context 缺失的键会被替换成空字符串；清洗之后若结果为空/仅
    符号（会被 sanitize_filename 吞掉并回退为 ``untitled``），调用方可通过
    ``fallback`` 进一步兜底。
    """

    def replace(match: "re.Match[str]") -> str:
        name = match.group(1)
        value = context.get(name)
        return "" if value is None else str(value)

    rendered = _PLACEHOLDER_RE.sub(replace, template)
    cleaned = sanitize_filename(rendered)
    if cleaned == "untitled" and fallback:
        return sanitize_filename(fallback)
    return cleaned


def _split_date(date_str: str) -> Dict[str, str]:
    """把 ``YYYY-MM-DD`` 拆成 ``{year, month, day}`` 三个字符串。"""
    if not date_str:
        return {"year": "", "month": "", "day": ""}
    parts = date_str.split("-")
    if len(parts) != 3:
        return {"year": "", "month": "", "day": ""}
    return {"year": parts[0], "month": parts[1], "day": parts[2]}


def build_aweme_context(
    *,
    aweme_id: str,
    title: str,
    author_name: str,
    author_sec_uid: Optional[str],
    publish_date: str,
    publish_ts: Optional[int],
    media_type: str,
    mode: Optional[str] = None,
) -> Dict[str, str]:
    """为普通视频/图集下载构造模板上下文。"""
    ctx: Dict[str, str] = {
        "id": str(aweme_id or ""),
        "title": title or "no_title",
        "author": author_name or "",
        "author_id": author_sec_uid or "",
        "date": publish_date or "",
        "time": "",
        "timestamp": str(publish_ts) if publish_ts else "",
        "type": media_type or "",
        "mode": mode or "",
    }
    ctx.update(_split_date(publish_date))
    # HHMM 对普通作品无意义，但仍基于 publish_ts 填一份，避免模板使用 {time}
    # 时出现空串。
    if publish_ts:
        try:
            ctx["time"] = datetime.fromtimestamp(publish_ts).strftime("%H%M")
        except (OSError, OverflowError, ValueError):
            ctx["time"] = ""
    return ctx


def build_music_context(
    *,
    music_id: str,
    title: str,
    author_name: str,
    publish_date: str,
    mode: str = "music",
) -> Dict[str, str]:
    """音乐下载专用上下文（music_id 会加上 ``music_`` 前缀用作 ``id``）。"""
    ctx: Dict[str, str] = {
        "id": f"music_{music_id}" if music_id else "",
        "title": title or "no_title",
        "author": author_name or "",
        "author_id": "",
        "date": publish_date or "",
        "time": "",
        "timestamp": "",
        "type": "music",
        "mode": mode,
    }
    ctx.update(_split_date(publish_date))
    return ctx


def build_live_context(
    *,
    room_id: str,
    title: str,
    author_name: str,
    started_at: datetime,
    mode: str = "live",
) -> Dict[str, str]:
    """直播录制上下文。

    ``date`` 特意保留为 ``YYYY-MM-DD_HHMM``（保留历史行为：同一天可能录多次
    直播，需要在文件名层面区分）。``year``/``month``/``day`` 仍按自然日拆分，
    方便按月/按日分文件夹。``time`` 单独提供 ``HHMM`` 方便用户在模板里改放到
    其他位置。
    """
    iso_date = started_at.strftime("%Y-%m-%d")
    date_with_time = started_at.strftime("%Y-%m-%d_%H%M")
    ctx: Dict[str, str] = {
        "id": str(room_id or ""),
        "title": title or "no_title",
        "author": author_name or "",
        "author_id": "",
        "date": date_with_time,
        "time": started_at.strftime("%H%M"),
        "timestamp": str(int(started_at.timestamp())),
        "type": "live",
        "mode": mode,
    }
    # 仍按自然日拆分 year/month/day，保证模板 {year}/{month}/{day} 语义一致。
    ctx.update(_split_date(iso_date))
    return ctx
