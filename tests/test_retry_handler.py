import time

import pytest

from control.retry_handler import RetryHandler


@pytest.mark.asyncio
async def test_retry_handler_succeeds_on_first_try():
    handler = RetryHandler(max_retries=3)
    call_count = 0

    async def task():
        nonlocal call_count
        call_count += 1
        return "ok"

    result = await handler.execute_with_retry(task)
    assert result == "ok"
    assert call_count == 1


@pytest.mark.asyncio
async def test_retry_handler_retries_then_succeeds():
    handler = RetryHandler(max_retries=3)
    handler.retry_delays = [0, 0, 0]
    call_count = 0

    async def task():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RuntimeError("transient error")
        return "recovered"

    result = await handler.execute_with_retry(task)
    assert result == "recovered"
    assert call_count == 3


@pytest.mark.asyncio
async def test_retry_handler_raises_after_exhaustion():
    handler = RetryHandler(max_retries=2)
    handler.retry_delays = [0, 0]

    async def task():
        raise ValueError("permanent")

    with pytest.raises(ValueError, match="permanent"):
        await handler.execute_with_retry(task)


@pytest.mark.asyncio
async def test_retry_handler_makes_max_retries_plus_one_attempts():
    # max_retries=N means N retries after the initial attempt → N+1 total
    # attempts. The previous implementation looped only N times, so the third
    # configured delay was unreachable.
    handler = RetryHandler(max_retries=3)
    handler.retry_delays = [0, 0, 0]
    call_count = 0

    async def task():
        nonlocal call_count
        call_count += 1
        if call_count < 4:
            raise RuntimeError("transient")
        return "ok"

    result = await handler.execute_with_retry(task)
    assert result == "ok"
    assert call_count == 4


@pytest.mark.asyncio
async def test_retry_handler_applies_all_configured_delays():
    # All three delays in retry_delays must be applied between failed attempts.
    handler = RetryHandler(max_retries=3)
    handler.retry_delays = [0.05, 0.1, 0.2]
    call_count = 0

    async def always_fail():
        nonlocal call_count
        call_count += 1
        raise RuntimeError("always")

    start = time.time()
    with pytest.raises(RuntimeError):
        await handler.execute_with_retry(always_fail)
    elapsed = time.time() - start

    assert call_count == 4
    assert elapsed >= 0.3, f"expected >= 0.3s of delay (sum of retry_delays), got {elapsed:.3f}s"
