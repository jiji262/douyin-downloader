"""agent 输出契约：错误类型、错误码与退出码的映射、envelope 的构造与输出。"""

import importlib.util
import json
import os
import re
import shlex
import sys
from typing import Any, Dict, List, Optional

from agent_mode.version import get_version

SCHEMA_VERSION = 1
EXIT_OK = 0
AGENT_COMMAND = "douyin-dl agent"
LOGIN_COMMAND = AGENT_COMMAND + " login"
DESKTOP_HINT = (
    "Douyin gates this endpoint behind ArgusSecurityPlugin page signing; the open-source "
    "CLI cannot call it. Tell the user plainly, and mention the Douzy desktop app, which "
    "can. Do not try to work around the gate."
)
USAGE_HINT = "Run `" + AGENT_COMMAND + " --help` for the verbs and their arguments."

# code -> (退出码, retryable 默认值)
ERROR_CODES = {
    "INTERNAL": (1, False),
    "USAGE": (2, False),
    "UNSUPPORTED": (2, False),
    "LOGIN_REQUIRED": (3, False),
    "ARGUS_GATED": (4, False),
    "UPSTREAM_EMPTY": (5, True),
    "PARTIAL": (6, False),
    "DEPENDENCY_MISSING": (7, False),
    "INTERRUPTED": (130, False),
}
ACTION_KINDS = ("run_command", "install", "use_desktop", "retry_later", "fix_usage")
# 没有显式给 action 时按错误码补上的默认 action
DEFAULT_ACTIONS = {
    "USAGE": {"kind": "fix_usage", "hint": USAGE_HINT},
    "LOGIN_REQUIRED": {"kind": "run_command", "command": LOGIN_COMMAND},
    "ARGUS_GATED": {"kind": "use_desktop", "hint": DESKTOP_HINT},
}

# 不可信文本的截断长度
DESC_MAX_CHARS = 200
NAME_MAX_CHARS = 64
ERROR_MESSAGE_MAX_CHARS = 300
_ELLIPSIS = "…"
# JSON 允许它们裸出现，但很多按行读取的消费者会把它们当换行
_UNICODE_LINE_SEPARATORS = ("\u2028", "\u2029")

_warnings: List[str] = []


