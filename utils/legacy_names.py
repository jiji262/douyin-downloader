"""认出旧版写下的、丢了作品 ID 的文件名。

0.5.0 及更早的版本把整段 ``{date}_{title}_{id}`` 交给 ``sanitize_filename``
（默认 80 个字符，从尾部截断），长标题作品的文件名因此没有作品 ID。磁盘增量判据
按文件名里的 ID 认作品，认不出这些文件，会把仍在盘上的作品再下一遍。

旧版各时期的清洗规则不同（空白、下划线的折叠方式改过），改动的都是字母数字以外
的字符；只比较字母和数字，就能跨时期把截断名对回「发布日期 + 标题 + 作品 ID」。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Optional, Tuple

# 旧版 sanitize_filename 的默认 max_length。
LEGACY_NAME_CAP = 80
# 截断后还会去掉结尾的 . _ - 空格，实际长度略短于上限。
_LEGACY_TRIM_SLACK = 10
# 抖音作品 ID 是 19 位；名字里有这么长的数字串，说明 ID 没被截掉。
_FULL_ID_DIGITS = 19
# 截断名或标题折叠后太短（几乎全是表情或符号）时，能比的只剩发布日期和 ID
# 前几位，同一天的作品 ID 前缀相同，分不清是哪一条，宁可重下。
_MIN_TITLE_CHARS = 8
_DIGIT_RUN_RE = re.compile(r"\d+")
_DATED_NAME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})_(.+)$", re.S)
# 图集图片、实况在截断名后面追加的 _1、_live_1 序号；非贪婪，免得 _live 留在名里。
_ASSET_INDEX_RE = re.compile(r"(.+?)_(?:live_)?\d+$", re.S)


def fold_alnum(text: str) -> str:
    """只留字母和数字（兼容分解、不分大小写），抹平各时期清洗规则的差别。"""
    folded = unicodedata.normalize("NFKD", text).casefold()
    return "".join(ch for ch in folded if ch.isalnum())


def _capped_key(stem: str) -> Optional[Tuple[str, str]]:
    if not LEGACY_NAME_CAP - _LEGACY_TRIM_SLACK <= len(stem) <= LEGACY_NAME_CAP:
        return None
    match = _DATED_NAME_RE.match(stem)
    if match is None:
        return None
    rest = fold_alnum(match.group(2))
    return (match.group(1), rest) if len(rest) >= _MIN_TITLE_CHARS else None


def legacy_name_keys(stem: str) -> List[Tuple[str, str]]:
    """旧版截断名 → [(发布日期, 折叠后的「标题 + 残留 ID」)]；其它名字返回空列表。

    原名和去掉图集序号后的名字都可能落在长度区间里（截断后去掉结尾符号只剩 78 个
    字符的图集名，加上 _1 又回到 80），两个都收；去掉序号只会让前缀变短，不会配错。
    """
    stem = unicodedata.normalize("NFC", stem)
    if any(len(run) >= _FULL_ID_DIGITS for run in _DIGIT_RUN_RE.findall(stem)):
        return []
    indexed = _ASSET_INDEX_RE.match(stem)
    candidates = (stem, indexed.group(1)) if indexed else (stem,)
    keys = (_capped_key(candidate) for candidate in candidates)
    return [key for key in keys if key is not None]


class LegacyNameIndex:
    """按发布日期分组的旧版截断名，给磁盘增量判据兜底。"""

    def __init__(self) -> None:
        self._by_date: Dict[str, List[str]] = {}

    def add(self, stem: str) -> None:
        for publish_date, name in legacy_name_keys(stem):
            self._by_date.setdefault(publish_date, []).append(name)

    def matches(self, publish_date: str, title: str, aweme_id: str) -> bool:
        """同一天发布，且某个截断名正好是「标题 + 作品 ID」开头的一段。"""
        names = self._by_date.get(publish_date)
        folded_title = fold_alnum(title)
        if not names or not aweme_id or len(folded_title) < _MIN_TITLE_CHARS:
            return False
        target = folded_title + aweme_id
        return any(target.startswith(name) for name in names)
