"""agent 模式入口：argparse、stdout 重定向、信号处理、调度 async 处理函数。

verb 模块接口（A2 / B 阶段照此实现，不需要改本文件）
====================================================

1. 模块名固定在 `VERB_MODULES` 里。每个模块导出::

       def register(subparsers) -> None:
           parser = subparsers.add_parser("hot", help="...")
           parser.add_argument("--max", type=bounded_int(1, 100), default=50)
           parser.set_defaults(handler=run_hot)

   `register` 负责定义自己的全部参数，并用 `set_defaults(handler=...)` 挂处理函数。
   `--verbose` 由入口统一加到每个 verb 上，模块不要自己加。
   整数范围用 `agent_mode.entry.bounded_int(lo, hi)`，越界时入口会输出 USAGE。

2. 处理函数::

       async def run_hot(args: argparse.Namespace) -> Dict[str, Any]

   - 成功：返回 data（dict，只放投影后的字段），入口包成 ok=true 的 envelope，退出 0。
   - 失败：抛 `agent_mode.envelope.AgentError(code, message, *, retryable=None,
     action=None, data=None)`。`retryable` / `action` 不给时按错误码取默认值
     （LOGIN_REQUIRED -> run_command `douyin-dl agent login`；ARGUS_GATED -> use_desktop；
     USAGE -> fix_usage）。
   - PARTIAL、INTERRUPTED 以及带分类信息的失败，都通过 `AgentError(..., data={...})`
     携带部分结果。
   - 其他异常一律变成 INTERNAL，message 只有异常类型和简短信息；traceback 只在
     `--verbose` 时写 stderr。`core.LoginRequiredError` 不会自动转换，处理函数要自己
     接住并改抛 `AgentError("LOGIN_REQUIRED", ...)`。

3. warnings：任何地方调 `agent_mode.envelope.add_warning("...")`（英文、去重），
   入口每次运行前清空，结束时放进 envelope 的 `warnings`。成功和失败都会带上。

4. 输出：处理函数运行期间 `sys.stdout` 已重定向到 stderr，`print` 不会污染 stdout。
   不要保存或直接写 `sys.__stdout__`，不要 import `cli.progress_display`。
   平台文本用 `agent_mode.envelope.clean_text(value, DESC_MAX_CHARS | NAME_MAX_CHARS)`。

5. 重依赖（aiohttp / core.* / playwright / whisper 等）在处理函数内部 import，
   模块顶层只 import 标准库和 `agent_mode.*`，保证 `--help` 和 `doctor` 够快。

6. 取消：第一次 SIGINT / SIGTERM（POSIX）会 cancel 处理函数所在的 task，且只 cancel
   这一次；之后的信号忽略到 envelope 写完为止。处理函数可以 `except asyncio.CancelledError:`
   做清理（不会被第二个信号打断），再抛 `AgentError("INTERRUPTED", ..., data=...)` 带上
   已完成的部分；不处理的话入口输出不带 data 的 INTERRUPTED（退出 130）。阻塞工作别放
   默认线程池：`asyncio.run` 收尾会等它跑完，拖住 envelope。Windows 退回
   KeyboardInterrupt -> INTERRUPTED，此时处理函数自己抛的 AgentError 不保证能送达。

7. 状态与路径：cookie 用 `agent_mode.state.read_cookies()`，config 用
   `agent_mode.state.build_config(download_path=...)`，路径用 `state.resolve_path()`；
   除状态目录和显式输出目录外不得创建文件，绝不能用无参的 `auth.CookieManager()`
   （它会往 cwd 写 `.cookies.json`）。
"""

import argparse
import asyncio
import contextlib
import importlib
import logging
import signal
import sys
import traceback
from typing import Any, Callable, Dict, Iterator, List, Optional

from agent_mode.capabilities import VERB_CAPABILITIES
from agent_mode.envelope import (
    AGENT_COMMAND,
    ERROR_MESSAGE_MAX_CHARS,
    AgentError,
    build_envelope,
    clean_text,
    collect_warnings,
    emit,
    exit_code_for,
    reset_warnings,
)
from agent_mode.logging_guard import snapshot_logging

