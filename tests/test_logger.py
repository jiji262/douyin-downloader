import logging
import uuid

from utils.logger import safe_log_url, set_console_log_level, set_log_context, setup_logger


def test_console_level_applies_to_loggers_created_after_setting(capsys):
    """A sidecar flag parsed before lazy server imports must still affect them."""

    set_console_log_level(logging.INFO)
    try:
        logger = setup_logger(f"late-logger-{uuid.uuid4().hex}")
        logger.info("late logger is visible")
        assert "late logger is visible" in capsys.readouterr().err
    finally:
        set_console_log_level(logging.ERROR)


def test_log_context_is_rendered_for_cross_module_job_tracing(capsys):
    set_console_log_level(logging.INFO)
    set_log_context("job:abc123")
    try:
        logger = setup_logger(f"context-logger-{uuid.uuid4().hex}")
        logger.info("request completed")
        output = capsys.readouterr().err
        assert "[trace=job:abc123]" in output
        assert "request completed" in output
    finally:
        set_log_context("-")
        set_console_log_level(logging.ERROR)


def test_debug_level_promotes_lazy_logger_and_handler(capsys):
    set_console_log_level(logging.DEBUG)
    try:
        logger = setup_logger(f"debug-logger-{uuid.uuid4().hex}")
        logger.debug("debug detail is visible")
        assert "debug detail is visible" in capsys.readouterr().err
    finally:
        set_console_log_level(logging.ERROR)


def test_safe_log_url_removes_credentials_query_values_and_fragment():
    safe = safe_log_url(
        "https://user:password@example.com/path?token=secret&modal_id=123#fragment"
    )

    assert safe == "https://example.com/path?[query_keys=modal_id,token]"
    assert "password" not in safe
    assert "secret" not in safe
    assert "123" not in safe
