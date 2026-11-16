"""日志状态快照与还原。

共享的 `utils.logger.set_console_log_level` 会把所有已知 logger 的 StreamHandler 级别
一并改掉且无法撤销（新版 pytest 的捕获 handler 也挂在这些 logger 上）。
进程内重复调用 agent 入口或人用 `main()` 时，用这里的快照精确还原。
"""
import logging
from typing import Callable


def snapshot_logging() -> Callable[[], None]:
    """记录 root 与已知 logger 的级别、handler 级别和全局控制台级别，返回还原函数。"""
    from utils import logger as app_logger

    names = sorted(getattr(app_logger, "_KNOWN_LOGGER_NAMES", ()))
    loggers = [logging.getLogger()] + [logging.getLogger(name) for name in names]
    logger_levels = [(item, item.level) for item in loggers]
    handler_levels = [(handler, handler.level) for item in loggers for handler in item.handlers]
    console_level = getattr(app_logger, "_console_log_level", None)

    def restore() -> None:
        for item, level in logger_levels:
            item.setLevel(level)
        for handler, level in handler_levels:
            handler.setLevel(level)
        if console_level is not None:
            app_logger._console_log_level = console_level

    return restore
