import asyncio
from typing import List, Callable, Any, TypeVar
from utils.logger import setup_logger

logger = setup_logger('QueueManager')

T = TypeVar('T')


class QueueManager:
    def __init__(self, max_workers: int = 5):
        self.max_workers = max_workers
        self.semaphore = asyncio.Semaphore(max_workers)

    async def process_tasks(self, tasks: List[Callable], *args, **kwargs) -> List[Any]:
        async def _task_wrapper(task):
            async with self.semaphore:
                try:
                    return await task(*args, **kwargs)
                except Exception as e:
                    logger.error(f"Task failed: {e}")
                    return None

        results = await asyncio.gather(*[_task_wrapper(task) for task in tasks], return_exceptions=True)
        return results

    async def download_batch(self, download_func: Callable, items: List[Any]) -> List[Any]:
        async def _download_wrapper(item):
            async with self.semaphore:
                try:
                    return await download_func(item)
                except Exception as e:
                    logger.error(f"Download failed for item: {e}")
                    return {'status': 'error', 'error': str(e), 'item': item}

        results = await asyncio.gather(*[_download_wrapper(item) for item in items], return_exceptions=False)
        return results
