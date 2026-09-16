import asyncio
from typing import Callable, TypeVar

from utils.logger import setup_logger

logger = setup_logger("RetryHandler")

T = TypeVar("T")


class RetryHandler:
    def __init__(self, max_retries: int = 3):
        # max_retries = number of retries AFTER the initial attempt;
        # total attempts = max_retries + 1.
        self.max_retries = max_retries
        self.retry_delays = [1, 2, 5]

    async def execute_with_retry(self, func: Callable[..., T], *args, **kwargs) -> T:
        last_error = None
        # 进循环前定死这次执行的次数。desktop 版把这个对象当全进程单例用，
        # 设置页保存 / 恢复默认会当场改写 `max_retries`；若循环内继续重读它，
        # 值被调小后 `attempt < self.max_retries` 立刻转假，剩下的尝试全部跳过
        # 退避、在同一毫秒里连发——正好是最容易撞上风控的形状。一次执行内
        # 自洽即可，新值从下一次调用开始生效。
        max_retries = self.max_retries
        total_attempts = max_retries + 1

        for attempt in range(total_attempts):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    delay = self.retry_delays[min(attempt, len(self.retry_delays) - 1)]
                    logger.warning(
                        "Attempt %d failed: %s, retrying in %ds...", attempt + 1, e, delay
                    )
                    await asyncio.sleep(delay)

        logger.error("All %d attempts failed: %s", total_attempts, last_error)
        raise last_error
