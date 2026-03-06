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
