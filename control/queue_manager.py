import asyncio
from collections import deque
from contextlib import asynccontextmanager
from typing import Any, Callable, Deque, List, TypeVar

from utils.logger import setup_logger

logger = setup_logger("QueueManager")

T = TypeVar("T")

DEFAULT_MAX_WORKERS = 5


def _clamp_workers(value: Any) -> int:
    """并发上限至少为 1。

    0 会让所有任务永远拿不到名额（表现是「下载卡住不动」而不是报错），
    所以夹住而不是照单全收；认不出的值退回默认，别让一个坏配置把队列锁死。
    """
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_WORKERS
    return parsed if parsed >= 1 else 1


class QueueManager:
    """并发闸门：同时最多放行 ``max_workers`` 个任务。

    **为什么不用 ``asyncio.Semaphore``**：名额在构造时就分配好了，上限没法在
    运行中改。要改只能换一个新信号量，而在途任务仍持有旧信号量的名额、新任务
    按新名额放行，实际并发会短暂冲到两个上限之和。设置页的「并发数」因此一直
    只能重启 sidecar 才生效。

    换成「活跃计数 + 等待队列」后：
    - 调小上限不抢占在途任务，只是不再放行新的，直到实际并发降到新上限以下；
    - 调大上限当场唤醒排队者，不必等某个在途任务结束；
    - 释放名额是**同步**的（``_release``），所以放在 ``finally`` 里即使任务
      正在被取消也一定执行得到。这点是刻意的：早先用 ``asyncio.Condition``
      的写法需要在 ``finally`` 里 ``await`` 拿锁，而被取消的任务一 await 就
      再次抛 ``CancelledError``，名额会永久泄漏，队列越跑越窄直到卡死。
    """

    def __init__(self, max_workers: int = DEFAULT_MAX_WORKERS):
        self.max_workers = _clamp_workers(max_workers)
        self._active = 0
        self._waiters: Deque[asyncio.Future] = deque()

    # -- 并发上限 ---------------------------------------------------------

    def set_max_workers(self, value: int) -> None:
        """运行中调整上限（设置页保存 / 恢复默认时回写）。

        同步方法：调用方不需要在事件循环里，也不会因为等锁被取消。
        """
        previous = self.max_workers
        self.max_workers = _clamp_workers(value)
        if self.max_workers != previous:
            logger.info(
                "Concurrency limit changed: %d -> %d (active=%d, waiting=%d)",
                previous,
                self.max_workers,
                self._active,
                len(self._waiters),
            )
        self._wake_waiters()

    # -- 名额 -------------------------------------------------------------

    def _wake_waiters(self) -> None:
        """把能放行的排队者依次唤醒。

        名额在**唤醒时**就记到 ``_active`` 上，而不是等被唤醒的协程自己加——
        否则同一轮里连续唤醒多个，每个都还没来得及记账，就会一起越过上限。
        """
        while self._waiters and self._active < self.max_workers:
            waiter = self._waiters.popleft()
            if waiter.done():
                # 已被取消的排队者不占名额，跳过继续找下一个。
                continue
            waiter.set_result(True)
            self._active += 1

    async def _acquire(self) -> None:
        # 有空位且没人排队时直接进——`not self._waiters` 保证先来后到，
        # 不让新任务插到已经在等的任务前面。
        if self._active < self.max_workers and not self._waiters:
            self._active += 1
            return

        waiter = asyncio.get_running_loop().create_future()
        self._waiters.append(waiter)
        try:
            await waiter
        except asyncio.CancelledError:
            if waiter.done() and not waiter.cancelled():
                # 名额已经发给我了，但我在恢复执行前被取消——还回去。
                self._active -= 1
                self._wake_waiters()
            else:
                try:
                    self._waiters.remove(waiter)
                except ValueError:
                    pass
            raise

    def _release(self) -> None:
        self._active -= 1
        self._wake_waiters()

    @asynccontextmanager
    async def _slot(self):
        await self._acquire()
        try:
            yield
        finally:
            self._release()

    # -- 对外接口 ---------------------------------------------------------

    async def process_tasks(self, tasks: List[Callable], *args, **kwargs) -> List[Any]:
        # Failures surface as exception instances in the result list (via
        # return_exceptions=True). Callers can filter with isinstance(r, BaseException).
        async def _task_wrapper(task):
            async with self._slot():
                try:
                    return await task(*args, **kwargs)
                except Exception:
                    logger.exception("Task failed")
                    raise

        return await asyncio.gather(
            *[_task_wrapper(task) for task in tasks], return_exceptions=True
        )

    async def download_batch(self, download_func: Callable, items: List[Any]) -> List[Any]:
        async def _download_wrapper(item):
            async with self._slot():
                try:
                    return await download_func(item)
                except Exception:
                    logger.exception("Download failed for item: %r", item)
                    raise

        return await asyncio.gather(
            *[_download_wrapper(item) for item in items], return_exceptions=True
        )
