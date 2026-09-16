import asyncio
import time

import pytest

from control.queue_manager import QueueManager


@pytest.mark.asyncio
async def test_process_tasks_returns_results_in_order():
    qm = QueueManager(max_workers=3)

    def make_task(value):
        async def _task():
            return value

        return _task

    tasks = [make_task(i) for i in range(5)]
    results = await qm.process_tasks(tasks)
    assert results == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_process_tasks_surfaces_exceptions_without_killing_others():
    # The previous implementation swallowed exceptions and returned None,
    # making "task succeeded with None result" indistinguishable from
    # "task threw". Failures must surface with the original exception attached.
    qm = QueueManager(max_workers=3)

    async def succeed():
        return "ok"

    async def boom():
        raise RuntimeError("kapow")

    tasks = [succeed, boom, succeed]
    results = await qm.process_tasks(tasks)

    assert results[0] == "ok"
    assert isinstance(results[1], RuntimeError)
    assert str(results[1]) == "kapow"
    assert results[2] == "ok"


@pytest.mark.asyncio
async def test_process_tasks_respects_concurrency_cap():
    qm = QueueManager(max_workers=2)
    in_flight = 0
    peak = 0

    async def slow():
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        return "done"

    await qm.process_tasks([slow] * 8)
    assert peak == 2, f"peak concurrency was {peak}, expected 2"


@pytest.mark.asyncio
async def test_download_batch_returns_results_in_order():
    qm = QueueManager(max_workers=3)

    async def echo(item):
        return {"status": "success", "item": item}

    items = ["a", "b", "c"]
    results = await qm.download_batch(echo, items)
    assert [r["item"] for r in results] == ["a", "b", "c"]
    assert all(r["status"] == "success" for r in results)


@pytest.mark.asyncio
async def test_download_batch_surfaces_exceptions_alongside_successes():
    # When one item raises, the other items must still complete and the
    # exception must be observable in the results list.
    qm = QueueManager(max_workers=3)

    async def maybe_fail(item):
        if item == "bad":
            raise ValueError(f"bad item: {item}")
        return {"status": "success", "item": item}

    results = await qm.download_batch(maybe_fail, ["a", "bad", "c"])

    assert isinstance(results[0], dict) and results[0]["status"] == "success"
    assert isinstance(results[1], ValueError)
    assert "bad item: bad" in str(results[1])
    assert isinstance(results[2], dict) and results[2]["status"] == "success"


@pytest.mark.asyncio
async def test_download_batch_respects_concurrency_cap():
    qm = QueueManager(max_workers=2)
    in_flight = 0
    peak = 0

    async def slow(_item):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        return {"status": "success"}

    start = time.time()
    await qm.download_batch(slow, list(range(8)))
    elapsed = time.time() - start

    assert peak == 2, f"peak concurrency was {peak}, expected 2"
    # 8 tasks at concurrency 2, 0.05s each = 4 batches = 0.2s minimum
    assert elapsed >= 0.18, f"elapsed {elapsed:.3f}s suggests concurrency cap not enforced"


# ---------------------------------------------------------------------------
# 运行中调整并发上限（设置页「并发数」改完即时生效）
# ---------------------------------------------------------------------------


