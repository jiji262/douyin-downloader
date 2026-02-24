import asyncio
import time

import pytest
from tools.cookie_fetcher import (
    extract_ms_token_from_text,
    goto_with_fallback,
    try_extract_ms_token,
    wait_for_login_confirmation,
)


class FakePage:
    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = []

    async def goto(self, url, wait_until=None, timeout=None):
        self.calls.append(
            {
                "url": url,
                "wait_until": wait_until,
                "timeout": timeout,
            }
        )
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class SlowPage:
    def __init__(self):
        self.calls = []
        self.cancelled = False

    async def goto(self, url, wait_until=None, timeout=None):
        self.calls.append(
            {
                "url": url,
                "wait_until": wait_until,
                "timeout": timeout,
            }
        )
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def evaluate(self, _):
        return ""


def test_goto_with_fallback_when_networkidle_timeout():
    page = FakePage([TimeoutError("network idle timeout"), object()])
    wait_until = asyncio.run(goto_with_fallback(page, "https://www.douyin.com/"))

    assert wait_until == "domcontentloaded"
    assert page.calls[0]["wait_until"] == "networkidle"
    assert page.calls[1]["wait_until"] == "domcontentloaded"


def test_goto_with_fallback_raises_non_timeout_errors():
    page = FakePage([RuntimeError("unexpected error")])

    with pytest.raises(RuntimeError, match="unexpected error"):
        asyncio.run(goto_with_fallback(page, "https://www.douyin.com/"))

    assert len(page.calls) == 1


def test_goto_with_fallback_handles_target_closed():
    class TargetClosedError(Exception):
        pass

    page = FakePage(
        [TargetClosedError("Target page, context or browser has been closed")]
    )
    wait_until = asyncio.run(goto_with_fallback(page, "https://www.douyin.com/"))

    assert wait_until == "target_closed"
    assert len(page.calls) == 1


def test_goto_with_fallback_returns_timeout_when_fallback_also_times_out():
    page = FakePage([TimeoutError("primary timeout"), TimeoutError("fallback timeout")])
    wait_until = asyncio.run(goto_with_fallback(page, "https://www.douyin.com/"))

    assert wait_until == "timeout"
    assert len(page.calls) == 2


def test_wait_for_login_confirmation_returns_without_waiting_navigation():
    page = SlowPage()
    started = time.time()

    asyncio.run(
        wait_for_login_confirmation(
            page,
            "https://www.douyin.com/",
            input_func=lambda: "",
        )
    )
    elapsed = time.time() - started

    assert elapsed < 1
    assert len(page.calls) == 1
    assert page.cancelled is True


def test_wait_for_login_confirmation_handles_completed_navigation():
    page = FakePage([object()])

    asyncio.run(
        wait_for_login_confirmation(
            page,
            "https://www.douyin.com/",
            input_func=lambda: "",
        )
    )

    assert len(page.calls) == 1


def test_try_extract_ms_token_from_observed_headers():
    page = SlowPage()

    token = asyncio.run(
        try_extract_ms_token(
            page,
            {"ttwid": "x"},
            ["ttwid=abc; msToken=token-from-header"],
            [],
        )
    )

    assert token == "token-from-header"


def test_extract_ms_token_from_text_supports_json_and_query_formats():
    assert (
        extract_ms_token_from_text(
            "https://www.douyin.com/?foo=1&msToken=query-token&bar=2"
        )
        == "query-token"
    )
    assert extract_ms_token_from_text('{"msToken":"json-token","x":1}') == "json-token"
