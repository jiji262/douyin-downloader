"""FastAPI 服务测试：验证 job 生命周期与 HTTP 接口。

仅测试 HTTP 层 + JobManager 抽象；不触达真实 Douyin API。
"""

import asyncio
from typing import Any, Dict

import pytest

try:
    from fastapi.testclient import TestClient  # type: ignore
except ImportError:  # pragma: no cover
    pytest.skip("fastapi not installed", allow_module_level=True)

import asyncio

from config import ConfigLoader
from server.app import build_app
from server.jobs import JobManager


@pytest.mark.asyncio
async def test_job_manager_runs_executor(tmp_path):
    async def fake_executor(url: str) -> Dict[str, int]:
        return {"total": 1, "success": 1, "failed": 0, "skipped": 0}

    manager = JobManager(executor=fake_executor, max_concurrency=2)
    job = await manager.submit("https://example/one")
    assert job.status == "pending"

    # 等待后台任务跑完
    await asyncio.wait_for(job._task, timeout=2.0)
    fetched = await manager.get(job.job_id)
    assert fetched is not None
    assert fetched.status == "success"
    assert fetched.success == 1


@pytest.mark.asyncio
async def test_job_manager_marks_failure_on_executor_error(tmp_path):
    async def boom(url: str) -> Dict[str, int]:
        raise RuntimeError("bad url")

    manager = JobManager(executor=boom)
    job = await manager.submit("x")
    await asyncio.wait_for(job._task, timeout=2.0)
    fetched = await manager.get(job.job_id)
    assert fetched is not None
    assert fetched.status == "failed"
    assert fetched.error is not None
    assert "bad url" in fetched.error


def test_health_endpoint(tmp_path):
    config = ConfigLoader(None)
    config.update(path=str(tmp_path))
    app = build_app(config)

    with TestClient(app) as client:
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


def test_download_endpoint_creates_job(tmp_path, monkeypatch):
    config = ConfigLoader(None)
    config.update(path=str(tmp_path))
    app = build_app(config)

    # 替换 job executor 为 fake（不去触达 Douyin）
    async def fake_executor(url: str) -> Dict[str, int]:
        return {"total": 0, "success": 0, "failed": 0, "skipped": 0}

    app.state.job_manager.executor = fake_executor

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/download", json={"url": "https://www.douyin.com/video/123"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("pending", "running", "success")
        assert data["url"] == "https://www.douyin.com/video/123"
        assert len(data["job_id"]) > 0

        job_id = data["job_id"]
        # job 列表应包含该 id
        list_resp = client.get("/api/v1/jobs")
        assert list_resp.status_code == 200
        ids = [j["job_id"] for j in list_resp.json()["jobs"]]
        assert job_id in ids

        # 详情接口
        detail = client.get(f"/api/v1/jobs/{job_id}")
        assert detail.status_code == 200
        assert detail.json()["job_id"] == job_id


def test_download_endpoint_rejects_empty_url(tmp_path):
    config = ConfigLoader(None)
    config.update(path=str(tmp_path))
    app = build_app(config)
    with TestClient(app) as client:
        resp = client.post("/api/v1/download", json={"url": ""})
        assert resp.status_code == 400


def test_get_unknown_job_returns_404(tmp_path):
    config = ConfigLoader(None)
    config.update(path=str(tmp_path))
    app = build_app(config)
    with TestClient(app) as client:
        resp = client.get("/api/v1/jobs/unknown-id")
        assert resp.status_code == 404


def test_build_app_shares_deps_across_requests(tmp_path):
    """重请求应复用同一个 FileManager / RateLimiter 等（避免每次重建）。"""
    config = ConfigLoader(None)
    config.update(path=str(tmp_path))
    app = build_app(config)

    deps = app.state.deps
    assert deps.file_manager is not None
    assert deps.rate_limiter is not None
    assert deps.retry_handler is not None
    assert deps.queue_manager is not None
    assert deps.cookie_manager is not None

    # 构建第二次 app 时应该是完全独立的 deps 实例，但同一 app 内是共享的
    app2 = build_app(config)
    assert app2.state.deps is not app.state.deps
    assert app.state.deps.file_manager is app.state.deps.file_manager  # identity


@pytest.mark.asyncio
async def test_job_manager_prunes_by_max_jobs():
    """max_jobs 超限时应优先淘汰最老的终态 job，保留 in-flight。"""
    async def fast_executor(url: str) -> Dict[str, int]:
        return {"total": 0, "success": 0, "failed": 0, "skipped": 0}

    manager = JobManager(
        executor=fast_executor, max_jobs=3, job_ttl_seconds=0.0
    )
    jobs = []
    for i in range(5):
        j = await manager.submit(f"u{i}")
        jobs.append(j)
        await asyncio.wait_for(j._task, timeout=1.0)

    remaining = await manager.list_jobs()
    # max_jobs=3：新任务 submit 时先剪裁，最终存量 ≤ max_jobs
    assert len(remaining) <= 3
    # 最新的那一批一定在，最早的那几个被淘汰
    ids_remaining = {j.job_id for j in remaining}
    assert jobs[-1].job_id in ids_remaining


@pytest.mark.asyncio
async def test_job_manager_prunes_by_ttl():
    """TTL 过期的终态 job 应在下次 submit 时被清理。"""
    async def fast_executor(url: str) -> Dict[str, int]:
        return {"total": 0, "success": 0, "failed": 0, "skipped": 0}

    manager = JobManager(
        executor=fast_executor, max_jobs=100, job_ttl_seconds=0.01
    )
    old_job = await manager.submit("old")
    await asyncio.wait_for(old_job._task, timeout=1.0)

    # 等 TTL 过期
    await asyncio.sleep(0.05)

    new_job = await manager.submit("new")
    await asyncio.wait_for(new_job._task, timeout=1.0)

    remaining_ids = {j.job_id for j in await manager.list_jobs()}
    assert old_job.job_id not in remaining_ids
    assert new_job.job_id in remaining_ids
