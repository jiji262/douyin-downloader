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


@pytest.mark.asyncio
async def test_shrinking_max_retries_mid_flight_keeps_the_backoff(monkeypatch):
    """运行途中调小 max_retries，不能让剩下的尝试变成零间隔连击。

    ``deps.retry_handler`` 是全进程共享的单例，设置页保存 / 恢复默认会当场
    回写它的 ``max_retries``。而循环外只算一次 ``total_attempts``、循环内每轮
    重读 ``self.max_retries`` 决定要不要 sleep——值被调小后，剩余的尝试全部
    跳过退避，在同一毫秒内把请求打出去，正好是最容易触发风控的形状。
    """
    handler = RetryHandler(max_retries=6)
    handler.retry_delays = [0, 0, 0]

    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr("control.retry_handler.asyncio.sleep", fake_sleep)

    attempts = 0

    async def task():
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            # 模拟下载途中用户把「失败重试次数」改小（或点了恢复默认）。
            handler.max_retries = 1
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await handler.execute_with_retry(task)

    # 一次执行内的次数必须自洽：尝试 N 次就该退避 N-1 次。
    assert len(sleeps) == attempts - 1