class AgentError(Exception):
    """verb 处理函数的失败出口；`data` 可携带部分结果或分类信息。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: Optional[bool] = None,
        action: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
    ):
        if code not in ERROR_CODES:
            raise ValueError("unknown agent error code: %s" % code)
        if action is not None and action.get("kind") not in ACTION_KINDS:
            raise ValueError("unknown action kind: %s" % action.get("kind"))
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = ERROR_CODES[code][1] if retryable is None else bool(retryable)
        self.action = action if action is not None else DEFAULT_ACTIONS.get(code)
        self.data = data

    @property
    def exit_code(self) -> int:
        return ERROR_CODES[self.code][0]

    def to_dict(self) -> Dict[str, Any]:
        error: Dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.action:
            error["action"] = dict(self.action)
        return error


_ON_WINDOWS = os.name == "nt"
# Windows：只含这些字符的参数原样输出（\w 含中文等 Unicode 字母）。不含反斜杠：
# Git Bash 会吃掉引号外的反斜杠，带反斜杠的路径一律加双引号
_WINDOWS_BARE_ARG = re.compile(r"[\w.:/+,=@-]+")
# 放进双引号后在 cmd、PowerShell、Git Bash 里都只是字面量的字符。% ! $ ` 和各种引号
# （PowerShell 把智能引号也当引号）即便在双引号里也会被解释，所以不在其中
_WINDOWS_QUOTABLE_ARG = re.compile(r"[\w .:\\/+,=@~#&;'()\[\]{}^-]+")
FALLBACK_PYTHON = "python"


class UnquotableArgument(ValueError):
    """参数里有 Windows shell 在双引号里也会解释的字符，没法安全地放进命令。"""


def shell_quote(arg: str) -> str:
    """给人复制执行的命令参数加引号，防止路径里的引号、`$(...)` 被 shell 解释。

    `subprocess.list2cmdline` 只是进程参数编码，不是 shell 转义，所以 Windows 上不用它。
    """
    if not _ON_WINDOWS:
        return shlex.quote(arg)
    if _WINDOWS_BARE_ARG.fullmatch(arg):
        return arg
    if _WINDOWS_QUOTABLE_ARG.fullmatch(arg):
        return '"%s"' % arg
    raise UnquotableArgument("cannot quote %r safely for a Windows shell" % arg)


def _interpreter() -> str:
    """当前解释器的路径（已加引号）；没法安全放进命令时退回通用的 python。"""
    try:
        return shell_quote(sys.executable or FALLBACK_PYTHON)
    except UnquotableArgument:
        return FALLBACK_PYTHON


def python_command(*args: str) -> str:
    """用当前解释器的精确路径拼一条可直接执行的命令。"""
    return " ".join([_interpreter()] + [shell_quote(a) for a in args])


def _pip_available() -> bool:
    return importlib.util.find_spec("pip") is not None


def install_action(*pip_args: str, hint: str = "") -> Dict[str, Any]:
    """DEPENDENCY_MISSING 用的 action：按当前解释器的精确路径拼出能直接执行的安装命令。"""
    exe = _interpreter()
    pip_args = tuple(shell_quote(a) for a in pip_args)
    if _pip_available():
        prefix = [exe, "-m", "pip", "install"]
    else:
        # uv 创建的环境（含 `uv tool install`）默认不带 pip，改用 uv 直接装进该解释器
        prefix = ["uv", "pip", "install", "--python", exe]
    action: Dict[str, Any] = {"kind": "install", "command": " ".join(prefix + list(pip_args))}
    if hint:
        action["hint"] = hint
    return action


def retry_action(retry_after_s: int, hint: str = "") -> Dict[str, Any]:
    action: Dict[str, Any] = {"kind": "retry_later", "retry_after_s": int(retry_after_s)}
    if hint:
        action["hint"] = hint
    return action


def clean_text(value: Any, limit: int = DESC_MAX_CHARS) -> str:
    """平台返回的文本：控制字符换成空格、压缩空白、按长度截断。"""
    text = "" if value is None else str(value)
    text = "".join(ch if ch.isprintable() else " " for ch in text)
    text = " ".join(text.split())
    if len(text) > limit:
        return text[:limit] + _ELLIPSIS
    return text


def reset_warnings() -> None:
    del _warnings[:]


def add_warning(message: str) -> None:
    """处理函数或其下层随时可调；入口会把它们放进 envelope 的 warnings。"""
    if message and message not in _warnings:
        _warnings.append(str(message))


def collect_warnings() -> List[str]:
    return list(_warnings)


def build_envelope(
    verb: Optional[str],
    *,
    data: Optional[Dict[str, Any]] = None,
    error: Optional[AgentError] = None,
    warnings: Optional[List[str]] = None,
) -> Dict[str, Any]:
    if error is not None and data is None:
        data = error.data
    return {
        "schema": SCHEMA_VERSION,
        "ok": error is None,
        "verb": verb,
        "version": get_version(),
        "data": data if data is not None else {},
        "error": error.to_dict() if error is not None else None,
        "warnings": list(warnings or []),
    }


def exit_code_for(envelope: Dict[str, Any]) -> int:
    error = envelope.get("error")
    if not error:
        return EXIT_OK
    return ERROR_CODES[error["code"]][0]


def emit(envelope: Dict[str, Any], stream: Any) -> None:
    """往 stream 写恰好一行 JSON。优先按 UTF-8 写字节，避开终端编码差异。"""
    line = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), default=str)
    for separator in _UNICODE_LINE_SEPARATORS:
        line = line.replace(separator, "\\u%04x" % ord(separator))
    line += "\n"
    if stream is None:
        # 进程启动时 stdout 就已关闭：没有地方可写
        return
    try:
        _write_line(line, stream)
    except (OSError, ValueError):
        # 读端已关闭（BrokenPipe）或 stream 已关闭：退出码仍然有效，不再抛 traceback
        pass


def _write_line(line: str, stream: Any) -> None:
    buffer = getattr(stream, "buffer", None)
    if buffer is not None:
        stream.flush()
        buffer.write(line.encode("utf-8", errors="backslashreplace"))
        buffer.flush()
        return
    stream.write(line)
    stream.flush()
