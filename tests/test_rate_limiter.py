import asyncio
import time

import pytest

from control.rate_limiter import RateLimiter


@pytest.mark.asyncio
async def test_rate_limiter_enforces_interval():
    limiter = RateLimiter(max_per_second=10)
    start = time.time()
    for _ in range(5):
        await limiter.acquire()
    elapsed = time.time() - start
    assert elapsed >= 0.4


@pytest.mark.asyncio
async def test_rate_limiter_invalid_value_uses_default():
    limiter = RateLimiter(max_per_second=0)
    assert limiter.max_per_second == 2

    limiter_neg = RateLimiter(max_per_second=-5)
    assert limiter_neg.max_per_second == 2


@pytest.mark.asyncio
async def test_rate_limiter_caps_concurrent_acquire_rate():
    # 10 concurrent acquires at 2/s must take at least 4.5s.
    limiter = RateLimiter(max_per_second=2)
    start = time.time()
    await asyncio.gather(*[limiter.acquire() for _ in range(10)])
    elapsed = time.time() - start
    assert elapsed >= 4.5, f"10 concurrent acquires finished in {elapsed:.2f}s, expected >= 4.5s"


@pytest.mark.asyncio
async def test_rate_limiter_spaces_consecutive_fires(monkeypatch):
    # Each fire (acquire() returning) must be at least min_interval after the
    # previous fire. The buggy implementation released the lock before its jitter
    # sleep — with alternating max/min jitter, consecutive fires would cluster
    # within the same millisecond, busting the per-second rate cap.
    import control.rate_limiter as rl_mod

    jitters = iter([0.5, 0.0] * 10)
    monkeypatch.setattr(rl_mod.random, "uniform", lambda *_a, **_kw: next(jitters))

    limiter = rl_mod.RateLimiter(max_per_second=2)
    fire_times: list[float] = []

    async def acquire_and_record():
        await limiter.acquire()
        fire_times.append(time.time())

    await asyncio.gather(*[acquire_and_record() for _ in range(6)])

    fire_times.sort()
    intervals = [fire_times[i + 1] - fire_times[i] for i in range(len(fire_times) - 1)]
    min_interval = 1.0 / 2
    slack = 0.05
    too_close = [round(i, 3) for i in intervals if i < min_interval - slack]
    assert not too_close, (
        f"fire intervals violate min_interval={min_interval}s: "
        f"too_close={too_close} all={[round(i, 3) for i in intervals]}"
    )
