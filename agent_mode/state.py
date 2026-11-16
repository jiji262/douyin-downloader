"""agent 状态目录：cookie 文件、历史库路径、权限检查、agent 用的 config。

状态目录由 `DOUYIN_DL_HOME` 指定，默认 `~/.douyin-dl`。agent 模式不读 `config.yml`，
也不读 `config/cookies.json` 或 cwd 下的 `.cookies.json`。这里的函数从不返回、
也从不记录 cookie 的值，对外只暴露键名。
"""

import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_mode.envelope import AgentError, add_warning, shell_quote

HOME_ENV = "DOUYIN_DL_HOME"
DEFAULT_HOME_NAME = ".douyin-dl"
COOKIES_FILENAME = "cookies.json"
HISTORY_DB_FILENAME = "history.db"
DEFAULT_OUTPUT_DIRNAME = "douyin-downloads"
STATE_DIR_MODE = 0o700
COOKIE_FILE_MODE = 0o600
_OTHERS_MASK = stat.S_IRWXG | stat.S_IRWXO
_IS_POSIX = sys.platform != "win32"

# 任一非空即视为已登录
LOGIN_COOKIE_KEYS = ("sessionid", "sessionid_ss")
# doctor / login 只会报告这些键名是否存在，其他键名不外泄
COOKIE_KEY_WHITELIST = (
    "sessionid",
    "sessionid_ss",
    "sid_tt",
    "sid_guard",
    "uid_tt",
    "ttwid",
    "odin_tt",
    "passport_csrf_token",
    "msToken",
    "s_v_web_id",
)
LOGIN_STATUS_NONE = "none"
LOGIN_STATUS_DEVICE_ONLY = "device_only"
LOGIN_STATUS_LOGGED_IN = "logged_in"


def state_dir() -> Path:
    raw = os.environ.get(HOME_ENV, "").strip()
    base = Path(raw).expanduser() if raw else Path.home() / DEFAULT_HOME_NAME
    return Path(os.path.abspath(str(base)))


def cookies_path() -> Path:
    return state_dir() / COOKIES_FILENAME


def history_db_path() -> Path:
    return state_dir() / HISTORY_DB_FILENAME


def ensure_state_dir() -> Path:
    """确保状态目录可用。只有确实要写状态的 verb 才调用。

    只在本函数新建目录时收紧到 0700；用户经 DOUYIN_DL_HOME 指定的已有目录不改权限
    （doctor 会报告过宽的权限）。路径不可用时报 USAGE 而不是 INTERNAL。
    """
    path = state_dir()
    created = not path.exists()
    try:
        path.mkdir(parents=True, exist_ok=True, mode=STATE_DIR_MODE)
        if created and _IS_POSIX:
            os.chmod(str(path), STATE_DIR_MODE)
    except OSError as exc:
        raise AgentError(
            "USAGE",
            "State directory %s is not usable (%s)." % (path, type(exc).__name__),
            action={"kind": "fix_usage", "hint": "Point DOUYIN_DL_HOME at a writable directory."},
        ) from None
    if not path.is_dir():
        raise AgentError(
            "USAGE",
            "State directory %s is not a directory." % path,
            action={"kind": "fix_usage", "hint": "Point DOUYIN_DL_HOME at a directory."},
        )
    return path


def mode_string(path: Path) -> Optional[str]:
    """权限位的八进制字符串（如 "0600"）；路径不存在或在 Windows 上返回 None。"""
    if not _IS_POSIX:
        return None
    try:
        return "%04o" % stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None


def permission_warnings() -> List[str]:
    """状态目录或 cookie 文件对 group/other 开放时给出提示（仅 POSIX）。"""
    if not _IS_POSIX:
        return []
    found = []
    for path, expected in ((state_dir(), STATE_DIR_MODE), (cookies_path(), COOKIE_FILE_MODE)):
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except OSError:
            continue
        if mode & _OTHERS_MASK:
            found.append(
                "%s has mode %04o; it should be %04o (run: chmod %o %s)"
                % (path, mode, expected, expected, shell_quote(str(path)))
            )
    return found


def read_cookies() -> Dict[str, str]:
    """读取状态目录里的 cookie。文件缺失或损坏时返回空 dict（损坏会记一条 warning）。"""
    from utils.cookie_utils import sanitize_cookies

    path = cookies_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        add_warning("cookie file %s is unreadable (%s)" % (path, type(exc).__name__))
        return {}
    if not isinstance(raw, dict):
        add_warning("cookie file %s is not a JSON object" % path)
        return {}
    return {key: value for key, value in sanitize_cookies(raw).items() if value}


def write_cookies(cookies: Dict[str, Any]) -> Path:
    """原子写入 cookie：同目录 0600 临时文件 + os.replace。返回最终路径。"""
    from utils.cookie_utils import sanitize_cookies

    clean = {key: value for key, value in sanitize_cookies(cookies or {}).items() if value}
    directory = ensure_state_dir()
    target = directory / COOKIES_FILENAME
    # mkstemp 创建的文件本身就是 0600
    fd, tmp_name = tempfile.mkstemp(dir=str(directory), prefix=".cookies-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(clean, handle, ensure_ascii=False, indent=2)
        if _IS_POSIX:
            os.chmod(tmp_name, COOKIE_FILE_MODE)
        os.replace(tmp_name, str(target))
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise
    return target


def keys_present(cookies: Dict[str, str]) -> List[str]:
    """白名单内、且值非空的键名。不含值。"""
    return [key for key in COOKIE_KEY_WHITELIST if cookies.get(key)]


def login_status(cookies: Dict[str, str]) -> str:
    if not cookies:
        return LOGIN_STATUS_NONE
    if any(cookies.get(key) for key in LOGIN_COOKIE_KEYS):
        return LOGIN_STATUS_LOGGED_IN
    return LOGIN_STATUS_DEVICE_ONLY


def default_output_dir() -> Path:
    return Path.cwd() / DEFAULT_OUTPUT_DIRNAME


def resolve_path(raw: str) -> Path:
    """用户给的路径一律按调用方 cwd 解析成绝对路径。"""
    return Path(os.path.abspath(os.path.expanduser(str(raw))))


def build_config(*, download_path: Optional[Path] = None) -> Any:
    """agent 用的 ConfigLoader：只有默认值，不读 config.yml。

    - `database_path` 指向 `<state>/history.db`（默认值是相对路径，会落到 cwd）；
    - `path` 是绝对路径，默认 `<cwd>/douyin-downloads`；
    - 去掉环境变量带进来的 cookie，关掉 auto_cookie；cookie 一律来自 `read_cookies()`；
    - `DOUYIN_PROXY`、`DOUYIN_THREAD` 仍然生效。
    本函数不创建任何文件或目录；要打开历史库的调用方先调 `ensure_state_dir()`。
    """
    from config import ConfigLoader

    config = ConfigLoader(None)
    for key in ("cookie", "cookies"):
        config.config.pop(key, None)
    target = download_path if download_path is not None else default_output_dir()
    config.update(
        path=str(resolve_path(str(target))),
        database_path=str(history_db_path()),
        auto_cookie=False,
    )
    return config
