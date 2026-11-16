"""版本号来源：已安装的包元数据优先，其次仓库根的 pyproject.toml。"""

import re
from pathlib import Path

DIST_NAME = "douyin-dl"
UNKNOWN_VERSION = "0+unknown"
_PYPROJECT_PATH = Path(__file__).resolve().parent.parent / "pyproject.toml"
_PROJECT_TABLE_RE = re.compile(r"^\[project\]\s*$(.*?)(?=^\[|\Z)", re.MULTILINE | re.DOTALL)
_VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)


def _from_metadata() -> str:
    try:
        from importlib import metadata

        return str(metadata.version(DIST_NAME) or "")
    except Exception:
        return ""


def _from_pyproject(path: Path = _PYPROJECT_PATH) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    table = _PROJECT_TABLE_RE.search(text)
    match = _VERSION_RE.search(table.group(1)) if table else None
    return match.group(1) if match else ""


def get_version() -> str:
    return _from_metadata() or _from_pyproject() or UNKNOWN_VERSION
