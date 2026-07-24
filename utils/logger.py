import logging
import sys
from contextvars import ContextVar
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, urlsplit, urlunsplit

_APP_LOGGER_PREFIX = "dy-downloader"
_KNOWN_LOGGER_NAMES = set()
_console_log_level = logging.ERROR
_log_context = ContextVar("douyin_log_context", default="-")


class _LogContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _log_context.get()
        return True


def setup_logger(
    name: str = "dy-downloader",
    level: int = logging.INFO,
    log_file: str = None,
    console_level: Optional[int] = None,
) -> logging.Logger:
    effective_console_level = (
        _console_log_level if console_level is None else console_level
    )
    logger = logging.getLogger(name)
    logger.setLevel(min(level, effective_console_level))
    logger.propagate = False
    _KNOWN_LOGGER_NAMES.add(name)

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - "
        "[trace=%(trace_id)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(effective_console_level)
    console_handler.addFilter(_LogContextFilter())
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if log_file:
        log_path = Path(log_file)
        if log_path.parent != Path("."):
            log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.addFilter(_LogContextFilter())
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def set_console_log_level(level: int) -> None:
    global _console_log_level
    _console_log_level = level
    for name in _KNOWN_LOGGER_NAMES:
        logger = logging.getLogger(name)
        if level < logger.level:
            logger.setLevel(level)
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(
                handler, logging.FileHandler
            ):
                handler.setLevel(level)


def set_log_context(trace_id: str):
    return _log_context.set(str(trace_id or "-"))


def reset_log_context(token) -> None:
    _log_context.reset(token)


def safe_log_url(value: object) -> str:
    """Keep URL routing context while removing credentials and query values."""

    text = str(value or "").strip()
    try:
        parsed = urlsplit(text)
        netloc = parsed.netloc.rsplit("@", 1)[-1]
        clean = urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
        query_keys = sorted({key for key, _value in parse_qsl(parsed.query, keep_blank_values=True)})
    except (TypeError, ValueError):
        return text.split("?", 1)[0].split("#", 1)[0]
    if not query_keys:
        return clean
    return f"{clean}?[query_keys={','.join(query_keys)}]"
