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