async def _wait_until(predicate, timeout=1.0):
    """轮询到条件成立；超时即失败，避免用固定 sleep 写出不稳定的断言。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while not predicate():
        if asyncio.get_event_loop().time() > deadline:
            return False
        await asyncio.sleep(0.005)
    return True


@pytest.mark.asyncio
async def test_shrinking_max_workers_does_not_preempt_in_flight_tasks():
    """调小上限不抢占在途任务——只是不再放行新的。

    之前并发度落在构造时的 `asyncio.Semaphore(max_workers)` 上，改设置只能
    换一个新信号量：在途任务仍持有旧信号量的名额，新任务按新名额放行，
    实际并发会短暂超过两个上限之和。所以当时只能重启 sidecar。
    """
    qm = QueueManager(max_workers=4)
    release = asyncio.Event()
    running = 0
    finished = []

    async def blocked(item):
        nonlocal running
        running += 1
        await release.wait()
        running -= 1
        finished.append(item)
        return item

    batch = asyncio.ensure_future(qm.download_batch(blocked, list(range(4))))
    assert await _wait_until(lambda: running == 4), "4 个任务应当都已起跑"

    qm.set_max_workers(2)

    # 在途的 4 个既不被取消也不被挂起。
    await asyncio.sleep(0.02)
    assert running == 4
    assert not batch.done()

    release.set()
    results = await batch
    assert results == [0, 1, 2, 3]
    assert sorted(finished) == [0, 1, 2, 3]


@pytest.mark.asyncio
async def test_new_tasks_honour_the_shrunk_limit():
    qm = QueueManager(max_workers=4)
    qm.set_max_workers(2)

    in_flight = 0
    peak = 0

    async def slow(_item):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1

    await qm.download_batch(slow, list(range(8)))

    assert peak == 2, f"调小后仍跑到 {peak} 并发"


@pytest.mark.asyncio
async def test_growing_max_workers_admits_waiters_without_a_release():
    """调大上限要当场放行排队者，而不是等某个在途任务结束才生效。"""
    qm = QueueManager(max_workers=1)
    release = asyncio.Event()
    running = 0

    async def blocked(item):
        nonlocal running
        running += 1
        await release.wait()
        running -= 1
        return item

    batch = asyncio.ensure_future(qm.download_batch(blocked, list(range(4))))
    assert await _wait_until(lambda: running == 1)
    await asyncio.sleep(0.02)
    assert running == 1, "上限是 1，不该有第二个跑起来"

    qm.set_max_workers(3)

    # 没有任何任务结束，靠的就是 set_max_workers 自己唤醒排队者。
    assert await _wait_until(lambda: running == 3), "调大后应当立刻补到 3 个"
    assert running == 3

    release.set()
    assert await batch == [0, 1, 2, 3]


@pytest.mark.asyncio
async def test_max_workers_is_clamped_to_at_least_one():
    """0 会让队列永远拿不到名额；夹到 1 而不是死锁。"""
    qm = QueueManager(max_workers=0)
    assert qm.max_workers == 1

    qm.set_max_workers(-5)
    assert qm.max_workers == 1

    results = await qm.download_batch(lambda item: asyncio.sleep(0, result=item), [1, 2])
    assert results == [1, 2]


@pytest.mark.asyncio
async def test_cancelled_task_returns_its_slot():
    """取消在途任务不能泄漏名额，否则队列越跑越窄直到彻底卡死。

    这是把 Semaphore 换成计数法时最容易踩的一脚：如果释放名额需要先 await
    拿锁，被取消的任务在 `finally` 里一 await 就再次抛 CancelledError，
    减法永远执行不到。所以 `_release` 必须是同步的。
    """
    qm = QueueManager(max_workers=2)
    release = asyncio.Event()
    started = 0

    async def blocked(_item):
        nonlocal started
        started += 1
        await release.wait()

    tasks = [asyncio.ensure_future(qm.download_batch(blocked, [i])) for i in range(2)]
    assert await _wait_until(lambda: started == 2)

    for task in tasks:
        task.cancel()
    for task in tasks:
        with pytest.raises(asyncio.CancelledError):
            await task

    # 两个名额都该还回来了。
    assert qm._active == 0

    # 闸门仍然完整可用：再跑一批，峰值不超过上限。
    in_flight = 0
    peak = 0

    async def quick(_item):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1

    await qm.download_batch(quick, list(range(6)))
    assert peak == 2


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_consume_a_slot():
    """排队中被取消的任务，不能把名额带走。"""
    qm = QueueManager(max_workers=1)
    release = asyncio.Event()
    started = 0

    async def blocked(_item):
        nonlocal started
        started += 1
        await release.wait()

    holder = asyncio.ensure_future(qm.download_batch(blocked, [0]))
    assert await _wait_until(lambda: started == 1)

    waiter = asyncio.ensure_future(qm.download_batch(blocked, [1]))
    await asyncio.sleep(0.02)
    assert started == 1, "上限 1，第二个应当还在排队"

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    release.set()
    await holder
    assert qm._active == 0
