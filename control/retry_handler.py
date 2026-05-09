import asyncio
from typing import Callable, Any, TypeVar
from utils.logger import setup_logger

logger = setup_logger('RetryHandler')

T = TypeVar('T')


class RetryHandler:
    def __init__(self, max_retries: int = 3):
        # max_retries = number of retries AFTER the initial attempt;
        # total attempts = max_retries + 1.
        self.max_retries = max_retries
        self.retry_delays = [1, 2, 5]

    async def execute_with_retry(self, func: Callable[..., T], *args, **kwargs) -> T:
        last_error = None
        total_attempts = self.max_retries + 1

        for attempt in range(total_attempts):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    delay = self.retry_delays[min(attempt, len(self.retry_delays) - 1)]
                    logger.warning("Attempt %d failed: %s, retrying in %ds...", attempt + 1, e, delay)
                    await asyncio.sleep(delay)

        logger.error("All %d attempts failed: %s", total_attempts, last_error)
        raise last_error