VERB_MODULES = (
    "agent_mode.auth_cmds",
    "agent_mode.inspect_cmd",
    "agent_mode.discovery_cmds",
    "agent_mode.login_cmd",
    "agent_mode.media_cmds",
)
VERBOSE_FLAG = "--verbose"
_VERBOSE_HELP = "write INFO logs and tracebacks to stderr"
_INTERRUPT_SIGNALS = ("SIGINT", "SIGTERM")
_REDACTED = "***"
# 短于这个长度的 cookie 值不做替换，避免把普通字符误伤成 ***
_MIN_SECRET_CHARS = 8


class _HelpRequested(Exception):
    def __init__(self, text: str):
        super().__init__("help requested")
        self.text = text


class AgentArgumentParser(argparse.ArgumentParser):
    """argparse 报错和 --help 都不直接退出，交给入口统一输出 envelope。"""

    def __init__(self, *args: Any, **kwargs: Any):
        # 不接受 --ma / --verb 这类缩写，避免参数被静默误读；子解析器同样走这里
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)
        # 3.14 起 help 可能带 ANSI 颜色，envelope 里不要
        self.color = False

    def error(self, message: str) -> None:
        raise AgentError("USAGE", message)

    def print_help(self, file: Any = None) -> None:
        raise _HelpRequested(self.format_help())

    def exit(self, status: int = 0, message: Optional[str] = None) -> None:
        raise AgentError("USAGE", (message or "argument parsing stopped").strip())


def bounded_int(minimum: int, maximum: int) -> Callable[[str], int]:
    """argparse 的 type：闭区间整数，越界时报 USAGE。"""

    def parse(raw: str) -> int:
        try:
            value = int(raw)
        except ValueError:
            value = minimum - 1
        if not minimum <= value <= maximum:
            raise argparse.ArgumentTypeError(
                "must be an integer between %d and %d" % (minimum, maximum)
            )
        return value

    return parse


def build_parser() -> AgentArgumentParser:
    parser = AgentArgumentParser(
        prog=AGENT_COMMAND,
        description="Machine-readable Douyin commands. Prints exactly one JSON line on stdout.",
    )
    parser.add_argument(VERBOSE_FLAG, action="store_true", help=_VERBOSE_HELP)
    subparsers = parser.add_subparsers(dest="verb", metavar="<verb>")
    subparsers.required = True
    for module_name in VERB_MODULES:
        importlib.import_module(module_name).register(subparsers)
    for verb_parser in subparsers.choices.values():
        # 让 --verbose 写在 verb 前后都行
        verb_parser.add_argument(
            VERBOSE_FLAG, action="store_true", default=argparse.SUPPRESS, help=_VERBOSE_HELP
        )
    # 给测试和文档校验用：verb 名 -> 它的子解析器
    parser.verb_parsers = dict(subparsers.choices)
    return parser


def _guess_verb(argv: List[str]) -> Optional[str]:
    """argparse 失败时尽力给出 verb；猜不出就是 None。"""
    for token in argv:
        if not token.startswith("-"):
            return token if token in VERB_CAPABILITIES else None
    return None


@contextlib.contextmanager
def _logging_scope(verbose: bool) -> Iterator[None]:
    """日志只写 stderr：默认 ERROR，--verbose 时 INFO。退出时精确还原，方便进程内重复调用。"""
    from utils import logger as app_logger

    level = logging.INFO if verbose else logging.ERROR
    restore = snapshot_logging()
    root = logging.getLogger()
    handler = logging.StreamHandler(sys.stderr)
    root.addHandler(handler)
    root.setLevel(level)
    app_logger.set_console_log_level(level)
    try:
        yield
    finally:
        root.removeHandler(handler)
        restore()


def _redact(text: str) -> str:
    """异常信息里万一带了 cookie 值，替换掉再输出。"""
    try:
        from agent_mode.state import read_cookies

        secrets = [v for v in read_cookies().values() if len(v) >= _MIN_SECRET_CHARS]
    except Exception:
        secrets = []
    for secret in secrets:
        text = text.replace(secret, _REDACTED)
    return text


def _internal_error(exc: BaseException, verbose: bool) -> AgentError:
    if verbose:
        # traceback 里也可能带 cookie 值，先脱敏再写 stderr
        sys.stderr.write(_redact(traceback.format_exc()))
    detail = clean_text(_redact(str(exc)), ERROR_MESSAGE_MAX_CHARS)
    name = type(exc).__name__
    return AgentError("INTERNAL", "%s: %s" % (name, detail) if detail else name)


class _Interrupts:
    # 第一次信号 cancel verb 的 task，之后的忽略到 restore() 为止。不用
    # `loop.add_signal_handler`：循环一关信号就回到默认行为，那时 envelope 还没写出去。

    def __init__(self) -> None:
        self._fired = False
        self.cancel: Optional[Callable[[], Any]] = None  # 没有在跑的 task 时是 None
        self._previous: Dict[int, Any] = {}

    def install(self) -> None:
        self._fired = False
        # Windows 不装，保持默认的 KeyboardInterrupt 路径；非主线程装不了，保持原样
        for name in () if sys.platform == "win32" else _INTERRUPT_SIGNALS:
            signum = getattr(signal, name)
            with contextlib.suppress(ValueError):
                self._previous[signum] = signal.signal(signum, self._on_signal)

    def restore(self) -> None:
        for signum, previous in self._previous.items():
            signal.signal(signum, signal.SIG_DFL if previous is None else previous)
        self._previous.clear()

    def _on_signal(self, signum: int, frame: Any) -> None:
        first, self._fired = not self._fired, True
        if first and self.cancel is not None:
            self.cancel()

    def watch(self, loop: Any, task: Any) -> None:
        # 信号到来时循环可能卡在 select 里，要用线程安全的方式唤醒它
        self.cancel = lambda: loop.call_soon_threadsafe(task.cancel)
        if self._fired:
            task.cancel()  # 信号早于 task 出现（比如还在解析参数时）


_interrupts = _Interrupts()


async def _supervise(handler: Callable[[Any], Any], args: argparse.Namespace) -> Any:
    task = asyncio.ensure_future(handler(args))
    _interrupts.watch(asyncio.get_event_loop(), task)
    try:
        return await task
    except asyncio.CancelledError:
        raise AgentError("INTERRUPTED", "Interrupted before the verb finished.") from None
    finally:
        _interrupts.cancel = None


def _dispatch(args: argparse.Namespace) -> Dict[str, Any]:
    handler = getattr(args, "handler", None)
    if handler is None:
        raise AgentError("INTERNAL", "verb %s has no handler" % args.verb)
    data = asyncio.run(_supervise(handler, args))
    if not isinstance(data, dict):
        raise AgentError("INTERNAL", "verb %s returned a non-object result" % args.verb)
    return data


def _execute(argv: List[str]) -> Dict[str, Any]:
    verb = _guess_verb(argv)
    verbose = VERBOSE_FLAG in argv
    data = None
    error = None
    try:
        args = build_parser().parse_args(argv)
        verb = args.verb
        verbose = bool(getattr(args, "verbose", verbose))
        with _logging_scope(verbose):
            data = _dispatch(args)
    except _HelpRequested as exc:
        sys.stderr.write(exc.text)
        data = {"help": exc.text}
    except AgentError as exc:
        error = exc
    except KeyboardInterrupt:
        error = AgentError("INTERRUPTED", "Interrupted before the verb finished.")
    except Exception as exc:  # noqa: BLE001 — 契约要求任何异常都变成 envelope
        error = _internal_error(exc, verbose)
    return build_envelope(verb, data=data, error=error, warnings=collect_warnings())


def main(argv: Optional[List[str]] = None) -> int:
    """运行一个 verb，往真实 stdout 写恰好一行 envelope，返回退出码。"""
    argv = list(sys.argv[1:] if argv is None else argv)
    real_stdout = sys.stdout
    reset_warnings()
    _interrupts.install()
    try:
        with contextlib.redirect_stdout(sys.stderr):
            envelope = _execute(argv)
        emit(envelope, real_stdout)
    finally:  # 进程内会反复调用 main()（测试），信号处理还原成调用前的样子
        _interrupts.restore()
    return exit_code_for(envelope)
